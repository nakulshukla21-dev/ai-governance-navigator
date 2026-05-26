"""Tests for AI Governance Navigator pure helper functions."""

from __future__ import annotations

import pytest

from agent import GovernanceBrief, _extract_json, _parse_governance_brief
from app import _build_question


def test_extract_json_valid() -> None:
    payload = '{"question": "test", "risk_classification": "High"}'
    result = _extract_json(payload)
    assert result == {"question": "test", "risk_classification": "High"}


def test_extract_json_with_fences() -> None:
    payload = """Here is the brief:
```json
{
  "question": "What is high-risk AI?",
  "risk_classification": "High"
}
```
"""
    result = _extract_json(payload)
    assert result["question"] == "What is high-risk AI?"
    assert result["risk_classification"] == "High"


def test_extract_json_invalid() -> None:
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        _extract_json("This response has no JSON object.")


def test_parse_governance_brief_full() -> None:
    payload = {
        "question": "What are EU high-risk requirements?",
        "jurisdictions_consulted": ["EU AI Act", "MAS"],
        "key_findings": {
            "EU AI Act": ["Requires conformity assessment.", "Human oversight is mandatory."],
            "MAS": ["Model risk management expected."],
        },
        "convergences": ["Both require governance controls."],
        "divergences": ["EU uses a binding risk tier system."],
        "risk_classification": "High",
        "sources": ["EU AI Act Article 9", "MAS FEAT principles"],
    }

    brief = _parse_governance_brief(payload, fallback_question="fallback question")

    assert brief == GovernanceBrief(
        question="What are EU high-risk requirements?",
        jurisdictions_consulted=["EU AI Act", "MAS"],
        key_findings={
            "EU AI Act": ["Requires conformity assessment.", "Human oversight is mandatory."],
            "MAS": ["Model risk management expected."],
        },
        convergences=["Both require governance controls."],
        divergences=["EU uses a binding risk tier system."],
        risk_classification="High",
        sources=["EU AI Act Article 9", "MAS FEAT principles"],
    )


def test_parse_governance_brief_invalid_risk() -> None:
    payload = {
        "question": "Sample question",
        "risk_classification": "Critical",
    }

    brief = _parse_governance_brief(payload, fallback_question="fallback question")

    assert brief.risk_classification == "Medium"


def test_parse_governance_brief_empty() -> None:
    brief = _parse_governance_brief({}, fallback_question="fallback question")

    assert brief.question == "fallback question"
    assert brief.jurisdictions_consulted == []
    assert brief.key_findings == {}
    assert brief.convergences == []
    assert brief.divergences == []
    assert brief.sources == []
    assert brief.risk_classification == "Medium"


def test_build_question_no_artefacts() -> None:
    question = "What are the EU requirements for biometric systems?"
    result = _build_question(question, [])
    assert result == question


def test_build_question_with_artefacts() -> None:
    question = "Does our model card meet regulatory expectations?"
    artefacts = [
        ("model_card.pdf", "Model purpose: credit scoring."),
        ("risk_assessment.pdf", "Residual risk rated medium."),
    ]

    result = _build_question(question, artefacts)

    assert result.startswith("Additional context from uploaded internal artefacts:")
    assert "--- Uploaded artefact: model_card.pdf ---" in result
    assert "Model purpose: credit scoring." in result
    assert "--- Uploaded artefact: risk_assessment.pdf ---" in result
    assert "Residual risk rated medium." in result
    assert result.endswith(f"Governance question:\n{question}")
