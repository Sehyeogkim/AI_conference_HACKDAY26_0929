"""Atomic, streamed recordings and a standalone LeRobot v2.1 dataset per episode.

Observations are captured BEFORE each executed action. Native MuJoCo states and
actions are kept separately from the policy's 53-state / 12-action representation.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any
import uuid
import zipfile

import h5py
import numpy as np

FPS = 20
CAMERAS = {"left": "left_view", "right": "right_view", "wrist": "wrist_view"}
STATE_FIELDS = {
    "base_position": (0, 3), "base_rotation": (3, 7),
    "end_effector_position_absolute": (7, 10), "end_effector_position_relative": (10, 13),
    "end_effector_rotation_absolute": (13, 17), "end_effector_rotation_relative": (17, 21),
    "gripper_qpos": (21, 23), "gripper_qvel": (23, 25), "joint_position": (25, 32),
    "joint_position_cos": (32, 39), "joint_position_sin": (39, 46), "joint_velocity": (46, 53),
}
ACTION_FIELDS = {"base_motion": (0, 4), "control_mode": (4, 5),
                 "end_effector_position": (5, 8), "end_effector_rotation": (8, 11), "gripper_close": (11, 12)}
TASKS = {
    "CoffeeSetupMug": "Place the mug below the dispenser.",
    "CoffeeServeMug": "Move the mug onto the counter.",
    "CoffeePressButton": "Activate the coffee machine.",
}


def _json_default(value: Any):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=_json_default, allow_nan=False) + "\n")
    os.replace(temporary, path)


def _jsonl(path: Path, values: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v, default=_json_default, allow_nan=False) + "\n" for v in values))


def _vector(value: Any, size: int | None, field: str):
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 1 or not len(array) or (size is not None and array.shape != (size,)):
        raise ValueError(f"{field} must have shape ({size or 'N'},), got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"{field} contains non-finite values")
    return array


class EpisodeRecorder:
    def __init__(self, root, metadata, task_id, seed, mode="collection", episode_id=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.episode_id = episode_id or f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.episode_id):
            raise ValueError("Invalid episode ID")
        self.path = self.root / self.episode_id
        self.temporary = self.root / f".{self.episode_id}.partial"
        if self.path.exists() or self.temporary.exists():
            raise FileExistsError(f"Episode already exists: {self.episode_id}")
        self.metadata = dict(metadata)
        self.metadata.update(episode_id=self.episode_id, task_id=task_id, seed=seed,
                             mode=mode, fps=FPS, schema_version="game1016.recording.v1",
                             observation_timing="pre-action", created_at=time.time())
        self.task_id = task_id
        self.steps = 0
        self.success = False
        self._closed = False
        self._result = None
        self._writers = {}
        self._last_timestamp = None
        self._h5 = None
        (self.temporary / "raw").mkdir(parents=True)
        (self.temporary / "replay").mkdir()
        _json(self.temporary / "metadata.json", self.metadata)
        model_xml = self.metadata.get("model_xml", self.metadata.get("xml"))
        if model_xml:
            (self.temporary / "replay/model.xml").write_text(model_xml)
        _json(self.temporary / "replay/environment.json", {
            key: value for key, value in self.metadata.items() if key not in ("model_xml", "xml")})

    def _open(self, native_state_size, native_action_size):
        self._h5 = h5py.File(self.temporary / "raw/episode.hdf5", "w")
        self._h5.attrs["metadata"] = json.dumps(self.metadata, default=_json_default)
        self._h5.attrs["observation_timing"] = "pre-action"
        self._h5.attrs["native_state_kind"] = self.metadata.get("native_state_kind", "robosuite.sim.get_state.flatten")
        self._h5.attrs["env_info"] = json.dumps(self.metadata.get("env_info", {}), default=_json_default)
        self._h5.attrs["ep_meta"] = json.dumps(self.metadata.get("ep_meta", {}), default=_json_default)
        # The native state/action widths can differ between simulator revisions.
        for key, width in (("state", 53), ("action", 12), ("native_state", native_state_size),
                           ("native_action", native_action_size)):
            self._h5.create_dataset(key, shape=(0, width), maxshape=(None, width), dtype="f8", chunks=(1, width), compression="gzip")
        for key, dtype in (("timestamp", "f8"), ("wall_timestamp", "f8"), ("reward", "f8"),
                           ("success", "?"), ("done", "?")):
            self._h5.create_dataset(key, shape=(0,), maxshape=(None,), dtype=dtype, chunks=True)
        self._h5.create_dataset("input_state", shape=(0,), maxshape=(None,), dtype=h5py.string_dtype())
        import imageio.v2 as imageio
        for short, name in CAMERAS.items():
            self._h5.create_dataset(f"images/{name}", shape=(0, 256, 256, 3), maxshape=(None, 256, 256, 3),
                                    chunks=(1, 256, 256, 3), dtype="u1", compression="gzip", compression_opts=1)
            video = self.temporary / f"lerobot/videos/chunk-000/observation.images.{name}/episode_000000.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            self._writers[short] = imageio.get_writer(str(video), fps=FPS, codec="libx264", pixelformat="yuv420p",
                                                     macro_block_size=None, quality=8, ffmpeg_log_level="error")

    def append(self, observation, result, input_state, timestamp):
        if self._closed:
            raise RuntimeError("Recording is already closed")
        state = _vector(observation["state"], 53, "state")
        native_state = _vector(observation["native_state"], None, "native_state")
        action = _vector(result["action"], 12, "action")
        native_action = _vector(result["native_action"], None, "native_action")
        timestamp = float(timestamp)
        if not math.isfinite(timestamp) or (self._last_timestamp is not None and not math.isclose(timestamp - self._last_timestamp, 1/FPS, abs_tol=1e-6)):
            raise ValueError("Simulation timestamps must advance exactly 1/20 second per recorded action")
        images = {}
        for short, name in CAMERAS.items():
            pixels = np.asarray(observation["images"].get(short, observation["images"].get(name)))
            if pixels.shape != (256, 256, 3) or pixels.dtype != np.uint8:
                raise ValueError(f"{name}: expected RGB uint8 (256,256,3), got {pixels.dtype} {pixels.shape}")
            images[short] = pixels
        if not np.isfinite(float(result.get("reward", 0))):
            raise ValueError("reward is not finite")
        if self._h5 is None:
            self._open(len(native_state), len(native_action))
        if self._h5["native_state"].shape[1] != len(native_state) or self._h5["native_action"].shape[1] != len(native_action):
            raise ValueError("Native state or action width changed within an episode")
        # Encoding errors abort collection, preserving the .partial raw file for diagnosis.
        for short, pixels in images.items():
            self._writers[short].append_data(pixels)
        values = {"state": state, "native_state": native_state, "action": action, "native_action": native_action,
                  "timestamp": timestamp, "wall_timestamp": time.time(), "reward": float(result.get("reward", 0)),
                  "success": bool(result.get("success", False)), "done": bool(result.get("done", False)),
                  "input_state": json.dumps(input_state, default=_json_default)}
        values.update({f"images/{CAMERAS[k]}": image for k, image in images.items()})
        for key, value in values.items():
            dataset = self._h5[key]
            dataset.resize(self.steps + 1, axis=0)
            dataset[self.steps] = value
        self.steps += 1
        self.success = self.success or bool(result.get("success", False))
        self._last_timestamp = timestamp
        if self.steps % FPS == 0:
            self._h5.flush()

    def _close(self):
        errors = []
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception as exc:
                errors.append(str(exc))
        self._writers.clear()
        if self._h5 is not None:
            self._h5.flush()
            self._h5.close()
            self._h5 = None
        self._closed = True
        if errors:
            raise RuntimeError("Video encoding failed: " + "; ".join(errors))

    def finalize(self, end_reason):
        if self._result is not None:
            return self._result
        if self.path.exists():
            if not (self.root / f"{self.episode_id}.zip").exists():
                _archive_episode(self.path)
            self._result = json.loads((self.path / "submission.json").read_text())
            return self._result
        if self.steps == 0:
            self._close()
            shutil.rmtree(self.temporary, ignore_errors=True)
            self._result = {"episode_id": self.episode_id, "status": "empty", "steps": 0,
                            "success": False, "path": None, "paths": {}, "archive": None,
                            "task_id": self.task_id, "mode": self.metadata["mode"],
                            "created_at": self.metadata["created_at"], "end_reason": str(end_reason),
                            "qa": {"structural_valid": False, "training_candidate": False,
                                   "errors": ["No control steps were recorded; no dataset was submitted."]}}
            return self._result
        self._close()
        self.metadata.update(steps=self.steps, success=self.success, end_reason=str(end_reason),
                             finished_at=time.time())
        _json(self.temporary / "metadata.json", self.metadata)
        export_episode(self.temporary)
        report = inspect_episode(self.temporary)
        _json(self.temporary / "qa/report.json", report)
        paths = {"raw": "raw/episode.hdf5", "dataset": "lerobot", "qa": "qa/report.json", "metadata": "metadata.json"}
        result = {"episode_id": self.episode_id, "status": "submitted" if report["structural_valid"] else "qa_failed",
                        "steps": self.steps, "success": self.success, "path": str(self.path),
                        "task_id": self.task_id, "mode": self.metadata["mode"],
                        "created_at": self.metadata["created_at"], "end_reason": str(end_reason),
                        "paths": paths, "qa": report, "archive": str(self.root / f"{self.episode_id}.zip")}
        _json(self.temporary / "submission.json", result)
        os.replace(self.temporary, self.path)
        _archive_episode(self.path)
        self._result = result
        return self._result

    def discard(self):
        self._close()
        if self.temporary.exists():
            shutil.rmtree(self.temporary)


def _numeric_stats(array):
    array = np.asarray(array, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, None]
    return {"min": array.min(axis=0).tolist(), "max": array.max(axis=0).tolist(),
            "mean": array.mean(axis=0).tolist(), "std": array.std(axis=0).tolist(),
            "q01": np.quantile(array, 0.01, axis=0).tolist(),
            "q99": np.quantile(array, 0.99, axis=0).tolist(), "count": [len(array)]}


def _training_eligibility(metadata, success):
    """Fail closed for provenance, independently of structural and replay QA."""
    reasons = []
    if not success:
        reasons.append("Native task success was not observed.")
    if metadata.get("end_reason") != "task_success":
        reasons.append(f"End reason {metadata.get('end_reason')!r} is not task_success.")
    if metadata.get("mode") != "collection":
        reasons.append(f"Mode {metadata.get('mode')!r} is not collection.")
    if metadata.get("split") != "train":
        reasons.append(f"Split {metadata.get('split')!r} is not train.")
    return not reasons, reasons


def export_episode(path):
    """Export one closed raw episode, with dataset-local episode and task index zero."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = Path(path)
    metadata = json.loads((path / "metadata.json").read_text())
    instruction = metadata.get("instruction", TASKS.get(metadata["task_id"], metadata["task_id"]))
    training_eligible, eligibility_reasons = _training_eligibility(metadata, metadata.get("success", False))
    with h5py.File(path / "raw/episode.hdf5", "r") as raw:
        count = len(raw["timestamp"])
        if not count:
            raise ValueError("Cannot export an empty episode")
        timestamp = raw["timestamp"][:] - raw["timestamp"][0]
        values = {"observation.state": raw["state"][:], "action": raw["action"][:],
                  "timestamp": timestamp, "frame_index": np.arange(count, dtype=np.int64),
                  "episode_index": np.zeros(count, dtype=np.int64), "index": np.arange(count, dtype=np.int64),
                  "task_index": np.zeros(count, dtype=np.int64), "annotation.human.action.task_description": np.zeros(count, dtype=np.int64),
                  "annotation.human.action.task_name": np.zeros(count, dtype=np.int64),
                  "annotation.human.validity": np.full(count, int(training_eligible), dtype=np.int64),
                  "next.reward": raw["reward"][:], "next.done": raw["done"][:], "next.success": raw["success"][:]}
        values["next.done"][-1] = True
        columns = {key: pa.array(value.tolist(), type=pa.list_(pa.float64(), value.shape[1])) if value.ndim == 2
                   else pa.array(value) for key, value in values.items()}
        destination = path / "lerobot/data/chunk-000/episode_000000.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table(columns), destination, compression="zstd")
        features = {}
        stats = {}
        for key, array in values.items():
            vector = array.ndim == 2
            features[key] = {"dtype": str(array.dtype), "shape": [array.shape[1]] if vector else [1],
                             "names": _feature_names(key) if vector else None}
            stats[key] = _numeric_stats(array)
        for short, name in CAMERAS.items():
            key = f"observation.images.{name}"
            features[key] = {"dtype": "video", "shape": [256, 256, 3], "names": ["height", "width", "channel"],
                             "video_info": {"video.fps": FPS, "video.codec": "h264", "video.pix_fmt": "yuv420p", "video.is_depth_map": False, "has_audio": False}}
            # LeRobot v2.1 calls this field "info"; retain the reference's old
            # "video_info" alias for consumers of its v2.0-style metadata.
            features[key]["info"] = dict(features[key]["video_info"])
            # Stream image statistics instead of loading a full episode into RAM.
            total = np.zeros(3); squared = np.zeros(3); low = np.ones(3); high = np.zeros(3)
            for pixels in raw[f"images/{name}"]:
                normalized = pixels.astype(np.float64).reshape(-1, 3) / 255
                total += normalized.sum(axis=0); squared += (normalized ** 2).sum(axis=0)
                low = np.minimum(low, normalized.min(axis=0)); high = np.maximum(high, normalized.max(axis=0))
            mean = total / (count*256*256)
            std = np.sqrt(np.maximum(0, squared/(count*256*256)-mean**2))
            stats[key] = {key_: np.asarray(value).reshape(3, 1, 1).tolist() for key_, value in
                          (("min", low), ("max", high), ("mean", mean), ("std", std))}
            stats[key]["count"] = [count]
    meta = path / "lerobot/meta"
    _json(meta / "info.json", {"codebase_version": "v2.1", "robot_type": "Panda+OmronBase", "total_episodes": 1,
           "total_frames": count, "total_tasks": 1, "total_videos": 3, "total_chunks": 1, "chunks_size": 1000,
           "fps": FPS, "splits": {"train": "0:1"}, "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
           "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4", "features": features})
    _json(meta / "modality.json", _modality())
    _jsonl(meta / "tasks.jsonl", [{"task_index": 0, "task": instruction}])
    _jsonl(meta / "episodes.jsonl", [{"episode_index": 0, "tasks": [instruction], "length": count}])
    _json(meta / "stats.json", stats)
    _jsonl(meta / "episodes_stats.jsonl", [{"episode_index": 0, "stats": stats}])
    _json(meta / "provenance.json", {**{key: value for key, value in metadata.items() if key not in ("model_xml", "xml")},
          "dataset_local_episode_index": 0, "dataset_local_task_index": 0,
          "training_eligible": training_eligible, "training_eligibility_reasons": eligibility_reasons,
          "annotation_validity": "1 only for native success with end_reason task_success, mode collection and split train; structural and replay QA remain separate required gates",
          "next_done": "true on simulator termination or final submitted frame; end_reason is recorded separately"})


