"""Marketplace for replay-checked MuJoCo tomato-harvest HDF5 episodes.

The game server writes one collect_demos-style ``data/demo_1`` per file under
``runs/episodes/tomato-harvest``. This adapter never accepts a browser-supplied
verdict: structural QA and simulator replay decide eligibility for rewards,
purchase, and Data Miner results. Crusoe may only explain that fixed result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import h5py
import numpy as np

from . import game_qa
from .agents.payment import PRICING


REPO = Path(__file__).resolve().parent.parent
TOMATO_ID = "tomato-harvest"
CUSTOMER = "demo-customer"
PRICE = PRICING["game"]["episode_price_credits"]
REWARD = PRICING["game"]["player_share_credits"]
DEFAULT_EPISODES = REPO / "runs" / "episodes" / TOMATO_ID
DEFAULT_ROOT = REPO / "runs" / "tomato_mujoco_marketplace"
PLAYER_ID = re.compile(r"[a-z0-9._-]{1,120}\Z")
STABLE_FILE_AGE_S = 1.0
REPLAY_TIMEOUT_S = 180
DOWNLOAD_PREFIX = f"/api/marketplace/{TOMATO_ID}/episodes"


def _text_attr(group: h5py.Group, key: str, default: str = "") -> str:
    value = group.attrs.get(key, default)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _bool_attr(group: h5py.Group, key: str) -> bool:
    value = group.attrs.get(key, False)
    return bool(value) if isinstance(value, (bool, np.bool_)) else False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_replay(path: Path) -> game_qa.EpisodeVerdict:
    """Replay in the simulator Python, where RoboCasa and MuJoCo are installed."""
    python = Path(os.getenv("WEMINE_SIM_PYTHON", str(game_qa.SIM_PYTHON)))
    if not python.exists():
        raise RuntimeError("simulator Python is unavailable for replay")
    env = dict(os.environ)
    env.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
    proc = subprocess.run(
        [str(python), "-m", "agriphilo.game_qa", str(path), "--replay"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=REPLAY_TIMEOUT_S,
    )
    if proc.returncode:
        raise RuntimeError("simulator replay failed")
    for line in reversed(proc.stdout.splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and result.get("episode_id") == "demo_1" and "passed" in result:
            return game_qa.EpisodeVerdict(
                episode_id="demo_1", passed=bool(result["passed"]),
                reasons=list(result.get("reasons", [])), metrics=dict(result.get("metrics", {})),
                replay_checked=bool(result.get("replay_checked", False)),
            )
    raise RuntimeError("simulator replay returned no episode verdict")


class TomatoMuJoCoMarketplace:
    """Index completed simulator files and expose purchased data to the miner."""

    def __init__(
        self,
        ledger,
        episode_dir: Path = DEFAULT_EPISODES,
        root: Path = DEFAULT_ROOT,
        game_server_url: str | None = None,
        explainer: Callable[[dict], Any] | None = None,
        replay_qa: Callable[[Path], game_qa.EpisodeVerdict] | None = None,
    ):
        self.ledger = ledger
        self.episode_dir = Path(episode_dir)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = self.root / "index.json"
        self.lock = threading.RLock()
        self.game_server_url = game_server_url
        self.explainer = explainer
        self.replay_qa = replay_qa or _default_replay
        self.episodes: list[dict] = json.loads(self.index.read_text()) if self.index.exists() else []

    def _save(self) -> None:
        tmp = self.index.with_name(f"{self.index.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self.episodes, indent=2, ensure_ascii=False))
        tmp.replace(self.index)

    def scan(self) -> list[dict]:
        """QA each stable, previously unseen HDF5 once; never reward without replay."""
        with self.lock:
            if not self.episode_dir.exists():
                return []
            seen = {e["file"] for e in self.episodes}
            hashes = {e["fingerprint"] for e in self.episodes if e.get("fingerprint")}
            added = []
            for path in sorted(self.episode_dir.glob("*.hdf5")):
                if path.name in seen or time.time() - path.stat().st_mtime < STABLE_FILE_AGE_S:
                    continue
                rec = self._review(path, hashes)
                self.episodes.insert(0, rec)
                seen.add(path.name)
                if rec.get("fingerprint"):
                    hashes.add(rec["fingerprint"])
                self._save()
                added.append(rec)
            return added

    def _review(self, path: Path, hashes: set[str]) -> dict:
        reasons: list[str] = []
        facts: dict[str, Any] = {}
        digest = None
        file_sha256 = _file_sha256(path)
        structural: game_qa.EpisodeVerdict | None = None
        try:
            with h5py.File(path, "r") as f:
                data = f["data"]
                if list(data.keys()) != ["demo_1"]:
                    raise ValueError("episode must contain exactly data/demo_1")
                demo = data["demo_1"]
                task = _text_attr(data, "env")
                if task != "TomatoHarvest":
                    reasons.append(f"wrong task: {task or 'missing'}")
                player_id = _text_attr(demo, "player_id", "unknown")
                if not PLAYER_ID.fullmatch(player_id):
                    reasons.append("invalid player_id")
                actions = np.asarray(demo["actions"][()]) if "actions" in demo else np.empty((0, 0))
                states = np.asarray(demo["states"][()]) if "states" in demo else np.empty((0, 0))
                if states.ndim == 2 and actions.ndim == 2 and len(states) == len(actions):
                    digest = game_qa.episode_hash(states, actions)
                    if digest in hashes:
                        reasons.append("duplicate of an earlier episode")
                env_info = json.loads(_text_attr(data, "env_info", "{}"))
                hz = float(env_info.get("control_freq", 20))
                if not np.isfinite(hz) or hz <= 0:
                    hz = 20.0
                facts = {
                    "player_id": player_id, "task": task, "steps": int(len(actions)),
                    "duration_s": round(len(actions) / hz, 2),
                    "stage": int(demo.attrs.get("stage", 0)),
                    "simulator_success_recorded": _bool_attr(demo, "success"),
                    "seed": int(demo.attrs["seed"]) if "seed" in demo.attrs else None,
                    # PandaOmron is a single right-arm robot in this game.
                    "arm": "right",
                    # The task has one tomato; stage 2 is tray placement.
                    "harvested_count": 1 if int(demo.attrs.get("stage", 0)) >= 2 else 0,
                }
                structural = game_qa.check_structure(demo, "demo_1", game_qa.QAConfig())
                reasons.extend(structural.reasons)
        except Exception as exc:
            reasons.append(f"unreadable episode: {type(exc).__name__}")
        replay_checked = False
        replay_metrics = {}
        if not reasons and structural and structural.passed:
            try:
                replay = self.replay_qa(path)
                replay_checked = bool(replay.replay_checked)
                replay_metrics = replay.metrics
                reasons.extend(replay.reasons)
                if not replay_checked:
                    reasons.append("simulator replay was not verified")
                if not replay.passed and not replay.reasons:
                    reasons.append("simulator replay failed")
                if not facts["simulator_success_recorded"]:
                    reasons.append("game did not record task success")
            except Exception as exc:
                reasons.append(f"replay unavailable: {type(exc).__name__}")
        if _file_sha256(path) != file_sha256:
            reasons.append("episode changed during QA")
        passed = not reasons and replay_checked
        idle = (structural.metrics.get("idle_ratio", 1.0) if structural else 1.0)
        quality = 5 if passed and idle < 0.2 else 4 if passed and idle < 0.4 else 3 if passed else 1
        explanation = "; ".join(reasons) if reasons else "Tomato harvest passed structural and simulator replay QA."
        qa_source = "rules"
        if self.explainer:
            try:
                explained = self.explainer({"pass": passed, "reasons": reasons, "facts": facts,
                                            "replay_checked": replay_checked})
                explanation = getattr(explained, "text", explained) or explanation
                qa_source = getattr(explained, "source", "rules")
            except Exception:
                pass
        episode_id = path.stem
        rec = {
            "episode_id": episode_id, "file": path.name, "player_id": facts.get("player_id", "unknown"),
            "submitted_at": path.stat().st_mtime, "steps": facts.get("steps", 0),
            "duration_s": facts.get("duration_s", 0), "stage": facts.get("stage", 0),
            "seed": facts.get("seed"), "arm": facts.get("arm"), "harvested_count": facts.get("harvested_count"),
            "success": facts.get("simulator_success_recorded", False),
            "qa": "pass" if passed else "fail", "quality": quality, "qa_reason": str(explanation),
            "qa_explanation_source": qa_source, "reasons": reasons, "replay_checked": replay_checked,
            "qa_metrics": {**(structural.metrics if structural else {}), **replay_metrics},
            "fingerprint": digest, "file_sha256": file_sha256,
            "reward_credits": 0.0, "purchased": False,
            "kind": "mujoco_robocasa_replay_checked" if replay_checked else "mujoco_robocasa_unverified",
        }
        if passed:
            self.ledger.reward(TOMATO_ID, episode_id, rec["player_id"], REWARD)
            rec["reward_credits"] = REWARD
        return rec

    def _play_url(self, role: str) -> str:
        base = self.game_server_url or os.getenv("WEMINE_TOMATO_GAME_URL") or "http://127.0.0.1:8011/"
        params = dict(role=role, game_id=TOMATO_ID, task="TomatoHarvest", robot="PandaOmron")
        return base + ("&" if "?" in base else "?") + urlencode(params)

    def listing(self) -> dict:
        self.scan()
        with self.lock:
            passed = [e for e in self.episodes if e["qa"] == "pass"]
            return {
                "order_id": TOMATO_ID, "title": "Harvest tomatoes with a wheeled robot",
                "task": "TomatoHarvest", "task_steps": ["Pick up the tomato", "Place it in the harvest tray"],
                "robot": "PandaOmron wheeled arm", "scene": "MuJoCo · café counter tomato task",
                "scene_image_url": "/static/tomato-preview.jpg", "requester": CUSTOMER, "status": "live",
                "reward_per_episode": REWARD, "episode_price": PRICE, "episodes_wanted": 20,
                "episodes_submitted": len(self.episodes), "episodes_passed": len(passed),
                "episodes_available": sum(not e["purchased"] for e in passed),
                "play_url": self._play_url("player"), "viewer_url": self._play_url("viewer"),
                "created_at": 0,
            }

    def summary(self) -> dict:
        listing = self.listing()
        with self.lock:
            return {"listing": listing, "episodes": list(self.episodes),
                    "wallet_balance": self.ledger.balance(f"wallet:{CUSTOMER}")}

    def player_episodes(self, player_id: str) -> list[dict]:
        self.scan()
        with self.lock:
            return [{**e, "order_id": TOMATO_ID, "title": "Harvest tomatoes with a wheeled robot"}
                    for e in self.episodes if e["player_id"] == player_id]

    def purchase(self, count: int) -> dict:
        self.scan()
        with self.lock:
            available = sorted((e for e in self.episodes if e["qa"] == "pass" and not e["purchased"]),
                               key=lambda e: e["submitted_at"])
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= len(available):
                raise ValueError(f"{len(available)} QA-passed episode(s) available; requested {count}")
            picks = available[:count]
            if any(not self._unchanged(e) for e in picks):
                raise ValueError("A selected episode changed after QA")
            ids = [e["episode_id"] for e in picks]
            self.ledger.purchase(CUSTOMER, TOMATO_ID, f"purchase:{TOMATO_ID}:{uuid.uuid4().hex}",
                                 count * PRICE, ids)
            for e in picks:
                e["purchased"] = True
            self._save()
            return {"episode_ids": ids, "credits": count * PRICE}

    def file_path(self, episode_id: str) -> Path | None:
        """Resolve a purchased, replay-checked episode for the authenticated download route."""
        self.scan()
        with self.lock:
            record = next((e for e in self.episodes if e["episode_id"] == episode_id and e["qa"] == "pass"
                           and e["purchased"] and e["replay_checked"]), None)
            if record is None:
                return None
            path = self.episode_dir / record["file"]
            return path if self._unchanged(record) else None

    def _unchanged(self, record: dict) -> bool:
        path = self.episode_dir / record["file"]
        return (path.is_file() and path.resolve().parent == self.episode_dir.resolve()
                and bool(record.get("file_sha256")) and _file_sha256(path) == record["file_sha256"])

    def mine(self, request: dict | None = None) -> list[dict]:
        """Select purchased, QA-passed HDF5 episodes by supported metadata filters."""
        self.scan()
        if request is None:
            request = {}
        if not isinstance(request, dict):
            raise ValueError("Data request must be a JSON object")
        allowed = {"min_quality", "max_duration_s", "player_id", "seed", "arm", "min_harvested"}
        if set(request) - allowed:
            raise ValueError("Unsupported data filter")
        for key in ("min_quality", "min_harvested"):
            if key in request and (not isinstance(request[key], int) or isinstance(request[key], bool)
                                   or request[key] < 0):
                raise ValueError(f"{key} must be a nonnegative integer")
        if "max_duration_s" in request and (not isinstance(request["max_duration_s"], (int, float))
                                             or isinstance(request["max_duration_s"], bool)
                                             or not np.isfinite(request["max_duration_s"]) or request["max_duration_s"] < 0):
            raise ValueError("max_duration_s must be a nonnegative finite number")
        if "seed" in request and (not isinstance(request["seed"], int) or isinstance(request["seed"], bool)):
            raise ValueError("seed must be an integer")
        if "arm" in request and request["arm"] not in ("left", "right"):
            raise ValueError("arm must be left or right")
        with self.lock:
            results = []
            for e in self.episodes:
                if e["qa"] != "pass" or not e["purchased"] or not e["replay_checked"]:
                    continue
                if not self._unchanged(e):
                    continue
                if "min_quality" in request and e["quality"] < request["min_quality"]:
                    continue
                if "max_duration_s" in request and e["duration_s"] > request["max_duration_s"]:
                    continue
                if "player_id" in request and e["player_id"] != request["player_id"]:
                    continue
                if "seed" in request and e.get("seed") != request["seed"]:
                    continue
                if "arm" in request and e.get("arm") != request["arm"]:
                    continue
                if "min_harvested" in request and (e.get("harvested_count") is None
                                                    or e["harvested_count"] < request["min_harvested"]):
                    continue
                results.append({"metadata": dict(e), "download_url": f"{DOWNLOAD_PREFIX}/{e['episode_id']}/file"})
            return results
