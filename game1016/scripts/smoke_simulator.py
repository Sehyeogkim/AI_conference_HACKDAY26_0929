"""Run all three native coffee tasks and validate local render/action/state contracts."""
from pathlib import Path
import gc
import json
import os
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MUJOCO_GL', 'cgl' if sys.platform == 'darwin' else 'egl')
if sys.platform != 'darwin':
    os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')

import numpy as np
from coffee_game.simulator import CoffeeSimulator, TASKS

results = []
for task_id in TASKS:
    started = time.perf_counter()
    sim = CoffeeSimulator(task_id=task_id, seed=0, split='train')
    construction_s = time.perf_counter() - started
    try:
        observation = sim.observe()
        assert observation['state'].shape == (53,)
        assert np.isfinite(observation['state']).all()
        assert set(observation['images']) == {'left', 'right', 'wrist'}
        for image in observation['images'].values():
            assert image.shape == (256, 256, 3) and image.dtype == np.uint8
            assert float(image.std()) > 1.0, 'Rendered camera image appears blank'
        initial = sim.native_state().copy()
        tick_durations = []
        for command in ({}, {'keys': ['KeyR']}, {'keys': ['KeyF'], 'fine': True}, {'mode': 'base', 'keys': ['KeyW']}, {'gripper': True}, {}):
            before = sim.observe()
            tick = time.perf_counter()
            result = sim.step(command)
            after = sim.observe()
            tick_durations.append(time.perf_counter() - tick)
            assert result['native_action'].shape == (12,)
            assert result['action'].shape == (12,)
            assert np.isfinite(result['action']).all()
            assert result['action'][4] in (0, 1) and result['action'][11] in (0, 1)
            assert abs(after['timestamp'] - before['timestamp'] - 0.05) < 1e-7
        metadata = sim.metadata()
        final = sim.native_state().copy()
        sim.restore(initial)
        restored = sim.native_state()
        spec = metadata['native_state_spec']
        qslice = slice(spec['qpos_start'], spec['qpos_end'])
        error = float(np.max(np.abs(restored[qslice] - initial[qslice])))
        assert error < 1e-10, f'State restore changed qpos: {error}'
        assert sim.env.__class__.__module__.startswith('robocasa.')
        results.append(dict(task_id=task_id, native_class=sim.env.__class__.__module__, passed=True,
            construction_s=round(construction_s, 3), median_tick_with_render_s=round(float(np.median(tick_durations)), 4),
            cameras={k: [*v.shape] for k, v in observation['images'].items()},
            state_dim=53, action_dim=12, native_dim=12, native_state_dim=len(initial),
            clock_delta_s=0.05, qpos_restore_max_error=error,
            movement_qpos_norm=float(np.linalg.norm(final[qslice] - initial[qslice])),
            task_success_observed=bool(result['success']), split=metadata['split'],
            layout_id=metadata['ep_meta']['layout_id'], style_id=metadata['ep_meta']['style_id'], versions=metadata['versions']))
        print(json.dumps(results[-1]), flush=True)
    finally:
        sim.close()
        del sim
        gc.collect()
report = {'platform': platform.platform(), 'python': platform.python_version(), 'mujoco_gl': os.environ.get('MUJOCO_GL'), 'tasks': results, 'passed': len(results) == 3, 'scope': 'Native task reset/render/action/state smoke only; no human completion or policy learning validation'}
(ROOT / 'logs').mkdir(exist_ok=True)
(ROOT / 'logs/simulator-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