def _feature_names(key):
    fields = STATE_FIELDS if key == "observation.state" else ACTION_FIELDS
    return [f"{name}_{index}" for name, (start, end) in fields.items() for index in range(end-start)]


def _modality():
    state = {key: {"start": start, "end": end} for key, (start, end) in STATE_FIELDS.items()}
    action = {key: {"start": start, "end": end, "absolute": False} for key, (start, end) in ACTION_FIELDS.items()}
    for name in ("base_rotation", "end_effector_rotation_absolute", "end_effector_rotation_relative"):
        state[name]["rotation_type"] = "quaternion"
    action["end_effector_rotation"]["rotation_type"] = "axis_angle"
    for name in ("control_mode", "gripper_close"):
        action[name].pop("absolute")
        action[name].update(dtype="int64", range=[0, 1])
    return {"state": state, "action": action, "video": {name: {"original_key": f"observation.images.{name}"} for name in CAMERAS.values()},
            "annotation": {"human.action.task_description": {}, "human.action.task_name": {}, "human.validity": {}}}


def inspect_episode(path):
    """Check file integrity; action replay is deliberately a separate QA gate."""
    import imageio.v2 as imageio
    import pyarrow.parquet as pq
    path = Path(path)
    if path.is_file():
        path = path.parent.parent if path.name.endswith("hdf5") else path.parent
    errors = []
    checks = {}
    count = 0
    success = False
    metadata = {}
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        with h5py.File(path / "raw/episode.hdf5", "r") as raw:
            count = len(raw["timestamp"])
            if count == 0:
                errors.append("Episode has no frames")
            for key in ("state", "action", "native_state", "native_action", "timestamp", "reward", "done", "success", "input_state"):
                if len(raw[key]) != count:
                    errors.append(f"Frame count mismatch: {key}")
            for key, width in (("state", 53), ("action", 12)):
                if raw[key].shape != (count, width):
                    errors.append(f"Wrong shape: {key}")
            for key in ("state", "action", "native_state", "native_action", "timestamp", "reward"):
                if not np.isfinite(raw[key][:]).all():
                    errors.append(f"Non-finite field: {key}")
            if count > 1 and not np.allclose(np.diff(raw["timestamp"][:]), 1/FPS, atol=1e-6):
                errors.append("Timestamp interval is not 20Hz")
            if count and not np.isin(raw["action"][:, [4, 11]], [0, 1]).all():
                errors.append("Dataset mode/gripper must use binary 0/1 encoding")
            success = bool(np.any(raw["success"][:]))
            checks["raw_schema"] = not errors
            for name in CAMERAS.values():
                if raw[f"images/{name}"].shape != (count, 256, 256, 3):
                    errors.append(f"Camera shape mismatch: {name}")
        checks["camera_raw_sync"] = not errors
        table = pq.read_table(path / "lerobot/data/chunk-000/episode_000000.parquet")
        if table.num_rows != count:
            errors.append("Parquet/raw frame count mismatch")
        checks["parquet"] = table.num_rows == count
        checks["videos"] = {}
        for name in CAMERAS.values():
            video = path / f"lerobot/videos/chunk-000/observation.images.{name}/episode_000000.mp4"
            reader = imageio.get_reader(str(video))
            try:
                frames = reader.count_frames()
                video_metadata = reader.get_meta_data()
                valid = frames == count and reader.get_data(0).shape == (256, 256, 3) and math.isclose(float(video_metadata["fps"]), FPS)
                checks["videos"][name] = {"frames": frames, "fps": video_metadata.get("fps"), "valid": valid}
                if not valid:
                    errors.append(f"Encoded video is not synchronized: {name}")
            finally:
                reader.close()
        checks["replay_model"] = (path / "replay/model.xml").is_file()
        if not checks["replay_model"]:
            errors.append("Missing replay model XML")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    existing_replay = {}
    report_path = path / "qa/report.json"
    if report_path.exists():
        existing_replay = json.loads(report_path.read_text()).get("action_replay", {})
    replay = existing_replay or {"status": "not_run", "passed": None, "reason": "Structural validation does not execute the simulator."}
    training_eligible, eligibility_reasons = _training_eligibility(metadata, success)
    return {"schema_version": "game1016.qa.v1", "checked_at": time.time(), "steps": count,
            "structural_valid": not errors, "checks": checks, "errors": errors,
            "native_task_success_observed": success, "action_replay": replay,
            "training_eligible": training_eligible, "training_eligibility_reasons": eligibility_reasons,
            "training_candidate": bool(not errors and training_eligible and replay.get("passed") is True),
            "learning_improvement": "not_evaluated"}


