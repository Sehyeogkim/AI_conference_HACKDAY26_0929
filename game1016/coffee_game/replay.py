"""Measured action replay for a saved episode, separate from file integrity QA."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import numpy as np

from .recording import read_replay, set_replay_qa


def check_episode(path: str | Path, simulator=None, tolerance: float = 1e-3) -> dict:
    """Replay native actions from the first saved state and measure qpos drift.

    A supplied live simulator is restored to its previous state on exit. Native
    state restoration can reset controller internals, as documented by its adapter.
    A mismatch is reported as a failed gate rather than silently passed.
    """
    data = read_replay(path)
    owned = simulator is None
    backup = None
    started = time.time()
    result = {
        "status": "failed", "passed": False, "qpos_tolerance": tolerance,
        "steps": int(len(data["native_states"])), "checked_at": started,
        "method": "first integration state + continuous native action replay",
    }
    try:
        if not len(data["native_states"]):
            raise ValueError("No actions to replay")
        if owned:
            from .simulator import CoffeeSimulator
            simulator = CoffeeSimulator(data["task_id"], int(data["seed"]), data["metadata"].get("split", "train"))
        metadata = simulator.metadata()
        expected_hash = data["metadata"].get("model_xml_sha256")
        actual_hash = metadata.get("model_xml_sha256") or hashlib.sha256(metadata["model_xml"].encode()).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise ValueError("Compiled world XML differs from the recorded episode")
        spec = metadata.get("native_state_spec", {})
        start = int(spec.get("qpos_start", 1))
        end = int(spec.get("qpos_end", start + simulator.env.sim.model.nq))
        backup = simulator.native_state().copy()
        simulator.restore(data["native_states"][0])
        maximum = 0.0
        first_bad_step = None
        observed_success = False
        for index, (expected, action) in enumerate(zip(data["native_states"], data["native_actions"])):
            actual = simulator.native_state()
            error = float(np.max(np.abs(actual[start:end] - expected[start:end])))
            maximum = max(maximum, error)
            if not np.isfinite(error):
                raise ValueError(f"Non-finite replay state at step {index}")
            if error > tolerance and first_bad_step is None:
                first_bad_step = index
            step = simulator.step_native(action)
            observed_success = observed_success or bool(step.get("success", False))
        recorded_success = bool(np.any(data["success"]))
        success_matches = observed_success == recorded_success
        passed = maximum <= tolerance and success_matches
        result.update(
            status="passed" if passed else "failed", passed=passed,
            max_qpos_abs_error=maximum, first_divergent_step=first_bad_step,
            recorded_task_success=recorded_success, replay_task_success=observed_success,
            success_matches=success_matches, world_xml_matches=True,
            controller_restore_limitation=spec.get("limitation"),
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if simulator is not None:
            if backup is not None and not owned:
                simulator.restore(backup)
            if owned:
                simulator.close()
    result["elapsed_s"] = time.time() - started
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Replay and validate a Game 1016 episode")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-3)
    args = parser.parse_args()
    result = check_episode(args.episode, tolerance=args.tolerance)
    qa = set_replay_qa(args.episode, result)
    print(json.dumps(qa, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
