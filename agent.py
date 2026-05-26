"""
AI Governance Navigator agent.

Connects to the local MCP regulatory server over stdio, uses Claude with tool
use to query relevant frameworks, and returns a structured governance brief.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal

import anyio
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment
from mcp.types import CallToolResult, TextContent, Tool

MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 20
PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_SCRIPT = PROJECT_ROOT / "server.py"

RiskLevel = Literal["High", "Medium", "Low"]

SYSTEM_PROMPT = """You are an AI Governance Navigator. You help organizations understand
how major regulatory frameworks apply to their AI governance questions.

You have access to MCP tools that retrieve authoritative content from these frameworks:
- EU AI Act (European Union)
- NIST AI Risk Management Framework (United States)
- MAS AI guidelines (Singapore)
- UK AI Policy / regulation (United Kingdom)
- FATF guidance on AI and financial crime

Workflow:
1. Read the user's governance question carefully.
2. Decide which regulatory tools to call. Call all frameworks that are plausibly
   relevant; skip only those clearly unrelated.
3. Use tool results as your primary evidence. Do not invent citations.
4. When you have enough material, stop calling tools and wait for the synthesis step.

Be precise, practical, and oriented toward compliance and risk management teams."""


SYNTHESIS_PROMPT = """Based on the research above, produce a final governance brief as a
single JSON object with exactly these keys:

- question (string): the original governance question
- jurisdictions_consulted (array of strings): jurisdiction or framework names consulted
- key_findings (object): map each jurisdiction/framework name to an array of concise findings
- convergences (array of strings): requirements or themes aligned across frameworks
- divergences (array of strings): meaningful differences or conflicts across frameworks
- risk_classification (string): exactly one of "High", "Medium", or "Low" for the scenario
- sources (array of strings): specific regulatory references cited from the tool results

Return only valid JSON. No markdown fences, commentary, or trailing text."""


@dataclass
class GovernanceBrief:
    question: str
    jurisdictions_consulted: list[str] = field(default_factory=list)
    key_findings: dict[str, list[str]] = field(default_factory=dict)
    convergences: list[str] = field(default_factory=list)
    divergences: list[str] = field(default_factory=list)
    risk_classification: RiskLevel = "Medium"
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _server_env() -> dict[str, str]:
    load_dotenv()
    env = get_default_environment()
    for key, value in os.environ.items():
        if value is not None:
            env[key] = value
    return env


def _mcp_tool_to_anthropic(tool: Tool) -> dict[str, Any]:
    input_schema = dict(tool.inputSchema or {})
    if "type" not in input_schema:
        input_schema["type"] = "object"
    if "properties" not in input_schema:
        input_schema["properties"] = {}

    return {
        "name": tool.name,
        "description": tool.description or f"Regulatory research tool: {tool.name}",
        "input_schema": input_schema,
    }


def _tool_result_to_text(result: CallToolResult) -> str:
    parts: list[str] = []

    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", str(block)))
        else:
            parts.append(str(block))

    if result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent, indent=2))

    text = "\n".join(part for part in parts if part).strip()
    if not text:
        text = "Tool returned no content."

    if result.isError:
        return f"Tool error: {text}"
    return text


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model response did not contain a JSON object.")

    return json.loads(stripped[start : end + 1])


def _parse_governance_brief(payload: dict[str, Any], fallback_question: str) -> GovernanceBrief:
    risk = payload.get("risk_classification", "Medium")
    if risk not in {"High", "Medium", "Low"}:
        risk = "Medium"

    key_findings_raw = payload.get("key_findings") or {}
    key_findings: dict[str, list[str]] = {}
    if isinstance(key_findings_raw, dict):
        for jurisdiction, findings in key_findings_raw.items():
            if isinstance(findings, list):
                key_findings[str(jurisdiction)] = [str(item) for item in findings]
            elif findings is not None:
                key_findings[str(jurisdiction)] = [str(findings)]

    return GovernanceBrief(
        question=str(payload.get("question") or fallback_question),
        jurisdictions_consulted=[
            str(item) for item in (payload.get("jurisdictions_consulted") or [])
        ],
        key_findings=key_findings,
        convergences=[str(item) for item in (payload.get("convergences") or [])],
        divergences=[str(item) for item in (payload.get("divergences") or [])],
        risk_classification=risk,
        sources=[str(item) for item in (payload.get("sources") or [])],
    )


async def _call_mcp_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> str:
    result = await session.call_tool(name, arguments or None)
    return _tool_result_to_text(result)


async def _research_with_tools(
    client: AsyncAnthropic,
    session: ClientSession,
    question: str,
    anthropic_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            tools=anthropic_tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            return messages

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            messages.append({"role": "assistant", "content": response.content})
            return messages

        messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool_input = dict(tool_use.input) if tool_use.input else {}
            result_text = await _call_mcp_tool(session, tool_use.name, tool_input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result_text,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(
        f"Exceeded maximum tool iterations ({MAX_TOOL_ITERATIONS}). "
        "Try a narrower governance question."
    )


async def _synthesize_brief(
    client: AsyncAnthropic,
    messages: list[dict[str, Any]],
    question: str,
) -> GovernanceBrief:
    synthesis_messages = [
        *messages,
        {"role": "user", "content": SYNTHESIS_PROMPT},
    ]

    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=synthesis_messages,
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no synthesis text.")

    payload = _extract_json("\n".join(text_blocks))
    return _parse_governance_brief(payload, question)


async def analyze_governance_question(
    question: str,
    *,
    server_script: Path | None = None,
) -> GovernanceBrief:
    if not question.strip():
        raise ValueError("Governance question must not be empty.")

    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")

    script = server_script or SERVER_SCRIPT
    if not script.exists():
        raise FileNotFoundError(f"MCP server not found at {script}")

    client = AsyncAnthropic(api_key=api_key)
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(script)],
        env=_server_env(),
        cwd=str(script.parent),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            if not tools_result.tools:
                raise RuntimeError("MCP server exposed no tools.")

            anthropic_tools = [_mcp_tool_to_anthropic(tool) for tool in tools_result.tools]
            messages = await _research_with_tools(
                client,
                session,
                question.strip(),
                anthropic_tools,
            )
            return await _synthesize_brief(client, messages, question.strip())


def run(question: str, *, server_script: Path | None = None) -> GovernanceBrief:
    return anyio.run(
        partial(analyze_governance_question, server_script=server_script),
        question,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze an AI governance question across regulatory frameworks.",
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="Governance question to analyze.",
    )
    parser.add_argument(
        "--question",
        "-q",
        dest="question_flag",
        help="Governance question (alternative to positional argument).",
    )
    parser.add_argument(
        "--server",
        type=Path,
        default=SERVER_SCRIPT,
        help=f"Path to MCP server script (default: {SERVER_SCRIPT.name}).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    question = args.question_flag or args.question
    if not question:
        parser.error("Provide a governance question as a positional argument or via --question.")

    brief = run(question, server_script=args.server)
    indent = 2 if args.pretty else None
    print(json.dumps(brief.to_dict(), indent=indent))


if __name__ == "__main__":
    main()
