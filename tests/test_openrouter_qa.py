"""OpenRouter QA cannot bypass deterministic WeFarm approval checks."""

import json
import gzip
from unittest.mock import Mock, patch

from agriphilo.openrouter_qa import QA_VERSION, evaluate_episode
from agriphilo.credits import Ledger
from agriphilo.wefarm_marketplace import WeFarmMarketplace


def evidence(**gates):
    return {
        "task": "tomato-path-harvest", "qa_version": QA_VERSION,
        "hard_gates": {"data_integrity": True, "reproducibility": True,
                       "task_completion": True, "uniqueness": True, **gates},
        "measurements": {"ripe_harvested": 1, "dropped": 0},
        "quality_signals": {"dropped": 0},
        "unavailable_measurements": ["idle_ratio"],
    }


def fake_client(decision="accept", **overrides):
    answer = {"decision": decision, "reason": "Replay confirms one ripe tomato was harvested.",
              "evidence_keys": ["hard_gates.task_completion", "hard_gates.reproducibility",
                                "measurements.ripe_harvested"],
              "model_id": "test/model", "qa_version": QA_VERSION, **overrides}
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(answer)}}]}
    client = Mock()
    client.post.return_value = response
    return client


def test_failed_and_missing_hard_gates_never_call_model():
    client = fake_client()
    assert evaluate_episode(evidence(data_integrity=False), api_key="test", model="test/model", client=client)["decision"] == "reject"
    assert evaluate_episode(evidence(reproducibility=None), api_key="test", model="test/model", client=client)["decision"] == "review"
    client.post.assert_not_called()


def test_valid_accept_and_evidence_only_request():
    client = fake_client()
    result = evaluate_episode(evidence(), api_key="test", model="test/model", client=client)
    assert result["decision"] == "accept"
    assert result["source"] == "openrouter"
    assert result["model_response"]["qa_version"] == QA_VERSION
    call = client.post.call_args
    assert call.args[0].endswith("/chat/completions")
    assert call.kwargs["json"]["model"] == "test/model"
    assert "ripe_harvested" in call.kwargs["json"]["messages"][1]["content"]


def test_invalid_evidence_reference_and_model_id_fail_closed():
    for altered in ({"evidence_keys": ["unmeasured_collision_count"]}, {"model_id": "other/model"}):
        result = evaluate_episode(evidence(), api_key="test", model="test/model", client=fake_client(**altered))
        assert result["decision"] == "review"
        assert result["source"] == "unavailable"


def test_accept_requires_replay_and_completion_citations():
    client = fake_client(evidence_keys=["hard_gates.data_integrity"])
    result = evaluate_episode(evidence(), api_key="test", model="test/model", client=client)
    assert result["decision"] == "review"
    assert result["source"] == "unavailable"


def test_wrong_task_or_qa_version_cannot_call_model():
    client = fake_client()
    for change in ({"task": "other-task"}, {"qa_version": "outdated"}):
        result = evaluate_episode({**evidence(), **change}, api_key="test", model="test/model", client=client)
        assert result["decision"] == "review"
        assert result["source"] == "validation"
    client.post.assert_not_called()


def test_no_credentials_or_transport_failure_fail_closed():
    with patch("agriphilo.openrouter_qa.load_dotenv"), patch.dict("agriphilo.openrouter_qa.os.environ", {}, clear=True):
        assert evaluate_episode(evidence())["decision"] == "review"
    client = Mock()
    client.post.side_effect = __import__("httpx").ConnectError("offline")
    assert evaluate_episode(evidence(), api_key="test", model="test/model", client=client)["decision"] == "review"


def test_high_idle_ratio_requires_review_without_model_call():
    item = evidence()
    item["quality_signals"]["idle_ratio"] = 0.40
    client = fake_client()
    assert evaluate_episode(item, api_key="test", model="test/model", client=client)["decision"] == "review"
    client.post.assert_not_called()


def _recording(session_id):
    lines = [
        {"type": "header", "schema_version": 0, "session_id": session_id,
         "task": {"task_id": "tomato-path-harvest", "tomato_count": 1, "layout_parameters": {"seed": 7}},
         "control_rate_hz": 50, "nq": 3, "scene_xml_sha256": "a" * 64},
        {"type": "step", "i": 0, "t": 0.02, "qpos": [0, 0, 0], "ctrl": [0], "attached": [0]},
        {"type": "event", "event": {"kind": "harvested", "time": 0.02,
                                   "tomato": 0, "ripeness": "ripe"}},
        {"type": "footer", "steps": 1, "harvested_ripe": 1, "harvested_unripe": 0, "dropped": 0},
    ]
    return gzip.compress(("\n".join(json.dumps(line) for line in lines) + "\n").encode())


def test_marketplace_needs_validated_model_accept_before_reward_or_sale(tmp_path):
    session = "11111111-1111-4111-8111-111111111111"
    game_id = "wf-aaaaaaaaaaaa"
    ledger = Ledger(tmp_path / "ledger.json")
    market = WeFarmMarketplace(ledger, tmp_path / "market")
    market.requests.append({"game_id": game_id, "requester_id": "requester", "task_spec": {},
                            "status": "live", "created_at": 0, "environment_status": "teammate_simulator_template"})
    replay = {"replay_checked": True, "passed": True, "reasons": [], "metrics": {"ripe_harvested": 1}}
    with patch("agriphilo.wefarm_marketplace._replay", return_value=replay), \
         patch("agriphilo.neo4j_graph.GraphStore.from_env"), \
         patch("agriphilo.openrouter_qa.load_dotenv"), \
         patch.dict("agriphilo.openrouter_qa.os.environ", {}, clear=True):
        pending = market.submit_episode(game_id, "player", session, _recording(session))
    assert pending["qa"] == "pending"
    assert pending["reward_credits"] == 0
    assert pending["qa_rule_results"]["task_completion"] is True
    assert pending["qa_model_response"] is None
    assert ledger.balance("payable:player") == 0
    assert market.listing(market.requests[0])["episodes_available"] == 0


def test_marketplace_does_not_reward_an_unverified_accept(tmp_path):
    session = "22222222-2222-4222-8222-222222222222"
    game_id = "wf-bbbbbbbbbbbb"
    ledger = Ledger(tmp_path / "ledger.json")
    market = WeFarmMarketplace(ledger, tmp_path / "market")
    market.requests.append({"game_id": game_id, "requester_id": "requester", "task_spec": {},
                            "status": "live", "created_at": 0, "environment_status": "teammate_simulator_template"})
    replay = {"replay_checked": True, "passed": True, "reasons": [], "metrics": {"ripe_harvested": 1}}
    unverified = {"decision": "accept", "source": "unavailable", "reason": "Unverified result.",
                  "evidence_keys": [], "model_id": None, "qa_version": QA_VERSION, "model_response": None}
    with patch("agriphilo.wefarm_marketplace._replay", return_value=replay), \
         patch("agriphilo.openrouter_qa.evaluate_episode", return_value=unverified), \
         patch("agriphilo.neo4j_graph.GraphStore.from_env"):
        result = market.submit_episode(game_id, "player", session, _recording(session))
    assert result["qa"] == "pending"
    assert result["reward_credits"] == 0
    assert market.listing(market.requests[0])["episodes_available"] == 0
