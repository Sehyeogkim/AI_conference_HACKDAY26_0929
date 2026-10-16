"""Native RLWRLD Kitchen24 coffee environment; no simulated gameplay shortcuts.

All methods must run on the thread which created the MuJoCo renderer.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import threading

import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[1]
TASKS = ("CoffeeSetupMug", "CoffeeServeMug", "CoffeePressButton")
CAMERAS = {"left": "robot0_agentview_left", "right": "robot0_agentview_right", "wrist": "robot0_eye_in_hand"}
STATE_FIELDS = (
    ("base_position", 3), ("base_rotation", 4),
    ("end_effector_position_absolute", 3), ("end_effector_position_relative", 3),
    ("end_effector_rotation_absolute", 4), ("end_effector_rotation_relative", 4),
    ("gripper_qpos", 2), ("gripper_qvel", 2), ("joint_position", 7),
    ("joint_position_cos", 7), ("joint_position_sin", 7), ("joint_velocity", 7),
)
ACTION_FIELDS = (("base_motion", 4), ("control_mode", 1), ("end_effector_position", 3), ("end_effector_rotation", 3), ("gripper_close", 1))


def _spec(fields):
    result, offset = {}, 0
    for name, width in fields:
        result[name] = {"start": offset, "end": offset + width}
        offset += width
    return result


def _flatten(mapped, fields):
    values = []
    for name, width in fields:
        prefix = "hand." if name.startswith("gripper") else "body."
        value = np.atleast_1d(mapped[prefix + name]).astype(np.float32)
        if value.shape != (width,):
            raise ValueError(f"Unexpected shape for {name}: {value.shape}; expected {(width,)}")
        values.append(value)
    return np.concatenate(values)


class CoffeeSimulator:
    def __init__(self, task_id="CoffeeSetupMug", seed=0, split="train"):
        if task_id not in TASKS:
            raise ValueError(f"Unsupported task: {task_id}")
        if split not in ("train", "eval"):
            raise ValueError("split must be train or eval")
        # robosuite selects its platform renderer during import.
        if platform.system() == "Darwin":
            os.environ.setdefault("MUJOCO_GL", "cgl")
        import mujoco
        import robosuite
        import robocasa  # noqa: F401 registers the original environments
        from robosuite.controllers import load_composite_controller_config
        from robocasa.models.robots import PandaOmronKeyConverter, reconstruct_latest_actions

        self._mujoco = mujoco
        self.converter = PandaOmronKeyConverter
        self._reconstruct = reconstruct_latest_actions
        self._thread = threading.get_ident()
        self.task_id, self.seed, self.split = task_id, int(seed), split
        self.steps = 0
        self.controller = load_composite_controller_config(controller=None, robot="PandaOmron")
        # Original wrapper hardcodes B and the eval layouts. Native make lets us
        # reserve those exact objects/layout-style pairs for held-out evaluation.
        layouts = [[0, 0], [3, 3], [5, 5]] if split == "train" else [[1, 1], [2, 2], [4, 4], [6, 9], [7, 10]]
        self.env_info = dict(
            env_name=task_id, robots="PandaOmron", controller_configs=self.controller,
            camera_names=list(CAMERAS.values()), camera_widths=512, camera_heights=512,
            has_renderer=False, has_offscreen_renderer=True, ignore_done=True,
            use_object_obs=True, use_camera_obs=True, camera_depths=False,
            seed=self.seed, translucent_robot=False, obj_instance_split="A" if split == "train" else "B",
            generative_textures=None, randomize_cameras=False, layout_ids=None,
            style_ids=None, layout_and_style_ids=layouts, control_freq=20, horizon=720,
        )
        self.env = robosuite.make(**self.env_info)
        self._raw_obs = self.env.reset()
        self._initial_controller_goals = {
            name: {key: np.asarray(getattr(part, key)).copy()
                   for key in ("goal_pos", "goal_ori", "goal_qpos", "goal_qvel")
                   if getattr(part, key, None) is not None}
            for name, part in self.env.robots[0].composite_controller.part_controllers.items()
        }
        self._state_type = mujoco.mjtState.mjSTATE_INTEGRATION
        self._physics_state_size = mujoco.mj_stateSize(self.env.sim.model._model, self._state_type)
        self._cached_observation = None

    def _check_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("MuJoCo simulator and renderer must stay on their owner thread")

    def _native_state(self):
        state = np.empty(self._physics_state_size, dtype=np.float64)
        self._mujoco.mj_getState(self.env.sim.model._model, self.env.sim.data._data, state, self._state_type)
        # CoffeeMachine's latched button state is Python state, outside MjData.
        return np.concatenate((state, [float(self.env.coffee_machine.get_state()["turned_on"]), self.steps]))

    def native_state(self):
        """Current replay state without rendering or refreshing observations."""
        self._check_thread()
        return self._native_state()

    def observe(self):
        self._check_thread()
        if self._cached_observation is None:
            raw = self._raw_obs
            state = _flatten(self.converter.map_obs(raw), STATE_FIELDS)
            images = {
                key: cv2.resize(np.ascontiguousarray(raw[name + "_image"][::-1]), (256, 256), interpolation=cv2.INTER_AREA)
                for key, name in CAMERAS.items()
            }
            self._cached_observation = dict(state=state, native_state=self._native_state(), images=images,
                timestamp=float(self.env.sim.data.time), frame_index=self.steps,
                success=bool(self.env._check_success()))
        return self._cached_observation

    def action_from_command(self, command):
        keys = set(command.get("keys", []))
        fine = bool(command.get("fine", False))
        amount = 0.08 if fine else 0.35
        axis = lambda positive, negative: amount * (int(positive in keys) - int(negative in keys))
        base_mode = command.get("mode", "arm") == "base"
        eef = np.zeros(6)
        base = np.zeros(4)
        if base_mode:
            base[:] = [axis("KeyW", "KeyS"), axis("KeyA", "KeyD"), axis("KeyQ", "KeyE"), axis("KeyR", "KeyF")]
        else:
            eef[:] = [axis("KeyW", "KeyS"), axis("KeyA", "KeyD"), axis("KeyR", "KeyF"), axis("KeyC", "KeyV"), axis("KeyZ", "KeyX"), axis("KeyQ", "KeyE")]
        mapped = {"action.base_motion": base, "action.control_mode": int(base_mode),
                  "action.end_effector_position": eef[:3], "action.end_effector_rotation": eef[3:],
                  "action.gripper_close": int(bool(command.get("gripper", False)))}
        named = self.converter.unmap_action(mapped)
        cc = self.env.robots[0].composite_controller
        native = np.zeros(self.env.action_dim, dtype=np.float64)
        for name, (start, end) in cc._action_split_indexes.items():
            native[start:end] = named["robot0_" + name]
        native[-1] = named["robot0_base_mode"][0]
        return native

    def step(self, command):
        return self.step_native(self.action_from_command(command))

    def step_native(self, native_action):
        self._check_thread()
        native = np.asarray(native_action, dtype=np.float64)
        if native.shape != (self.env.action_dim,) or not np.isfinite(native).all():
            raise ValueError("Native action has invalid shape or non-finite values")
        low, high = self.env.action_spec
        native = np.clip(native, low, high)
        mapped = self.converter.map_action(self._reconstruct(self.env, native))
        dataset_action = _flatten(mapped, ACTION_FIELDS)
        self._raw_obs, reward, done, _ = self.env.step(native)
        self.steps += 1
        self._cached_observation = None
        return dict(native_action=native.copy(), action=dataset_action,
                    reward=float(reward), done=bool(done), success=bool(self.env._check_success()))

    def restore(self, native_state):
        self._check_thread()
        state = np.asarray(native_state, dtype=np.float64)
        if state.shape != (self._physics_state_size + 2,):
            raise ValueError("Native state belongs to a different compiled world")
        self._mujoco.mj_setState(self.env.sim.model._model, self.env.sim.data._data, state[:-2], self._state_type)
        self.env.coffee_machine._turned_on = bool(state[-2])
        self.steps = int(state[-1])
        self.env.timestep = self.steps
        self.env.cur_time = float(self.env.sim.data.time)
        self.env.done = False
        self.env.sim.forward()
        controller = self.env.robots[0].composite_controller
        controller.update_state()
        for part in controller.part_controllers.values():
            part.update(force=True)
        controller.reset()
        if self.steps == 0:
            for name, goals in self._initial_controller_goals.items():
                for key, value in goals.items():
                    setattr(controller.part_controllers[name], key, value.copy())
            # PandaGripper accumulates the sign command in a Python-side value.
            # Fresh-episode replay must restore its reset value as well.
            for gripper in self.env.robots[0].gripper.values():
                gripper.current_action = np.zeros(gripper.dof)
        self.env._reset_observables()
        self._raw_obs = self.env._get_observations(force_update=True)
        self._cached_observation = None

    def metadata(self):
        xml = self.env.sim.model.get_xml()
        versions = {name: importlib.metadata.version(name) for name in ("mujoco", "robosuite", "robocasa", "numpy", "numba")}
        manifest = ROOT / "source-manifest.json"
        return dict(task_id=self.task_id, seed=self.seed, split=self.split, robot="PandaOmron",
            control_hz=20, model_xml=xml, model_xml_sha256=hashlib.sha256(xml.encode()).hexdigest(),
            native_state_kind="mjSTATE_INTEGRATION+coffee_turned_on+step",
            env_info=self.env_info, ep_meta=self.env.get_ep_meta(), versions=versions,
            source_manifest=json.loads(manifest.read_text()) if manifest.exists() else {},
            controller=self.controller, state_spec=_spec(STATE_FIELDS), action_spec=_spec(ACTION_FIELDS),
            initial_controller_goals={name: {key: value.tolist() for key, value in goals.items()} for name, goals in self._initial_controller_goals.items()},
            native_action_spec={**{name: list(bounds) for name, bounds in self.env.robots[0].composite_controller._action_split_indexes.items()}, "base_mode": [self.env.action_dim - 1, self.env.action_dim]},
            native_state_spec={"type": "mjSTATE_INTEGRATION+coffee_turned_on+step", "physics_size": self._physics_state_size,
                "qpos_start": 1, "qpos_end": 1 + self.env.sim.model.nq,
                "limitation": "State restore resets Python controller goals; action replay must start from a fresh identical seed/world. Includes coffee latch; other fixture Python caches are not serialized."},
            cameras={key: {"name": name, "render_size": [512, 512], "export_size": [256, 256], "flip_vertical": True, "resize": "cv2 INTER_AREA", "fovy": float(self.env.sim.model.cam_fovy[self.env.sim.model.camera_name2id(name)])} for key, name in CAMERAS.items()},
            collection_horizon=720, benchmark_horizon=720, assistance=False)

    def render_display(self, view="overview"):
        self._check_thread()
        key = view if view in CAMERAS else "left"
        name = CAMERAS[key]
        return np.ascontiguousarray(self._raw_obs[name + "_image"][::-1])

    def close(self):
        self._check_thread()
        self.env.close()
