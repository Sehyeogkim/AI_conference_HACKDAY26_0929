"""Measure short continuous native-action replay in the original coffee task."""
from pathlib import Path
import gc
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MUJOCO_GL', 'cgl' if sys.platform == 'darwin' else 'egl')
if sys.platform != 'darwin':
    os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')

import numpy as np
from coffee_game.simulator import CoffeeSimulator

sim = CoffeeSimulator('CoffeeSetupMug', seed=0, split='train')
try:
    states, actions, success = [], [], []
    commands = [{}, {'keys': ['KeyR']}, {'keys': ['KeyA']}, {'keys': ['KeyF'], 'fine': True},
        {'gripper': True}, {'gripper': True}, {'mode': 'base', 'keys': ['KeyW']},
        {'mode': 'base', 'keys': ['KeyQ']}, {}, {'keys': ['KeyS']}, {}, {}]
    for command in commands:
        states.append(sim.native_state())
        result = sim.step(command)
        actions.append(result['native_action'])
        success.append(bool(result['success']))
    sim.restore(states[0])
    qpos_slice = slice(1, 1 + sim.env.sim.model.nq)
    errors, replay_success = [], []
    for expected, action in zip(states, actions):
        errors.append(float(np.max(np.abs(sim.native_state()[qpos_slice] - expected[qpos_slice]))))
        replay_success.append(bool(sim.step_native(action)['success']))
    report = dict(task_id='CoffeeSetupMug', steps=len(actions),
        method='Restore first integration state; continuously execute recorded native actions',
        qpos_tolerance=1e-3, max_qpos_abs_error=max(errors), success_matches=success == replay_success,
        recorded_any_task_success=any(success), replay_any_task_success=any(replay_success))
    sim.env.sim._render_context_offscreen.gl_ctx.make_current()
    from OpenGL.GL import glGetString, GL_RENDERER, GL_VENDOR
    renderer, vendor = glGetString(GL_RENDERER), glGetString(GL_VENDOR)
    report['gl_renderer'] = renderer.decode() if renderer else 'unavailable via PyOpenGL'
    report['gl_vendor'] = vendor.decode() if vendor else 'unavailable via PyOpenGL'
    report['passed'] = report['max_qpos_abs_error'] <= report['qpos_tolerance'] and report['success_matches']
    (ROOT / 'logs').mkdir(exist_ok=True)
    (ROOT / 'logs/replay-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise SystemExit(1)
finally:
    sim.close()
    del sim
    gc.collect()
