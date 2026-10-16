"""Single-player local browser host for the original RoboCasa coffee tasks.

All native simulation and graphics work happens on the event-loop thread.
File finalization stays on this thread: Python GC can finalize OpenGL resources.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

os.environ.setdefault("MUJOCO_GL", "cgl" if __import__("sys").platform == "darwin" else "egl")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
import numpy as np

from .tasks import CONTROL_HZ, HORIZONS, TASKS
from .access import install_access_gate, authorized_websocket

ROOT = Path(__file__).resolve().parents[1]
EPISODES = ROOT / "runs" / "episodes"
LOG = logging.getLogger("coffee_game")
ACTIVE_PHASES = {"recording", "paused", "completed", "stopped", "submitting", "replaying"}
MOVEMENT_KEYS = {
    "KeyW", "KeyS", "KeyA", "KeyD", "KeyR", "KeyF", "KeyQ", "KeyE",
    "KeyZ", "KeyX", "KeyC", "KeyV", "ShiftLeft", "ShiftRight",
}


class LocalGame:
    def __init__(self) -> None:
        self.sim: Any = None
        self.recorder: Any = None
        self.task_id = "CoffeeSetupMug"
        self.seed = 0
        self.mode = "collection"
        self.phase = "loading"
        self.error: str | None = None
        self.steps = 0
        self.success = False
        self.episode_id: str | None = None
        self.last_submission: dict | None = None
        self.end_reason = "user_stop"
        self.command = {"keys": [], "mode": "arm", "gripper": False, "fine": False}
        self.owner: str | None = None
        self.socket: WebSocket | None = None
        self.input_at = time.monotonic()
        self.last_images: dict[str, np.ndarray] = {}
        self.last_frames: dict = {}
        self.view = "overview"
        self._running = True
        self._task: asyncio.Task | None = None
        self._finalizing: asyncio.Task | None = None
        self._last_frame_at = 0.0
        self._last_status_at = 0.0
        self.last_step_wall_s = 0.0
        self._replay: dict | None = None

    def status(self) -> dict:
        return {
            "phase": self.phase, "task_id": self.task_id, "seed": self.seed,
            "mode": self.mode, "control_mode": self.command["mode"],
            "gripper_closed": self.command["gripper"], "steps": self.steps,
            "sim_time": self.steps / CONTROL_HZ, "success": self.success,
            "episode_id": self.episode_id, "error": self.error,
            "owner_connected": self.socket is not None,
            "tasks": list(TASKS.values()), "last_submission": self.last_submission,
            "recording": self.recorder is not None and self.phase == "recording",
            "has_recording": self.recorder is not None,
            "view": self.view,
            "metrics": {
                "control_hz": CONTROL_HZ, "camera_count": 3, "state_dim": 53,
                "action_dim": 12, "last_step_wall_ms": round(self.last_step_wall_s * 1000, 1),
                "realtime": self.last_step_wall_s <= 1 / CONTROL_HZ,
                "max_steps": HORIZONS[self.mode],
            },
        }

    def authorize(self, request: Request) -> None:
        client = request.headers.get("X-Player-ID")
        if not self.owner or client != self.owner or self.socket is None:
            raise HTTPException(409, "Connect the active player tab before controlling this session.")

    def clear_input(self) -> None:
        self.command["keys"] = []
        self.command["fine"] = False

    def reset(self, task_id: str, seed: int, mode: str) -> None:
        if task_id not in TASKS or mode not in HORIZONS:
            raise ValueError("Unknown task or collection mode")
        if self.recorder is not None or self.phase in {"submitting", "replaying"}:
            raise ValueError("Submit or discard this episode before resetting")
        from .simulator import CoffeeSimulator

        self.phase = "loading"
        self.error = None
        self.clear_input()
        self.command.update(mode="arm", gripper=False)
        if self.sim is not None:
            self.sim.close()
            self.sim = None
        self.sim = CoffeeSimulator(task_id=task_id, seed=seed, split="train")
        self.task_id, self.seed, self.mode = task_id, seed, mode
        self.steps, self.success, self.episode_id = 0, False, None
        self.end_reason = "user_stop"
        self.phase = "ready"
        self._capture_frame()

    def _capture_frame(self, observation: dict | None = None) -> None:
        if self.sim is None:
            return
        observation = observation if observation is not None else self.sim.observe()
        self.last_images = observation["images"]
        if self.view in self.last_images:
            main = self.last_images[self.view]
        elif hasattr(self.sim, "render_display"):
            main = self.sim.render_display(self.view)
        else:
            main = self.last_images["left"]
        images = {"main": main, **self.last_images}
        self.last_frames = {"type": "frame", "images": {k: encode_jpeg(v) for k, v in images.items()}}

    async def emit(self, payload: dict) -> None:
        ws = self.socket
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=0.4)
        except (Exception, asyncio.TimeoutError):
            if self.socket is ws:
                self.socket = None
                self.owner = None
                self.clear_input()
                if self.phase == "recording":
                    self.phase = "paused"

    async def run(self) -> None:
        try:
            await asyncio.sleep(0.4)
            self.reset(self.task_id, self.seed, self.mode)
            await self.emit(self.last_frames)
        except Exception as exc:
            LOG.exception("Initial simulator setup failed")
            self.phase, self.error = "error", str(exc)
        while self._running:
            tick = time.monotonic()
            try:
                if self.phase == "recording" and self.sim is not None:
                    if self.socket is None or tick - self.input_at > 1.0:
                        self.clear_input()
                        self.phase = "paused"
                    else:
                        observation = self.sim.observe() if self.recorder is not None else None
                        command = {**self.command, "keys": list(self.command["keys"])}
                        result = self.sim.step(command)
                        if self.recorder is not None:
                            self.recorder.append(observation, result, command, self.steps / CONTROL_HZ)
                        self.steps += 1
                        self.success = bool(result.get("success", False))
                        horizon = HORIZONS[self.mode]
                        if self.success:
                            self.phase, self.end_reason = "completed", "task_success"
                            self.clear_input()
                        elif horizon is not None and self.steps >= horizon:
                            self.phase, self.end_reason = "stopped", "time_limit"
                            self.clear_input()
                        if tick - self._last_frame_at >= 0.1 or self.phase != "recording":
                            # Display the same pre-action frame the recorder saw.
                            self._capture_frame(observation)
                            self._last_frame_at = tick
                            await self.emit(self.last_frames)
                elif self.phase == "replaying" and self._replay:
                    replay = self._replay
                    if replay["i"] < len(replay["states"]):
                        self.sim.restore(replay["states"][replay["i"]])
                        self.steps = replay["i"]
                        replay["i"] += 1
                        if tick - self._last_frame_at >= 0.1:
                            self._capture_frame()
                            self._last_frame_at = tick
                            await self.emit(self.last_frames)
                    else:
                        self.sim.restore(replay["states"][-1])
                        result = self.sim.step_native(replay["actions"][-1])
                        self.success = bool(result.get("success", False))
                        self.phase = "stopped"
                        self._replay = None
                        # A played-back final state is not a new collection start.
                        # Restore the selected fresh scene before enabling Start.
                        self.reset(self.task_id, self.seed, "practice")
                        self._capture_frame()
                        await self.emit(self.last_frames)
                if tick - self._last_status_at >= 0.2:
                    await self.emit({"type": "status", **self.status()})
                    self._last_status_at = tick
                self.last_step_wall_s = time.monotonic() - tick
            except Exception as exc:
                LOG.exception("Simulation loop failed")
                self.phase, self.error = "error", str(exc)
                self.clear_input()
                await self.emit({"type": "status", **self.status()})
            await asyncio.sleep(max(0.001, 1 / CONTROL_HZ - (time.monotonic() - tick)))

    def start(self) -> None:
        if self.phase != "ready" or self.sim is None:
            raise ValueError("Reset the scene before starting a new episode")
        self.steps, self.success = 0, False
        self.input_at = time.monotonic()
        if self.mode != "practice":
            from .recording import EpisodeRecorder
            metadata = self.sim.metadata()
            metadata["operator"] = {"source": "human", "device": "keyboard", "player_id": self.owner}
            metadata["collection_horizon"] = HORIZONS[self.mode]
            metadata["assistance"] = False
            self.recorder = EpisodeRecorder(
                EPISODES, metadata, self.task_id, self.seed, mode=self.mode,
            )
            self.episode_id = str(self.recorder.episode_id)
        self.phase = "recording"

    async def submit(self) -> dict:
        if self._finalizing is not None:
            return await asyncio.shield(self._finalizing)
        if self.recorder is None:
            if self.last_submission and self.phase == "submitted":
                return self.last_submission
            raise ValueError("There is no recorded episode to submit")
        if self.phase not in {"recording", "paused", "completed", "stopped", "error"}:
            raise ValueError("Episode cannot be submitted in this state")
        recorder = self.recorder
        self.clear_input()
        self.phase = "submitting"
        await self.emit({"type": "status", **self.status()})

        async def finish() -> dict:
            try:
                result = recorder.finalize(self.end_reason)
                if result.get("status") == "empty":
                    self.recorder = None
                    self.reset(self.task_id, self.seed, self.mode)
                    self.error = "No control steps were recorded; no dataset was created."
                    await self.emit({"type": "status", **self.status()})
                    return result
                if result.get("path") and result.get("steps", 0):
                    from .replay import check_episode
                    from .recording import set_replay_qa
                    replay = check_episode(result["path"], simulator=self.sim)
                    result["qa"] = set_replay_qa(result["path"], replay)
                self.last_submission = public_episode(result)
                self.recorder = None
                self.phase = "submitted"
                self.error = None
                await self.emit({"type": "status", **self.status()})
                return self.last_submission
            except Exception as exc:
                self.phase, self.error = "stopped", f"Save failed: {exc}"
                raise
            finally:
                self._finalizing = None

        self._finalizing = asyncio.create_task(finish())
        return await asyncio.shield(self._finalizing)

    async def close(self) -> None:
        self._running = False
        if self._task:
            await self._task
        if self._finalizing:
            await asyncio.shield(self._finalizing)
        if self.recorder is not None:
            with contextlib.suppress(Exception):
                self.recorder.finalize("server_shutdown")
        if self.sim is not None:
            self.sim.close()


def encode_jpeg(frame: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(buffer, format="JPEG", quality=82)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def public_episode(record: dict) -> dict:
    result = dict(record)
    episode_id = result.get("episode_id") or result.get("id")
    if episode_id and result.get("status") != "empty":
        result["episode_id"] = episode_id
        result["download_url"] = f"/api/episodes/{episode_id}/download"
        result["replay_url"] = f"/api/episodes/{episode_id}/replay"
    return result


def episode_dir(episode_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", episode_id):
        raise HTTPException(400, "Invalid episode ID")
    path = EPISODES / episode_id
    if not path.is_dir():
        raise HTTPException(404, "Episode not found")
    return path


game = LocalGame()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    EPISODES.mkdir(parents=True, exist_ok=True)
    game._task = asyncio.create_task(game.run())
    yield
    await game.close()


app = FastAPI(title="Coffee Lab · Game 1016", lifespan=lifespan)
install_access_gate(app)
app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")


@app.get("/")
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
async def status():
    return game.status()


@app.get("/api/episodes")
async def episodes():
    from .recording import list_episodes
    return {"episodes": [public_episode(r) for r in list_episodes(EPISODES)]}


@app.post("/api/reset")
async def reset(request: Request):
    game.authorize(request)
    values = await request.json()
    if game.phase == "recording":
        raise HTTPException(409, "Pause and submit or discard the current episode first")
    try:
        seed = int(values.get("seed", 0))
        if not 0 <= seed < 2**32:
            raise ValueError("Seed must be between 0 and 4294967295")
        game.reset(values.get("task_id", game.task_id), seed, values.get("mode", game.mode))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        game.phase, game.error = "error", str(exc)
        raise HTTPException(500, str(exc)) from exc
    await game.emit(game.last_frames)
    return game.status()


@app.post("/api/start")
async def start(request: Request):
    game.authorize(request)
    try:
        game.start()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return game.status()


@app.post("/api/pause")
async def pause(request: Request):
    game.authorize(request)
    game.clear_input()
    if game.phase == "recording":
        game.phase = "paused"
    return game.status()


@app.post("/api/resume")
async def resume(request: Request):
    game.authorize(request)
    if game.phase != "paused":
        raise HTTPException(409, "Only paused episodes can resume")
    game.clear_input()
    game.input_at = time.monotonic()
    game.phase = "recording"
    return game.status()


@app.post("/api/submit")
async def submit(request: Request):
    game.authorize(request)
    try:
        return await game.submit()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/discard")
async def discard(request: Request):
    game.authorize(request)
    if game.phase == "submitting":
        raise HTTPException(409, "An episode is being saved")
    game.clear_input()
    if game.recorder is not None:
        # Preserve an interrupted attempt, explicitly excluded from positive training.
        game.end_reason = "discarded"
        await game.submit()
    game.reset(game.task_id, game.seed, game.mode)
    await game.emit(game.last_frames)
    return game.status()


@app.post("/api/view")
async def view(request: Request):
    game.authorize(request)
    values = await request.json()
    selected = values.get("view", "overview")
    if selected not in {"overview", "left", "right", "wrist"}:
        raise HTTPException(400, "Invalid camera")
    game.view = selected
    game._capture_frame()
    await game.emit(game.last_frames)
    return game.status()


@app.get("/api/episodes/{episode_id}/download")
async def download(episode_id: str):
    path = episode_dir(episode_id)
    archives = list(path.glob("*.zip"))
    if not archives:
        sibling = path.with_suffix(".zip")
        if sibling.is_file():
            archives = [sibling]
    if not archives:
        raise HTTPException(404, "Episode archive is not available")
    return FileResponse(archives[0], filename=f"{episode_id}.zip", media_type="application/zip")


@app.get("/api/episodes/{episode_id}/report")
async def report(episode_id: str):
    path = episode_dir(episode_id) / "qa" / "report.json"
    if not path.is_file():
        raise HTTPException(404, "QA report is not available")
    return JSONResponse(json.loads(path.read_text()))


@app.post("/api/replay")
async def replay(request: Request):
    game.authorize(request)
    if game.recorder is not None or game.phase in {"recording", "submitting", "replaying"}:
        raise HTTPException(409, "Finish the active episode before replay")
    values = await request.json()
    path = episode_dir(values.get("episode_id", ""))
    # This reader is completed against the recorder's actual schema during integration.
    from .recording import read_replay
    replay_data = read_replay(path)
    game.reset(replay_data["task_id"], int(replay_data["seed"]), "practice")
    game._replay = {"states": replay_data["native_states"], "actions": replay_data["native_actions"], "i": 0}
    if not len(game._replay["states"]):
        game._replay = None
        raise HTTPException(400, "Empty episodes cannot be replayed")
    game.phase = "replaying"
    return game.status()


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    if not authorized_websocket(ws):
        await ws.close(code=1008)
        return
    client = ws.query_params.get("client_id", "")
    await ws.accept()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", client):
        await ws.send_json({"type": "error", "message": "A player tab ID is required"})
        await ws.close(code=1008)
        return
    if game.socket is not None:
        await ws.send_json({"type": "error", "message": "This local simulation is already in use by another tab.", "locked": True})
        await ws.close(code=1013)
        return
    game.socket, game.owner = ws, client
    game.input_at = time.monotonic()
    try:
        await ws.send_json({"type": "status", **game.status()})
        if game.last_frames:
            await ws.send_json(game.last_frames)
        while True:
            payload = await ws.receive_json()
            if payload.get("type") == "input":
                if not isinstance(payload.get("keys", []), list):
                    continue
                game.command = {
                    "keys": [k for k in payload.get("keys", []) if isinstance(k, str) and k in MOVEMENT_KEYS],
                    "mode": "base" if payload.get("mode") == "base" else "arm",
                    "gripper": bool(payload.get("gripper", False)),
                    "fine": bool(payload.get("fine", False)),
                }
                game.input_at = time.monotonic()
            elif payload.get("type") == "pause":
                game.clear_input()
                if game.phase == "recording":
                    game.phase = "paused"
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        if game.socket is ws:
            game.socket, game.owner = None, None
            game.clear_input()
            if game.phase == "recording":
                game.phase = "paused"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Kitchen24 coffee game")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8786)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, access_log=not bool(os.environ.get("COFFEE_ACCESS_TOKEN")))


if __name__ == "__main__":
    main()
