"""
MCP server for the AI Governance Navigator.

Exposes regulatory research tools that fetch and return cleaned text from
authoritative AI governance sources.
"""

from __future__ import annotations

import re
from typing import Final

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "AI Governance Navigator",
    instructions=(
        "Use these tools to retrieve authoritative regulatory text on AI governance. "
        "Pass a concise topic describing the compliance question you need to research."
    ),
)

MAX_CHARS: Final[int] = 8000
REQUEST_TIMEOUT: Final[int] = 30
USER_AGENT: Final[str] = "AI-Governance-Navigator/1.0"

STRIP_TAGS: Final[tuple[str, ...]] = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "noscript",
    "iframe",
)

EU_AI_ACT_URL: Final[str] = "https://artificialintelligenceact.eu/the-act/"
NIST_AI_RMF_URL: Final[str] = "https://airc.nist.gov/Docs/1"
MAS_AI_GUIDANCE_URL: Final[str] = (
    "https://www.mas.gov.sg/regulation/explainers/ai-in-financial-services"
)
UK_AI_POLICY_URL: Final[str] = (
    "https://www.gov.uk/government/publications/"
    "ai-regulation-a-pro-innovation-approach/white-paper"
)
FATF_AI_GUIDANCE_URL: Final[str] = (
    "https://www.fatf-gafi.org/en/publications/Digitaltransformation/"
    "Guidance-AI-in-financial-crime.html"
)


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _topic_keywords(topic: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", topic.lower())
    return [word for word in words if len(word) > 2]


def _select_relevant_text(full_text: str, topic: str) -> str:
    if not full_text:
        return ""

    keywords = _topic_keywords(topic)
    if not keywords:
        return full_text[:MAX_CHARS]

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", full_text) if part.strip()]
    if not paragraphs:
        return full_text[:MAX_CHARS]

    scored: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        score = sum(lowered.count(keyword) for keyword in keywords)
        if score:
            scored.append((score, paragraph))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = "\n\n".join(paragraph for _, paragraph in scored)
        return selected[:MAX_CHARS]

    return full_text[:MAX_CHARS]


def _fetch_and_clean(url: str, topic: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Error fetching {url}: {exc}"

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        text = soup.get_text(separator="\n")
        cleaned = _normalize_whitespace(text)
        if not cleaned:
            return f"Error processing content from {url}: page contained no readable text."

        excerpt = _select_relevant_text(cleaned, topic)
        header = f"Source: {url}\nTopic: {topic.strip() or 'general'}\n\n"
        content = header + excerpt
        return content[:MAX_CHARS]
    except Exception as exc:
        return f"Error processing content from {url}: {exc}"


@mcp.tool()
def get_eu_ai_act(topic: str) -> str:
    """Retrieve EU AI Act requirements and obligations.

    Use when the question involves European Union AI regulation, risk tiers,
    conformity assessment, prohibited practices, or provider/deployer duties
    under the EU AI Act.
    """
    return _fetch_and_clean(EU_AI_ACT_URL, topic)


@mcp.tool()
def get_nist_ai_rmf(topic: str) -> str:
    """Retrieve NIST AI Risk Management Framework guidance.

    Use when the question involves U.S. AI risk management, the NIST AI RMF
    functions (Govern, Map, Measure, Manage), trustworthiness, or voluntary
    enterprise AI governance practices.
    """
    return _fetch_and_clean(NIST_AI_RMF_URL, topic)


@mcp.tool()
def get_mas_ai_guidance(topic: str) -> str:
    """Retrieve MAS guidance on AI in financial services.

    Use when the question involves Singapore financial-sector AI regulation,
    model risk, fairness, explainability, or MAS expectations for regulated
    institutions using AI.
    """
    return _fetch_and_clean(MAS_AI_GUIDANCE_URL, topic)


@mcp.tool()
def get_uk_ai_policy(topic: str) -> str:
    """Retrieve UK pro-innovation AI regulation policy text.

    Use when the question involves United Kingdom AI policy, sector regulators,
    cross-sector principles, or the UK's approach to AI oversight and innovation.
    """
    return _fetch_and_clean(UK_AI_POLICY_URL, topic)


@mcp.tool()
def get_fatf_ai_guidance(topic: str) -> str:
    """Retrieve FATF guidance on AI and financial crime.

    Use when the question involves AI-enabled money laundering, terrorist
    financing, sanctions evasion, AML/CFT controls, or financial-crime risks
    linked to AI systems.
    """
    return _fetch_and_clean(FATF_AI_GUIDANCE_URL, topic)


if __name__ == "__main__":
    mcp.run(transport="stdio")
