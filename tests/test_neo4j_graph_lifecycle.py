"""The graph never advertises an episode before verified OpenRouter QA."""

from agriphilo.neo4j_graph import GraphStore


GATES = {
    "data_integrity": True,
    "reproducibility": True,
    "task_completion": True,
    "uniqueness": True,
}


class Driver:
    def __init__(self):
        self.calls = []
        self.records = []

    def execute_query(self, query, **kwargs):
        self.calls.append((query, kwargs["parameters_"]))
        return (self.records, None, [])


def test_queued_then_verified_episode_updates_one_graph_record():
    driver = Driver()
    graph = GraphStore(driver)
    episode = {
        "task_id": "wf-123", "episode_id": "wf-123-ep", "player_id": "player-1",
        "recording_uri": "/container/episodes/ep.jsonl.gz", "sha256": "a" * 64,
        "qa_status": "queued",
    }
    assert graph.upsert_episode(episode, {"qa_id": "qa-wf-123-ep"})
    queued = driver.calls[-1][1]
    assert queued["episode"]["approved"] is False
    assert queued["episode"]["qa_status"] == "queued"
    assert queued["episode"]["recording_uri"] == episode["recording_uri"]
    assert "MERGE (p:Player" in driver.calls[-1][0]

    episode["qa_status"] = "approved"
    qa = {"qa_id": "qa-wf-123-ep", "structural_pass": True, "replay_pass": True,
          "ai_verdict": "accept", "ai_source": "openrouter", "rule_results": GATES}
    assert graph.upsert_episode(episode, qa)
    approved = driver.calls[-1][1]
    assert approved["episode"]["approved"] is True
    assert approved["qa"]["ai_source"] == "openrouter"
    assert "MERGE (e:Episode {episode_id: $episode_id})" in driver.calls[-1][0]


def test_graph_rejects_model_or_hard_gate_shortcuts():
    driver = Driver()
    graph = GraphStore(driver)
    episode = {"task_id": "wf-123", "episode_id": "ep-1", "qa_status": "approved"}
    qa = {"structural_pass": True, "replay_pass": True, "ai_verdict": "accept",
          "ai_source": "openrouter", "rule_results": GATES}
    for delta in ({"rule_results": {**GATES, "task_completion": False}},
                  {"ai_source": "crusoe"}, {"ai_verdict": "review"}):
        assert graph.upsert_episode(episode, {**qa, **delta})
        assert driver.calls[-1][1]["episode"]["approved"] is False


def test_graph_metadata_read_is_parameterized_and_unavailable_is_distinct():
    driver = Driver()
    graph = GraphStore(driver)
    driver.records = [{"episode": {"episode_id": "ep-1", "qa_status": "queued"}, "qa": {}}]
    items = graph.query_task_episodes("wf-123")
    assert items is not None and items[0]["episode_id"] == "ep-1"
    assert items[0]["qa_status"] == "queued"
    assert driver.calls[-1][1] == {"task_id": "wf-123"}
    assert graph.query_task_episodes("wf-123") != GraphStore().query_task_episodes("wf-123")
