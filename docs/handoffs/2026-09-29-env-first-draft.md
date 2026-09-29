# 2026-09-29 — environment → robot: first draft of the simulator

From Sean's environment workstream. For the teammate working on the Franka arm, and their coding agent.

## What exists

- Photoreal tomato path (World Labs Marble, from a CC BY-SA photo of a greenhouse in Crete) with a generated work cell: 22 plants, 69 tomatoes (40 ripe) on breakable stems.
- Harvest cart (made up) with forward, sideways, and turn joints, one basket, and one **MuJoCo Menagerie Franka Panda** on a post (prefix `arm0/`).
- Live MuJoCo physics in the browser (about 1.3 ms per 20 ms control step in Chrome).
- Keyboard teleop (arm W/S A/D R/F, wrist Q/E Z/C, grip Space, base I/K J/L U/O, Shift precise, click a tomato to reach it), grasp assist, harvest scoring.
- Record → download `.jsonl.gz` → replay with scrubbing and QA pass/fail.

## Where your work plugs in

1. **Arm control**: implement `ArmController` in `web/src/robot/` and pass it to `TomatoHarvestSimulation.create` (see `docs/interfaces.md` §1). The built-in IK is a placeholder you can replace.
2. **Python / MuJoCo work**: the browser generates the scene XML from `web/src/sim/sceneXml.ts`; `window.wefarm.simulation.sceneXml` in the browser console gives the exact XML. It loads the Menagerie Panda from `web/public/models/franka_emika_panda/` (pinned commit in `SOURCE.txt`).
3. **Data**: recordings store the operator command, the joint targets sent, and joint positions per step; see `docs/interfaces.md` §4.

## Known limits (MuJoCo 3.14.0 WASM)

- `MjData.eq_active` cannot be read directly; use `mj_getState`/`mj_setState` with `mjSTATE_EQ_ACTIVE` (done in `simulation.ts`).
- A `weld` to the world does not hold a free body in the WASM build; stems use `connect`.
- Menagerie's gripper is weak (about 2 N); its stiffness is raised 10× at load.

## Asks and open questions for you

- Is one arm right for now? Two arms = a second `<attach>` with prefix `arm1/` (cart generator takes the mount list).
- Does your arm work need anything Isaac-specific? If not, we stay on MuJoCo.
- Which cameras and data format does your training side want (we assumed wrist + head cameras, LeRobot export later)?