def _archive_episode(path):
    destination = path.parent / f"{path.name}.zip"
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for child in sorted(path.rglob("*")):
            if child.is_file():
                archive.write(child, Path(path.name) / child.relative_to(path))
    os.replace(temporary, destination)


def set_replay_qa(path, report):
    """Called only by a simulator replay implementation after an actual replay."""
    path = Path(path)
    qa = inspect_episode(path)
    qa["action_replay"] = report
    qa["training_candidate"] = bool(qa["structural_valid"] and qa["training_eligible"] and report.get("passed") is True)
    _json(path / "qa/report.json", qa)
    submission_path = path / "submission.json"
    if submission_path.exists():
        submission = json.loads(submission_path.read_text())
        submission["qa"] = qa
        _json(submission_path, submission)
    _archive_episode(path)
    return qa


def list_episodes(root):
    root = Path(root)
    if not root.exists():
        return []
    episodes = []
    for manifest in root.glob("*/submission.json"):
        try:
            episodes.append(json.loads(manifest.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(episodes, key=lambda item: item["episode_id"], reverse=True)


def read_replay(path):
    """Read raw pre-action states and executed native actions for simulator replay."""
    path = Path(path)
    raw_path = path if path.is_file() else path / "raw/episode.hdf5"
    episode_path = raw_path.parent.parent
    with h5py.File(raw_path, "r") as raw:
        metadata = json.loads(raw.attrs["metadata"])
        manifest = episode_path / "metadata.json"
        if manifest.exists():
            metadata.update(json.loads(manifest.read_text()))
        return {"task_id": metadata["task_id"], "seed": metadata["seed"],
                "native_states": raw["native_state"][:], "native_actions": raw["native_action"][:],
                "timestamps": raw["timestamp"][:], "success": raw["success"][:], "metadata": metadata}
