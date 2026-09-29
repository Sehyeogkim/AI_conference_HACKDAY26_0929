"""Safety gates for the teammate simulator marketplace adapter."""

import base64
import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agriphilo.credits import Ledger
from agriphilo.wefarm_marketplace import WeFarmMarketplace, check_recording


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lV8AAAAASUVORK5CYII=")
SESSION = "11111111-1111-4111-8111-111111111111"


def recording(*, harvest: bool = False) -> bytes:
    events = ([{"type": "event", "event": {"kind": "harvested", "time": 0.02,
                                            "tomato": 0, "ripeness": "ripe"}}] if harvest else [])
    lines = [
        {"type": "header", "schema_version": 0, "session_id": SESSION,
         "task": {"task_id": "tomato-path-harvest", "tomato_count": 1, "layout_parameters": {"seed": 7}},
         "control_rate_hz": 50, "nq": 3, "scene_xml_sha256": "a" * 64},
        {"type": "step", "i": 0, "t": 0.02, "qpos": [0, 0, 0], "ctrl": [0], "attached": [1]},
        *events,
        {"type": "footer", "steps": 1, "harvested_ripe": int(harvest),
         "harvested_unripe": 0, "dropped": 0},
    ]
    return gzip.compress(("\n".join(json.dumps(line) for line in lines) + "\n").encode())


def test_request_publishes_teammate_template_without_claiming_image_generation():
    with tempfile.TemporaryDirectory() as temp, patch(
        "agriphilo.openrouter_tasks.interpret_request",
        return_value={"source": "preset", "image_analyzed": False,
                      "task_spec": {"requested_episodes": 3, "objective": "Harvest ripe tomatoes"}},
    ), patch("agriphilo.neo4j_graph.GraphStore.from_env") as graph:
        market = WeFarmMarketplace(Ledger(Path(temp) / "ledger.json"), Path(temp) / "market")
        result = market.create_request("requester", "Collect ripe tomato harvest trajectories", "farm.png",
                                       "data:image/png;base64," + base64.b64encode(PNG).decode())
        assert result["status"] == "published"
        assert result["image_analyzed"] is False
        assert "not converted into a 3D world" in result["environment_assumption"]
        assert result["game"]["episodes_wanted"] == 3
        assert Path(market.requests[0]["image_path"]).read_bytes() == PNG
        graph.return_value.upsert_task.assert_called_once()


def test_no_harvest_is_stored_but_cannot_earn_or_be_purchased():
    with tempfile.TemporaryDirectory() as temp, patch("agriphilo.neo4j_graph.GraphStore.from_env"):
        ledger = Ledger(Path(temp) / "ledger.json")
        market = WeFarmMarketplace(ledger, Path(temp) / "market")
        game_id = "wf-aaaaaaaaaaaa"
        market.requests.append({"game_id": game_id, "requester_id": "requester", "task_spec": {},
                                "status": "live", "created_at": 0, "environment_status": "teammate_simulator_template"})
        record = market.submit_episode(game_id, "player", SESSION, recording())
        assert record["qa"] == "fail"
        assert record["structural_pass"] is False
        assert record["reward_credits"] == 0
        assert ledger.balance("payable:player") == 0
        assert market.mine(game_id) == []
        with pytest.raises(ValueError, match="0 QA-passed"):
            market.purchase(game_id, 1)
        assert (Path(temp) / "market" / "episodes" / game_id / record["file"]).is_file()
        cloned = [json.loads(line) for line in gzip.decompress(recording()).splitlines()]
        second_session = "22222222-2222-4222-8222-222222222222"
        cloned[0]["session_id"] = second_session
        cloned_blob = gzip.compress(("\n".join(json.dumps(line) for line in cloned) + "\n").encode())
        with pytest.raises(ValueError, match="duplicate recording trajectory"):
            market.submit_episode(game_id, "player", second_session, cloned_blob)


def test_gzip_payload_must_match_upload_session():
    with tempfile.TemporaryDirectory() as temp:
        market = WeFarmMarketplace(Ledger(Path(temp) / "ledger.json"), Path(temp) / "market")
        market.requests.append({"game_id": "wf-aaaaaaaaaaaa"})
        with pytest.raises(ValueError, match="invalid session ID"):
            market.submit_episode("wf-aaaaaaaaaaaa", "player", "wrong", recording())
        with pytest.raises(ValueError, match="gzip recording"):
            market.submit_episode("wf-aaaaaaaaaaaa", "player", SESSION, b"not gzip")


def test_ai_decision_metadata_does_not_change_trajectory_identity():
    original = recording()
    lines = [json.loads(line) for line in gzip.decompress(original).splitlines()]
    lines.insert(2, {"type": "ai_decision", "frame_id": 1, "model": "demo-vlm",
                     "action": "wait", "reason": "Observe the tomato"})
    with_ai_metadata = gzip.compress(("\n".join(json.dumps(line) for line in lines) + "\n").encode())
    _, baseline, _ = check_recording(original, SESSION)
    _, ai_episode, _ = check_recording(with_ai_metadata, SESSION)
    assert ai_episode["trajectory_sha256"] == baseline["trajectory_sha256"]
