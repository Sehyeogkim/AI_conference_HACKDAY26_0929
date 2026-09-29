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
# Demo mode can replay stored OpenRouter responses; cached results remain
# distinct from live OpenRouter QA and cannot pass the strict purchase gate.
DEMO_CACHE_PATH = Path(__file__).resolve().parent / "demo_cache.json"


def _demo_cache() -> dict | None:
    if os.environ.get("WEFARM_DEMO_CACHE") != "1" or not DEMO_CACHE_PATH.is_file():
        return None
    return json.loads(DEMO_CACHE_PATH.read_text())


def _cached_interpretation(request_text: str) -> dict | None:
    cache = _demo_cache()
    if cache is None:
        return None
    hit = cache.get("requests", {}).get(_sha256(request_text.encode()))
    if hit:
        return {"source": "openrouter-cached", "model": hit.get("model"), "image_analyzed": hit.get("image_analyzed", False),
                "task_spec": hit["task_spec"]}
    from .openrouter_tasks import _fixed_spec
    return {"source": "preset", "model": None, "image_analyzed": False, "task_spec": _fixed_spec(request_text)}


def _cached_episode_review(evidence: dict) -> dict | None:
    """Provide an explicitly cached QA explanation, never a live approval."""
    cache = _demo_cache()
    if cache is None:
        return None
    from .openrouter_qa import HARD_GATE_KEYS, QA_VERSION
    gates = evidence.get("hard_gates") or {}
    base = {"qa_version": QA_VERSION, "source": "openrouter-cached", "model_id": cache["episode_qa"]["model"]}
    if all(gates.get(key) is True for key in HARD_GATE_KEYS):
        accept = cache["episode_qa"]["accept"]
        return {**base, "decision": "accept", "reason": accept["reason"], "evidence_keys": accept["evidence_keys"],
                "model_response": {**accept, "model_id": base["model_id"], "qa_version": QA_VERSION}}
    failed = [f"hard_gates.{key}" for key in HARD_GATE_KEYS if gates.get(key) is not True]
    return {**base, "decision": "reject", "reason": "A required QA check failed.", "evidence_keys": failed, "model_response": None}


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
    return (record.get("qa") == "pass" and record.get("qa_status") in (None, "approved")
            and record.get("ai_source") == "openrouter"
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
        interpreted = _cached_interpretation(request_text.strip()) or interpret_request(request_text.strip(), image_bytes=image, image_mime=mime)
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
                                   "image_uri": str(image_path), "created_at": record["created_at"],
                                   "requester_id": record["requester_id"], "status": record["status"]})
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
            "episodes_processing": sum(e.get("qa_status") in {"queued", "processing"} for e in episodes),
            "episodes_pending_review": sum(e.get("qa_status") == "pending_review" for e in episodes),
            "episodes_rejected": sum(e.get("qa_status") == "rejected" for e in episodes),
            "play_url": f"/games/wefarm/{game_id}", "viewer_url": None, "created_at": request["created_at"],
            "environment_status": request["environment_status"], "recording_format": "jsonl.gz",
        }

    def listings(self) -> list[dict]:
        with self.lock:
            return [self.listing(r) for r in self.requests if r.get("status") != "deleted"]

    def delete_listing(self, game_id: str, requester_id: str) -> dict:
        """Remove an owner's listing from discovery without erasing paid recordings."""
        if not GAME_ID.fullmatch(game_id):
            raise ValueError("invalid game ID")
        if not isinstance(requester_id, str) or not requester_id.strip():
            raise ValueError("requester_id is required")
        with self.lock:
            request = self._request(game_id)
            if request.get("requester_id") != requester_id.strip():
                raise PermissionError("Only the requester can delete this listing")
            changed = request.get("status") != "deleted"
            if request.get("status") != "deleted":
                request["status"] = "deleted"
                request["deleted_at"] = time.time()
                _save_json(self.requests_path, self.requests)
            result = {"game_id": game_id, "status": "deleted",
                      "episodes_preserved": sum(e["game_id"] == game_id for e in self.episodes)}
        if changed:
            self.start_graph_sync()
        return result

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

    # Free-play recordings are stored for replay but do not enter paid marketplace QA.
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

    def resume_queued_qa(self) -> None:
        """Resume uploads that were persisted before a server restart."""
        with self.lock:
            ids = [e["episode_id"] for e in self.episodes
                   if e.get("qa_status") in {"queued", "processing"}]
        for episode_id in ids:
            self._start_qa(episode_id)

    def _start_qa(self, episode_id: str) -> None:
        threading.Thread(target=self._run_qa, args=(episode_id,), daemon=True,
                         name=f"wefarm-qa-{episode_id}").start()

    def start_graph_sync(self) -> None:
        """Reconcile locally durable metadata without delaying the web server."""
        threading.Thread(target=self.sync_graph, daemon=True, name="wefarm-graph-sync").start()

    def sync_graph(self) -> bool:
        try:
            from .neo4j_graph import GraphStore
            graph = GraphStore.from_env()
            try:
                if not graph.configured:
                    return False
                graph.ensure_schema()  # Best effort: writes may still work without schema privilege.
                with self.lock:
                    requests = [dict(item) for item in self.requests]
                    episodes = [dict(item) for item in self.episodes]
                success = True
                for request in requests:
                    success = graph.upsert_task({
                        "task_id": request["game_id"], "task_spec": request.get("task_spec") or {},
                        "source": request.get("orchestrator_source"),
                        "image_uri": request.get("image_path"), "created_at": request.get("created_at"),
                        "requester_id": request.get("requester_id"), "status": request.get("status"),
                    }) and success
                for episode in episodes:
                    success = self._write_graph_qa(episode, graph=graph) and success
                return success
            finally:
                graph.close()
        except Exception:
            return False

    def submit_episode(self, game_id: str, player_id: str, session_id: str, blob: bytes,
                       *, defer_qa: bool = False) -> dict:
        if not GAME_ID.fullmatch(game_id) or not PLAYER_ID.fullmatch(player_id):
            raise ValueError("invalid game or player ID")
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", session_id):
            raise ValueError("invalid session ID")
        if not blob.startswith(b"\x1f\x8b") or len(blob) > MAX_RECORDING_BYTES:
            raise ValueError("Expected a gzip recording no larger than 20 MB")
        with self.lock:
            request = self._request(game_id)
            old = next((e for e in self.episodes if e.get("session_id") == session_id), None)
            if old:
                if old["file_sha256"] == _sha256(blob) and old["game_id"] == game_id and old["player_id"] == player_id:
                    return dict(old)
                raise ValueError("session ID has already been submitted")
            if request.get("status") != "live":
                raise ValueError("This game is no longer accepting episodes")
        header, facts, reasons = check_recording(blob, session_id)
        structural_pass = not reasons
        digest = _sha256(blob)
        episode_id = f"{game_id}-{uuid.uuid4().hex[:12]}"
        folder = self.root / "episodes" / game_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{episode_id}.jsonl.gz"
        from .openrouter_qa import QA_VERSION
        operator = header.get("operator")
        if not isinstance(operator, dict):
            operator = {}
        record = {
            "episode_id": episode_id, "game_id": game_id, "file": path.name, "player_id": player_id,
            "session_id": session_id, "submitted_at": time.time(), **facts,
            "qa": "pending", "qa_status": "queued", "quality": 1, "qa_reason": "QA is queued.",
            "reasons": list(reasons), "structural_pass": structural_pass,
            "replay_checked": False, "replay_pass": False, "ai_verdict": None,
            "ai_source": None, "ai_model": None, "qa_version": QA_VERSION,
            "qa_rule_results": {"data_integrity": structural_pass, "reproducibility": None,
                                "task_completion": None, "uniqueness": True},
            "qa_evidence": None, "qa_model_response": None, "qa_evidence_keys": [],
            "qa_metrics": {}, "file_sha256": digest, "reward_credits": 0,
            "purchased": False, "kind": "wefarm_mujoco_jsonl_gz", "operator_device": operator.get("device"),
        }
        with self.lock:
            if self._request(game_id).get("status") != "live":
                raise ValueError("This game is no longer accepting episodes")
            if any(e["game_id"] == game_id and e.get("trajectory_sha256") == facts["trajectory_sha256"]
                   for e in self.episodes):
                raise ValueError("duplicate recording trajectory")
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(blob)
            tmp.replace(path)
            self.episodes.insert(0, record)
            _save_json(self.episodes_path, self.episodes)
        if defer_qa:
            queued = dict(record)
            self._start_qa(episode_id)
            return queued
        self._run_qa(episode_id)
        with self.lock:
            return dict(next(e for e in self.episodes if e["episode_id"] == episode_id))

    def _run_qa(self, episode_id: str) -> None:
        with self.lock:
            record = next((e for e in self.episodes if e["episode_id"] == episode_id), None)
            if record is None or record.get("qa_status") not in {"queued", "processing"}:
                return
            queued = dict(record)
        if queued["qa_status"] == "queued":
            self._write_graph_qa(queued)
        with self.lock:
            record = next((e for e in self.episodes if e["episode_id"] == episode_id), None)
            if record is None or record.get("qa_status") not in {"queued", "processing"}:
                return
            record["qa_status"] = "processing"
            record["qa_started_at"] = time.time()
            record["qa_reason"] = "Replay and QA are processing."
            _save_json(self.episodes_path, self.episodes)
            snapshot = dict(record)
        self._write_graph_qa(snapshot)
        try:
            result = self._evaluate_episode(snapshot)
        except Exception:
            result = {"qa": "pending", "qa_status": "pending_review", "quality": 1,
                      "qa_reason": "QA processing could not be verified.", "ai_verdict": "review"}
        with self.lock:
            record = next((e for e in self.episodes if e["episode_id"] == episode_id), None)
            if record is None:
                return
            record.update(result)
            record["qa_completed_at"] = time.time()
            if record["qa"] == "pass" and not _approved(record):
                record.update({"qa": "pending", "qa_status": "pending_review", "quality": 1,
                               "qa_reason": "OpenRouter QA approval could not be verified."})
            if _approved(record):
                try:
                    self.ledger.reward(record["game_id"], episode_id, record["player_id"], REWARD)
                    record["reward_credits"] = REWARD
                except Exception:
                    record.update({"qa": "pending", "qa_status": "pending_review", "quality": 1,
                                   "qa_reason": "Player reward could not be recorded; QA approval is held."})
            _save_json(self.episodes_path, self.episodes)
            graph_record = dict(record)
        self._write_graph_qa(graph_record)

    def _evaluate_episode(self, record: dict) -> dict:
        from .openrouter_qa import QA_VERSION, evaluate_episode
        path = self.root / "episodes" / record["game_id"] / record["file"]
        reasons = list(record["reasons"])
        structural_pass = record["structural_pass"] and self._unchanged(record)
        if not structural_pass and not reasons:
            reasons.append("Recording changed after intake.")
        replay = _replay(path) if structural_pass else {"replay_checked": False, "passed": False, "reasons": []}
        if replay.get("replay_checked") and not replay.get("passed"):
            reasons.extend(replay.get("reasons") or ["physics replay failed"])
        replay_checked = replay.get("replay_checked") is True
        replay_pass = replay.get("passed") is True
        replay_metrics = replay.get("metrics") if isinstance(replay.get("metrics"), dict) else {}
        hard_gates = {
            "data_integrity": structural_pass,
            "reproducibility": replay_pass if replay_checked else None,
            "task_completion": (replay_pass and record["harvested_count"] >= 1
                                and isinstance(replay_metrics.get("ripe_harvested"), (int, float))
                                and not isinstance(replay_metrics.get("ripe_harvested"), bool)
                                and replay_metrics["ripe_harvested"] >= 1) if replay_checked else None,
            "uniqueness": True,
        }
        measured_evidence = {
            "task": "tomato-path-harvest", "qa_version": QA_VERSION,
            "hard_gates": hard_gates,
            "measurements": {"steps": record["steps"], "duration_s": record["duration_s"],
                             "harvested_count": record["harvested_count"], "dropped": record["dropped"],
                             "scene_hash": record["scene_hash"], **replay_metrics},
            "quality_signals": {"dropped": record["dropped"]},
            "unavailable_measurements": ["idle_ratio", "oscillation", "unintended_collisions",
                                         "near_duplicate_score", "starting_condition_coverage"],
        }
        ai = _cached_episode_review(measured_evidence) or evaluate_episode(measured_evidence)
        qa = {"accept": "pass", "review": "pending", "reject": "fail"}[ai["decision"]]
        verified_accept = (qa == "pass" and ai["source"] == "openrouter"
                           and isinstance(ai.get("model_response"), dict)
                           and all(value is True for value in hard_gates.values()))
        if qa == "pass" and not verified_accept:
            qa = "pending"
            qa_reason = "OpenRouter QA approval could not be verified."
        else:
            qa_reason = "; ".join([*reasons, ai["reason"]] if reasons else [ai["reason"]])
        return {
            "qa": qa, "qa_status": {"pass": "approved", "fail": "rejected", "pending": "pending_review"}[qa],
            "quality": 4 if qa == "pass" else 1, "qa_reason": qa_reason, "reasons": reasons,
            "structural_pass": structural_pass, "replay_checked": replay_checked, "replay_pass": replay_pass,
            "ai_verdict": ai["decision"], "ai_source": ai["source"], "ai_model": ai["model_id"],
            "qa_version": ai["qa_version"], "qa_rule_results": hard_gates, "qa_evidence": measured_evidence,
            "qa_model_response": ai["model_response"], "qa_evidence_keys": ai["evidence_keys"],
            "qa_metrics": replay_metrics,
        }

    def _write_graph_qa(self, record: dict, *, graph=None) -> bool:
        owned = graph is None
        try:
            if graph is None:
                from .neo4j_graph import GraphStore
                graph = GraphStore.from_env()
            try:
                path = self.root / "episodes" / record["game_id"] / record["file"]
                return graph.upsert_episode({"episode_id": record["episode_id"], "task_id": record["game_id"],
                                      "player_id": record["player_id"], "duration_s": record["duration_s"],
                                      "score": record["harvested_count"], "recording_uri": str(path),
                                      "sha256": record["file_sha256"], "created_at": record["submitted_at"],
                                      "quality": record["quality"], "harvested_count": record["harvested_count"],
                                      "seed": record["seed"], "arm": "single", "qa_status": record.get("qa_status"),
                                      "qa_started_at": record.get("qa_started_at"),
                                      "qa_completed_at": record.get("qa_completed_at"),
                                      "trajectory_sha256": record.get("trajectory_sha256"),
                                      "requester_id": self._request(record["game_id"])["requester_id"]},
                                     {"qa_id": f"qa-{record['episode_id']}", "episode_id": record["episode_id"],
                                      "structural_pass": record.get("structural_pass"),
                                      "replay_pass": record.get("replay_pass"),
                                      "ai_verdict": record.get("ai_verdict"), "reasons": record.get("reasons", []),
                                      "model_id": record.get("ai_model"), "qa_version": record.get("qa_version"),
                                      "qa_status": record.get("qa_status"), "ai_source": record.get("ai_source"),
                                      "hard_gates": record.get("qa_rule_results"),
                                      "reason": record.get("qa_reason"),
                                      "evidence": record.get("qa_evidence"),
                                      "model_response": record.get("qa_model_response"),
                                      "started_at": record.get("qa_started_at"),
                                      "evaluated_at": record.get("qa_completed_at")})
            finally:
                if owned:
                    graph.close()
        except Exception:
            return False

    def _unchanged(self, record: dict) -> bool:
        path = self.root / "episodes" / record["game_id"] / record["file"]
        return path.is_file() and _sha256(path.read_bytes()) == record["file_sha256"]

    def purchase(self, game_id: str, count: int) -> dict:
        with self.lock:
            request = self._request(game_id)
            if request.get("status") != "live":
                raise ValueError("This listing is no longer available for purchase")
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
                if found is not None:
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
