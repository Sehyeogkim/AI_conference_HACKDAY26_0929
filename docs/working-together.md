# Working together without merge surprises

Shared guide for both workstreams (environment and robot) and their coding agents. Anyone may edit it. The short rules are in [`../AGENTS.md`](../AGENTS.md); this file explains the flow.

## Branches

```text
main ─────────●──────────────●──────────────●─────►   always runs; merged work only
               \            / \            /
env/simulator   ●──●──●────●   \          /           environment work (Sean's agents)
                                \        /
robot/<topic>                    ●──●───●             robot work (teammate's agents)
```

- `main` must always start and let you pick a tomato. Merge into it only work that passes the check below.
- Work on your own branch. **Merge `main` into your branch** (not the other way round) at least daily and before opening a pull request, so conflicts show up on your side, in small pieces.
- One pull request per coherent change, with a handoff note if it touches a seam.

## How environment updates stay safe for robot code

The environment will keep changing (new worlds, better plants, cameras, UI). These rules keep robot code working across those changes:

| Rule | Example |
| --- | --- |
| **The seams in [`interfaces.md`](interfaces.md) change only on purpose.** Everything else is free to change. | Rendering, plants, UI, and world loading can be rewritten without telling anyone; `ArmController`, `OperatorCommand`, scene names, and the recording format cannot. |
| **Add, don't break.** New fields are optional with defaults; names are never reused for a different meaning. | A second arm is added as `arm1/`; `arm0/` keeps its names. A new command field gets a default so old code and old recordings still work. |
| **Deprecate before removing.** Keep the old name working for one merge cycle and say so in a handoff. | |
| **New features arrive switched off or behind a URL option** until both sides have tried them. | `?world=agrob-tomato`, `?cameras=wrist,head`, `?arm=yourController` |
| **Scene content is data, not code edits in shared files.** | Worlds are listed in a registry file; farm layouts are parameters; a new crop is a new layout module. |
| **Physics numbers that robot code depends on are named constants in one place** and listed in `interfaces.md`. | Timestep, control rate, detach force, grasp-assist radius, gripper stiffness |
| **Every seam change ships with a handoff** saying what changed, why, and what the other side must do (often "nothing"). | `docs/handoffs/2026-09-30-env-second-world.md` |

## Where each side adds code

| You want to… | Put it in… | Touch shared files? |
| --- | --- | --- |
| Replace IK, add a controller or policy | `web/src/robot/<yourController>.ts`; select it in one line where the simulation is created | One line (or a URL option once added) |
| Run Python MuJoCo experiments | `robot/` (Python); take the scene XML from `window.wefarm.simulation.sceneXml` or a future export script | No |
| Add a camera for training data | Ask in a handoff, or add it in `web/src/render/` with a URL option | Environment zone; coordinate |
| Change the arm model (FR3, two arms) | `web/src/sim/sceneXml.ts` mount list plus model files in `web/public/models/` | Yes: seam change with handoff |
| New world, plants, lighting, UI | Environment zone | No |

## Check before merging into `main` (about 1 minute)

1. `docker compose run --rm web npx tsc --noEmit -p tsconfig.json` (type check).
2. `docker compose run --rm web node --experimental-strip-types tools/simulationCheck.ts` (a scripted pick must end with `harvested`).
3. Open the page, pick one tomato by hand, and check that there's no red error box.

No other test suites; debug from logs and the browser.

## When a merge conflict happens anyway

- In a seam file, keep **both** sides' additions, then re-run the check. If the two sides changed the same behaviour differently, stop and ask the other side (or write a handoff) rather than guessing.
- `web/package-lock.json`: take either side, run `npm install` in the `web` container, and commit.
- Handoffs never conflict (new files only).
