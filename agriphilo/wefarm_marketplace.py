"""WeFarm request and teammate-simulator recording marketplace.

The uploaded image is retained as requester input. A teammate-produced World Labs
package is a separate handoff; this service does not pretend to create one from
the image. Browser recordings are kept in their native gzip JSONL format.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .agents.payment import PRICING
from .credits import InsufficientCredits


REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO / "runs" / "wefarm"
GAME_ID = re.compile(r"wf-[0-9a-f]{12}\Z")
PLAYER_ID = re.compile(r"[a-z0-9._-]{1,120}\Z")
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 6_000_000
MAX_RECORDING_BYTES = 20_000_000
MAX_UNCOMPRESSED_BYTES = 150_000_000
MAX_STEPS = 200_000
PRICE = PRICING["game"]["episode_price_credits"]
REWARD = PRICING["game"]["player_share_credits"]
CUSTOMER = "demo-customer"


def _save_json(path: Path, value: Any) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _approved(record: dict) -> bool:
    """Only a current, audited OpenRouter acceptance is marketplace-eligible."""
    from .openrouter_qa import QA_VERSION
    gates = record.get("qa_rule_results")
    return (record.get("qa") == "pass" and record.get("ai_source") == "openrouter"
            and record.get("ai_verdict") == "accept" and record.get("qa_version") == QA_VERSION
            and isinstance(gates, dict) and all(gates.get(k) is True for k in
                                                 ("data_integrity", "reproducibility", "task_completion", "uniqueness"))
            and isinstance(record.get("qa_model_response"), dict))


def _finite_vector(value: Any, count: int) -> bool:
    return (isinstance(value, list) and len(value) == count
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value))


def _decode_image(data_url: str) -> tuple[bytes, str, str]:
    match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)", data_url)
    if not match:
        raise ValueError("Upload a JPEG, PNG, or WebP image")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Image data is invalid") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be between 1 byte and 6 MB")
    mime = match.group(1)
    signatures = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures[mime]:
        raise ValueError("Image bytes do not match the declared type")
    return data, mime, IMAGE_TYPES[mime]


def _recording_lines(blob: bytes) -> list[dict]:
    if not blob.startswith(b"\x1f\x8b") or len(blob) > MAX_RECORDING_BYTES:
        raise ValueError("Expected a gzip recording no larger than 20 MB")
    try:
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(blob)) as stream:
            raw = stream.read(MAX_UNCOMPRESSED_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise ValueError("Could not decompress recording") from exc
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Recording expands beyond 150 MB")
    try:
        lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Recording is not JSON Lines") from exc
    if not lines or any(not isinstance(line, dict) for line in lines):
        raise ValueError("Recording is empty or malformed")
    return lines


def check_recording(blob: bytes, expected_session_id: str) -> tuple[dict, dict, list[str]]:
    """Check format and event consistency; does not claim physics replay."""
    lines = _recording_lines(blob)
    header, footer = lines[0], lines[-1]
    reasons: list[str] = []
    if header.get("type") != "header" or header.get("schema_version") != 0:
        reasons.append("unsupported recording header or schema")
    if footer.get("type") != "footer":
        reasons.append("missing recording footer")
    if header.get("session_id") != expected_session_id:
        reasons.append("session ID does not match upload context")
    task = header.get("task") or {}
    if not isinstance(task, dict):
        task = {}
        reasons.append("invalid task metadata")
    if task.get("task_id") != "tomato-path-harvest":
        reasons.append("unexpected simulator task")
    nq = header.get("nq")
    tomato_count = task.get("tomato_count")
    hz = header.get("control_rate_hz")
    if not isinstance(nq, int) or not 1 <= nq <= 10_000:
        reasons.append("invalid qpos size")
        nq = 0
    if not isinstance(tomato_count, int) or not 1 <= tomato_count <= 500:
        reasons.append("invalid tomato count")
        tomato_count = 0
    if hz != 50:
        reasons.append("unsupported control rate")
    scene_hash = header.get("scene_xml_sha256")
    if not isinstance(scene_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", scene_hash):
        reasons.append("missing scene hash")
    steps = [line for line in lines[1:-1] if line.get("type") == "step"]
    events = [line.get("event") for line in lines[1:-1] if line.get("type") == "event"]
    if not steps or len(steps) > MAX_STEPS:
        reasons.append("recording must contain 1 to 200000 steps")
    for i, step in enumerate(steps):
        if step.get("i") != i or not isinstance(step.get("t"), (int, float)) or not math.isfinite(step["t"]):
            reasons.append("step index or time is invalid")
            break
        if abs(step["t"] - (i + 1) / 50) > 0.1:
            reasons.append("step timing is inconsistent")
            break
        if not _finite_vector(step.get("qpos"), nq):
            reasons.append("qpos vector is invalid")
            break
        if not _finite_vector(step.get("attached"), tomato_count) or any(x not in (0, 1) for x in step["attached"]):
            reasons.append("stem attachment vector is invalid")
            break
        ctrl = step.get("ctrl")
        if not isinstance(ctrl, list) or not ctrl or len(ctrl) > 100 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in ctrl):
            reasons.append("actuator target vector is invalid")
            break
    harvested = [e for e in events if isinstance(e, dict) and e.get("kind") == "harvested"]
    ripe = sum(e.get("ripeness") == "ripe" for e in harvested)
    unripe = len(harvested) - ripe
    dropped = sum(isinstance(e, dict) and e.get("kind") == "dropped" for e in events)
    if any(not isinstance(e, dict) or e.get("kind") not in {"grasp", "detach", "release", "harvested", "dropped"}
           or not isinstance(e.get("tomato"), int) or not 0 <= e["tomato"] < tomato_count for e in events):
        reasons.append("event stream is invalid")
    if (footer.get("steps") != len(steps) or footer.get("harvested_ripe") != ripe
            or footer.get("harvested_unripe") != unripe or footer.get("dropped") != dropped):
        reasons.append("footer totals do not match recording")
    if ripe < 1:
        reasons.append("no ripe tomato was harvested")
    trajectory_digest = hashlib.sha256()
    for line in lines[1:-1]:
        if line.get("type") not in {"step", "event", "state"}:
            continue
        trajectory_digest.update(json.dumps(line, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        trajectory_digest.update(b"\n")
    parameters = task.get("layout_parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    facts = {"steps": len(steps), "duration_s": round(len(steps) / 50, 2), "harvested_count": ripe,
             "harvested_unripe": unripe, "dropped": dropped, "seed": parameters.get("seed"),
             "scene_hash": scene_hash, "event_count": len(events), "trajectory_sha256": trajectory_digest.hexdigest()}
    return header, facts, reasons


def _replay(path: Path) -> dict:
    script = REPO / "web" / "tools" / "replayRecording.ts"
    if not script.exists():
        return {"replay_checked": False, "passed": False, "reasons": ["physics replay checker is not available"]}
    try:
        proc = subprocess.run(["node", "--experimental-strip-types", str(script), str(path)],
                              cwd=script.parent.parent, capture_output=True, text=True, timeout=120)
        if proc.returncode:
            return {"replay_checked": False, "passed": False, "reasons": ["physics replay process failed"]}
        for line in reversed(proc.stdout.splitlines()):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("replay_checked"), bool) and isinstance(value.get("passed"), bool):
                return value
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"replay_checked": False, "passed": False, "reasons": ["physics replay could not be verified"]}


class WeFarmMarketplace:
    def __init__(self, ledger, root: Path = DEFAULT_ROOT):
        self.ledger = ledger
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.requests_path = self.root / "requests.json"
        self.episodes_path = self.root / "episodes.json"
        self.requests: list[dict] = json.loads(self.requests_path.read_text()) if self.requests_path.exists() else []
        self.episodes: list[dict] = json.loads(self.episodes_path.read_text()) if self.episodes_path.exists() else []
        self.lock = threading.RLock()

    def _request(self, game_id: str) -> dict:
        request = next((r for r in self.requests if r["game_id"] == game_id), None)
        if request is None:
            raise KeyError("unknown WeFarm game")
        return request

    def create_request(self, requester_id: str, request_text: str, image_name: str, image_data: str) -> dict:
        if not isinstance(request_text, str) or not 10 <= len(request_text.strip()) <= 4000:
            raise ValueError("Describe the requested harvest data in 10 to 4000 characters")
        if not isinstance(requester_id, str) or not requester_id.strip() or len(requester_id) > 200:
            raise ValueError("requester_id is required")
        if not isinstance(image_data, str):
            raise ValueError("An image is required")
        image, mime, ext = _decode_image(image_data)
        game_id = "wf-" + uuid.uuid4().hex[:12]
        folder = self.root / "requests" / game_id
        folder.mkdir(parents=True)
        image_path = folder / f"source{ext}"
        image_path.write_bytes(image)
        from .openrouter_tasks import interpret_request
        interpreted = interpret_request(request_text.strip(), image_bytes=image, image_mime=mime)
        task_spec = interpreted.get("task_spec") or {}
        record = {
            "game_id": game_id, "requester_id": requester_id.strip(), "request": request_text.strip(),
            "image_name": str(image_name or "farm image")[:200], "image_mime": mime,
            "image_sha256": _sha256(image), "image_path": str(image_path), "task_spec": task_spec,
            "orchestrator_source": interpreted.get("source", "template"), "orchestrator_model": interpreted.get("model"),
            "image_analyzed": bool(interpreted.get("image_analyzed")),
            "environment_status": "teammate_simulator_template", "created_at": time.time(), "status": "live",
        }
        with self.lock:
            self.requests.insert(0, record)
            _save_json(self.requests_path, self.requests)
        try:
            from .neo4j_graph import GraphStore
            graph = GraphStore.from_env()
            try:
                graph.upsert_task({"task_id": game_id, "task_spec": task_spec, "source": record["orchestrator_source"],
                                   "image_uri": str(image_path), "created_at": record["created_at"]})
            finally:
                graph.close()
        except Exception:
            pass  # Local index remains authoritative if AuraDB is not configured.
        return {"request_id": game_id, "status": "published", "game": self.listing(record),
                "environment_assumption": "The teammate simulator template is used; the uploaded image is stored as request input, not converted into a 3D world here.",
                "orchestrator_source": record["orchestrator_source"], "image_analyzed": record["image_analyzed"]}

    def listing(self, request: dict) -> dict:
        game_id = request["game_id"]
        episodes = [e for e in self.episodes if e["game_id"] == game_id]
        passed = [e for e in episodes if _approved(e)]
        spec = request.get("task_spec") or {}
        return {
            "order_id": game_id, "title": str(spec.get("title") or "Harvest ripe tomatoes")[:120],
            "task": "tomato-path-harvest", "task_steps": ["Drive through the tomato rows", "Pick ripe tomatoes", "Place them in the basket"],
            "robot": "Franka Panda on a movable cart", "scene": "MuJoCo · procedural tomato farm",
            "scene_image_url": f"/api/marketplace/{game_id}/image", "requester": request["requester_id"],
            "world_preview_url": "/static/demo/crete-world-render.jpg",
            "world": {"world_id": "crete-path", "version": 2, "model": "World Labs Marble 1.1"},
            "status": request["status"], "reward_per_episode": REWARD, "episode_price": PRICE,
            "episodes_wanted": int(spec.get("requested_episodes") or 1), "episodes_submitted": len(episodes),
            "episodes_passed": len(passed), "episodes_available": sum(not e["purchased"] for e in passed),
            "play_url": f"/games/wefarm/{game_id}", "viewer_url": None, "created_at": request["created_at"],
            "environment_status": request["environment_status"], "recording_format": "jsonl.gz",
        }

    def listings(self) -> list[dict]:
        with self.lock:
            return [self.listing(r) for r in self.requests]

    def summary(self, game_id: str) -> dict:
        with self.lock:
            request = self._request(game_id)
            return {"listing": self.listing(request), "episodes": [e for e in self.episodes if e["game_id"] == game_id],
                    "wallet_balance": self.ledger.balance(f"wallet:{CUSTOMER}"),
                    "request": {"text": request["request"], "image_name": request["image_name"],
                                "orchestrator_source": request["orchestrator_source"],
                                "image_analyzed": request["image_analyzed"]}}

    def image(self, game_id: str) -> tuple[Path, str] | None:
        """The requester's uploaded farm image and its media type."""
        with self.lock:
            request = self._request(game_id)
        path = Path(request["image_path"])
        return (path, request["image_mime"]) if path.is_file() else None

    def replay_path(self, game_id: str, episode_id: str) -> Path | None:
        """Any stored episode of this game, for watching (downloads stay purchase-gated)."""
        with self.lock:
            record = next((e for e in self.episodes if e["game_id"] == game_id and e["episode_id"] == episode_id), None)
        if record is None:
            return None
        path = self.root / "episodes" / game_id / record["file"]
        return path if path.is_file() else None

    # Free play: recordings made in the simulator without a marketplace game. They are stored on
    # the server (not downloaded in the browser) so they can be replayed later; they earn nothing.
    def _free_play_folder(self) -> Path:
        folder = self.root / "free-play"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_free_play(self, session_id: str, blob: bytes) -> dict:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", session_id):
            raise ValueError("invalid session ID")
        lines = _recording_lines(blob)
        header, footer = lines[0], lines[-1]
        if header.get("type") != "header" or header.get("session_id") != session_id:
            raise ValueError("recording header does not match the session ID")
        folder = self._free_play_folder()
        (folder / f"{session_id}.jsonl.gz").write_bytes(blob)
        summary = {"session_id": session_id, "saved_at": time.time(), "started_at": header.get("started_at"),
                   "steps": footer.get("steps"), "duration_s": footer.get("duration_s"),
                   "harvested_ripe": footer.get("harvested_ripe"), "dropped": footer.get("dropped"),
                   "operator_device": (header.get("operator") or {}).get("device")}
        _save_json(folder / f"{session_id}.json", summary)
        return summary

    def free_play_path(self, session_id: str) -> Path | None:
        path = self._free_play_folder() / f"{session_id}.jsonl.gz"
        return path if re.fullmatch(r"[0-9a-f-]{36}", session_id) and path.is_file() else None

    def free_play_sessions(self) -> list[dict]:
        items = []
        for path in self._free_play_folder().glob("*.json"):
            try:
                items.append(json.loads(path.read_text()))
            except ValueError:
                continue
        return sorted(items, key=lambda item: -item.get("saved_at", 0))

    def player_episodes(self, player_id: str) -> list[dict]:
        with self.lock:
            return [{**e, "order_id": e["game_id"], "title": "Harvest ripe tomatoes"}
                    for e in self.episodes if e["player_id"] == player_id]

    def submit_episode(self, game_id: str, player_id: str, session_id: str, blob: bytes) -> dict:
        if not GAME_ID.fullmatch(game_id) or not PLAYER_ID.fullmatch(player_id):
            raise ValueError("invalid game or player ID")
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", session_id):
            raise ValueError("invalid session ID")
        with self.lock:
            self._request(game_id)
            old = next((e for e in self.episodes if e["session_id"] == session_id), None)
            if old:
                if old["file_sha256"] == _sha256(blob) and old["game_id"] == game_id and old["player_id"] == player_id:
                    return old
                raise ValueError("session ID has already been submitted")
        header, facts, reasons = check_recording(blob, session_id)
        structural_pass = not reasons
        with self.lock:
            unique = not any(e["game_id"] == game_id and e.get("trajectory_sha256") == facts["trajectory_sha256"]
                             for e in self.episodes)
        if not unique:
            raise ValueError("duplicate recording trajectory")
        digest = _sha256(blob)
        episode_id = f"{game_id}-{uuid.uuid4().hex[:12]}"
        folder = self.root / "episodes" / game_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{episode_id}.jsonl.gz"
        path.write_bytes(blob)
        replay = _replay(path) if structural_pass and unique else {"replay_checked": False, "passed": False, "reasons": []}
        if replay.get("replay_checked") and not replay.get("passed"):
            reasons.extend(replay.get("reasons") or ["physics replay failed"])
        from .openrouter_qa import QA_VERSION, evaluate_episode
        replay_checked = replay.get("replay_checked") is True
        replay_pass = replay.get("passed") is True
        replay_metrics = replay.get("metrics") if isinstance(replay.get("metrics"), dict) else {}
        hard_gates = {
            "data_integrity": structural_pass,
            "reproducibility": replay_pass if replay_checked else None,
            "task_completion": (replay_pass and facts["harvested_count"] >= 1
                                and isinstance(replay_metrics.get("ripe_harvested"), (int, float))
                                and not isinstance(replay_metrics.get("ripe_harvested"), bool)
                                and replay_metrics["ripe_harvested"] >= 1) if replay_checked else None,
            "uniqueness": unique,
        }
        measured_evidence = {
            "task": "tomato-path-harvest", "qa_version": QA_VERSION,
            "hard_gates": hard_gates,
            "measurements": {"steps": facts["steps"], "duration_s": facts["duration_s"],
                             "harvested_count": facts["harvested_count"], "dropped": facts["dropped"],
                             "scene_hash": facts["scene_hash"], **replay_metrics},
            "quality_signals": {"dropped": facts["dropped"]},
            "unavailable_measurements": ["idle_ratio", "oscillation", "unintended_collisions",
                                         "near_duplicate_score", "starting_condition_coverage"],
        }
        ai = evaluate_episode(measured_evidence)
        qa = {"accept": "pass", "review": "pending", "reject": "fail"}[ai["decision"]]
        qa_reason = "; ".join([*reasons, ai["reason"]] if reasons else [ai["reason"]])
        record = {
            "episode_id": episode_id, "game_id": game_id, "file": path.name, "player_id": player_id,
            "session_id": session_id, "submitted_at": time.time(), **facts,
            "qa": qa, "quality": 4 if qa == "pass" else 1, "qa_reason": qa_reason,
            "reasons": reasons, "structural_pass": structural_pass, "replay_checked": bool(replay.get("replay_checked")),
            "replay_pass": replay_pass, "ai_verdict": ai["decision"],
            "ai_source": ai["source"], "ai_model": ai["model_id"], "qa_version": ai["qa_version"],
            "qa_rule_results": hard_gates, "qa_evidence": measured_evidence,
            "qa_model_response": ai["model_response"], "qa_evidence_keys": ai["evidence_keys"],
            "qa_metrics": replay_metrics, "file_sha256": digest, "reward_credits": 0,
            "purchased": False, "kind": "wefarm_mujoco_jsonl_gz", "operator_device": (header.get("operator") or {}).get("device"),
        }
        with self.lock:
            if any(e["game_id"] == game_id and e.get("trajectory_sha256") == facts["trajectory_sha256"]
                   for e in self.episodes):
                raise ValueError("duplicate recording trajectory")
            if record["qa"] == "pass" and not _approved(record):
                record["qa"] = "pending"
                record["quality"] = 1
                record["qa_reason"] = "OpenRouter QA approval could not be verified."
            if _approved(record):
                self.ledger.reward(game_id, episode_id, player_id, REWARD)
                record["reward_credits"] = REWARD
            self.episodes.insert(0, record)
            _save_json(self.episodes_path, self.episodes)
        try:
            from .neo4j_graph import GraphStore
            graph = GraphStore.from_env()
            try:
                graph.upsert_episode({"episode_id": episode_id, "task_id": game_id, "player_id": player_id,
                                      "duration_s": facts["duration_s"], "score": facts["harvested_count"],
                                      "recording_uri": str(path), "sha256": digest, "created_at": record["submitted_at"],
                                      "quality": record["quality"], "harvested_count": facts["harvested_count"],
                                      "seed": facts["seed"], "arm": "single"},
                                     {"qa_id": f"qa-{episode_id}", "episode_id": episode_id,
                                      "structural_pass": record["structural_pass"], "replay_pass": record["replay_pass"],
                                      "ai_verdict": record["ai_verdict"], "reasons": record["reasons"],
                                      "model_id": record["ai_model"], "qa_version": record["qa_version"],
                                      "evaluated_at": record["submitted_at"]})
            finally:
                graph.close()
        except Exception:
            pass
        return record

    def _unchanged(self, record: dict) -> bool:
        path = self.root / "episodes" / record["game_id"] / record["file"]
        return path.is_file() and _sha256(path.read_bytes()) == record["file_sha256"]

    def purchase(self, game_id: str, count: int) -> dict:
        with self.lock:
            self._request(game_id)
            available = sorted((e for e in self.episodes if e["game_id"] == game_id and _approved(e) and not e["purchased"]),
                               key=lambda e: e["submitted_at"])
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= len(available):
                raise ValueError(f"{len(available)} QA-passed episode(s) available; requested {count}")
            picks = available[:count]
            if any(not self._unchanged(e) for e in picks):
                raise ValueError("An episode changed after QA")
            ids = [e["episode_id"] for e in picks]
            entry = self.ledger.purchase(CUSTOMER, game_id, f"purchase:{game_id}:{uuid.uuid4().hex}", count * PRICE, ids)
            for episode in picks:
                episode["purchased"] = True
            _save_json(self.episodes_path, self.episodes)
            graph_recorded = False
            try:
                from .neo4j_graph import GraphStore
                graph = GraphStore.from_env()
                try:
                    graph_recorded = graph.record_purchase(f"purchase-{entry['id']}", game_id, ids,
                                                           self._request(game_id)["requester_id"], entry["id"])
                finally:
                    graph.close()
            except Exception:
                pass
            return {"episode_ids": ids, "credits": count * PRICE, "graph_recorded": graph_recorded}

    def file_path(self, game_id: str, episode_id: str) -> Path | None:
        with self.lock:
            record = next((e for e in self.episodes if e["game_id"] == game_id and e["episode_id"] == episode_id
                           and _approved(e) and e["purchased"] and e["replay_checked"]), None)
            if record is None or not self._unchanged(record):
                return None
            return self.root / "episodes" / game_id / record["file"]

    def mine(self, game_id: str, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - {"min_harvested", "min_quality", "max_duration_s", "player_id", "seed"}:
            raise ValueError("Unsupported data filter")
        for key in ("min_harvested", "min_quality", "seed"):
            if key in filters and (not isinstance(filters[key], int) or isinstance(filters[key], bool) or filters[key] < 0):
                raise ValueError(f"{key} must be a nonnegative integer")
        if "max_duration_s" in filters and (not isinstance(filters["max_duration_s"], (int, float))
                                             or isinstance(filters["max_duration_s"], bool)
                                             or not math.isfinite(filters["max_duration_s"])
                                             or filters["max_duration_s"] < 0):
            raise ValueError("max_duration_s must be a nonnegative finite number")
        graph_ids: set[str] | None = None
        graph_source = "local_fallback"
        try:
            from .neo4j_graph import GraphStore
            graph = GraphStore.from_env()
            try:
                found = graph.query_approved_episodes(game_id, filters)
                # An empty graph answer usually means the episode was never indexed; use the local index.
                if found:
                    graph_ids = set(found)
                    graph_source = "neo4j"
            finally:
                graph.close()
        except ValueError:
            raise
        except Exception:
            pass
        with self.lock:
            self._request(game_id)
            result = []
            for episode in self.episodes:
                if episode["game_id"] != game_id or not _approved(episode) or not episode["purchased"] or not self._unchanged(episode):
                    continue
                if graph_ids is not None and episode["episode_id"] not in graph_ids:
                    continue
                if "min_harvested" in filters and episode.get("harvested_count", 0) < filters["min_harvested"]:
                    continue
                if "min_quality" in filters and episode.get("quality", 0) < filters["min_quality"]:
                    continue
                if "max_duration_s" in filters and episode["duration_s"] > filters["max_duration_s"]:
                    continue
                if "player_id" in filters and episode["player_id"] != filters["player_id"]:
                    continue
                if "seed" in filters and episode["seed"] != filters["seed"]:
                    continue
                result.append({"metadata": dict(episode), "download_url": f"/api/marketplace/{game_id}/episodes/{episode['episode_id']}/file",
                               "index_source": graph_source})
            return result
