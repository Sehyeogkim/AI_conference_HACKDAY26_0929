"""Crusoe adapter contract; no live network or credentials required."""

import json

import httpx
import pytest

from agriphilo.crusoe import CHAT_COMPLETIONS_URL, explain_qa


def test_missing_key_preserves_rule_verdict(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CRUSOE_API_KEY", raising=False)
    monkeypatch.delenv("CURSOE_API_KEY", raising=False)
    result = explain_qa({"harvested": False}, "fail", ["Tomato was not placed in the bin"])
    assert result.source == "rules"
    assert result.text == "QA fail: Tomato was not placed in the bin."


def test_crusoe_explains_without_owning_verdict():
    seen = {}

    def respond(request):
        seen["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "The tomato reached the bin with a complete grasp trace."}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = explain_qa(
            {"harvested": True, "grasp_events": 1},
            "pass",
            ["All required stages recorded"],
            api_key="test-key",
            client=client,
        )

    assert result.source == "crusoe"
    assert result.text.startswith("The tomato")
    assert result.model
    assert seen["request"].url == CHAT_COMPLETIONS_URL
    assert seen["request"].headers["authorization"] == "Bearer test-key"
    body = json.loads(seen["request"].content)
    assert '"verdict": "pass"' in body["messages"][1]["content"]


def test_existing_key_spelling_is_supported(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CRUSOE_API_KEY", raising=False)
    monkeypatch.setenv("CURSOE_API_KEY", "alias-key")
    seen = {}

    def respond(request):
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "The required steps were recorded."}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = explain_qa({}, "pass", client=client)
    assert result.source == "crusoe"
    assert seen["authorization"] == "Bearer alias-key"


def test_remote_failure_falls_back():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        result = explain_qa({}, "fail", ["Incomplete trace"], api_key="test-key", client=client)
    assert result.source == "rules"
    assert "Incomplete trace" in result.text


def test_invalid_verdict_is_rejected():
    with pytest.raises(ValueError):
        explain_qa({}, "maybe")
