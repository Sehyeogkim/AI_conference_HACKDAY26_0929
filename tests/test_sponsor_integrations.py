"""Sponsor adapters exercise actual payloads without contacting live services."""

import json

import httpx

from agriphilo import crusoe_review, neo4j_graph, openrouter_tasks


def test_openrouter_preset_is_explicit_and_does_not_analyze_image(monkeypatch):
    monkeypatch.setattr(openrouter_tasks, "load_dotenv", lambda: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = openrouter_tasks.interpret_request("Collect 12 ripe tomato harvests", b"abc", "image/png")
    assert result["source"] == "preset"
    assert result["image_analyzed"] is False
    assert result["task_spec"]["environment_generation"].startswith("assumed")


def test_openrouter_vision_call_cannot_change_fixed_simulator(monkeypatch):
    monkeypatch.setattr(openrouter_tasks, "load_dotenv", lambda: None)
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "requested_episodes": 4,
            "objective": "Pick red tomatoes into basket",
            "quality_requirements": ["fruit placed inside basket"],
            "image_description": "Greenhouse rows",
            "simulator": "unsafe model supplied value",
        })}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = openrouter_tasks.interpret_request("harvest", b"abc", "image/png", api_key="test", client=client)
    assert result["source"] == "openrouter"
    assert result["image_analyzed"] is True
    assert result["task_spec"]["requested_episodes"] == 4
    assert result["task_spec"]["simulator"] == "browser MuJoCo"
    assert seen["messages"][1]["content"][1]["type"] == "image_url"


def test_crusoe_fails_closed_without_verified_replay(monkeypatch):
    monkeypatch.setattr(crusoe_review, "load_dotenv", lambda: None)
    monkeypatch.delenv("CRUSOE_API_KEY", raising=False)
    monkeypatch.delenv("CURSOE_API_KEY", raising=False)
    pending = crusoe_review.evaluate_episode({"structural_pass": True})
    failed = crusoe_review.evaluate_episode({"structural_pass": False, "replay_pass": True})
    assert pending["verdict"] == "pending"
    assert failed["verdict"] == "fail"
    assert failed["source"] == "deterministic_qa"


def test_crusoe_uses_only_bounded_evidence(monkeypatch):
    monkeypatch.setattr(crusoe_review, "load_dotenv", lambda: None)
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"verdict":"pass","reasons":["Harvest confirmed by replay."]}'}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = crusoe_review.evaluate_episode(
            {"structural_pass": True, "replay_pass": True, "harvested_count": 1},
            api_key="test", client=client,
        )
    assert result["verdict"] == "pass" and result["source"] == "crusoe"
    assert seen["messages"][1]["content"].find("harvested_count") >= 0


def test_neo4j_uses_parameterized_writes_and_approval_gate():
    class Driver:
        def __init__(self):
            self.calls = []

        def execute_query(self, query, **kwargs):
            self.calls.append((query, kwargs))

    driver = Driver()
    graph = neo4j_graph.GraphStore(driver)
    assert graph.upsert_task({"task_id": "task-1", "task_spec": {"objective": "Harvest"}})
    assert graph.upsert_episode(
        {"task_id": "task-1", "episode_id": "ep-1", "recording_uri": "runs/ep-1.jsonl.gz"},
        {"structural_pass": True, "replay_pass": False, "verdict": "pass"},
    )
    assert driver.calls[0][1]["parameters_"]["properties"]["objective"] == "Harvest"
    assert driver.calls[1][1]["parameters_"]["episode"]["approved"] is False
    assert "$episode" in driver.calls[1][0]


def test_neo4j_data_miner_returns_only_query_ids_and_preserves_filter_parameters():
    class Driver:
        def __init__(self):
            self.query = ""
            self.params = {}

        def execute_query(self, query, **kwargs):
            self.query = query
            self.params = kwargs["parameters_"]
            return ([{"episode_id": "approved-1"}], None, ["episode_id"])

    driver = Driver()
    graph = neo4j_graph.GraphStore(driver)
    ids = graph.query_approved_episodes("task-1", {"player_id": "alice", "min_harvested": 2})
    assert ids == ["approved-1"]
    assert "q.ai_verdict = 'accept'" in driver.query
    assert "q.ai_source = 'openrouter'" in driver.query
    assert "e.qa_status = 'approved'" in driver.query
    assert driver.params["player_id"] == "alice"
    assert driver.params["min_harvested"] == 2
    assert graph.query_approved_episodes("task-1", {"player_id": "' OR true"}) == ["approved-1"]
    assert "' OR true" not in driver.query


def test_neo4j_purchase_requires_all_approved_records():
    class Driver:
        def __init__(self, records):
            self.records = records
            self.params = None
            self.query = None

        def execute_query(self, query, **kwargs):
            self.query = query
            self.params = kwargs["parameters_"]
            return (self.records, None, ["purchase_id"])

    driver = Driver([])
    graph = neo4j_graph.GraphStore(driver)
    assert not graph.record_purchase("p1", "task-1", ["ep-1"], "requester-1", "credit-1")
    assert graph.last_error == "purchase episodes are missing or not approved"
    driver.records = [{"purchase_id": "p1"}]
    assert graph.record_purchase("p1", "task-1", ["ep-1"], "requester-1", "credit-1")
    assert "size(episodes) = size($episode_ids)" in driver.query
    assert driver.params["credit_transaction_id"] == "credit-1"
    assert not graph.record_purchase("p1", "task-1", ["ep-1", "ep-1"], "requester-1", "credit-1")


def test_neo4j_unavailable_query_signals_local_fallback():
    graph = neo4j_graph.GraphStore()
    assert graph.query_approved_episodes("task-1") is None
