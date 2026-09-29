# WeFarm — guide for coding agents (and humans)

Read this first, then `docs/README.md`. `CLAUDE.md` points here.

**This file is shared by everyone on the project** (Sean, his teammate, and all of their coding agents). Anyone may edit it: add rules, fix what is wrong, adjust zones as the work changes. Keep edits small and in place so they merge cleanly, and mention a change to the zones or rules in a handoff note (`docs/handoffs/`) so the other side notices.

WeFarm is a browser simulator for robot harvesting: a photoreal greenhouse (World Labs Marble splat) plus a generated work cell (plants, tomatoes) and a harvest cart with a Franka Panda, simulated live by MuJoCo (WebAssembly) and drawn with three.js. People teleoperate it; sessions are recorded and replayed for QA; recordings are meant to become training data.

## Two workstreams, two ownership zones

| Zone | Owner | Paths | What lives there |
| --- | --- | --- | --- |
| **Environment** | Sean's agents | `web/src/farm/`, `web/src/render/`, `web/src/recording/`, `web/src/main.ts`, `web/index.html`, `web/src/style.css`, `src/wefarm/` (Python world generation), `data/` (ignored), `compose.yaml`, `docker/` | Scene, world packages, rendering, UI, recording format |
| **Robot** | Friend's agents | `web/src/robot/` (arm control), `robot/` (Python robot work, create as needed) | IK, controllers, policies, grasping, arm models, Python MuJoCo experiments |
| **Shared seam** (change with care, note it in a handoff) | Both | `web/src/sim/` (scene generator + simulation core), `web/src/teleop/`, `web/public/models/`, `docs/interfaces.md` | The contract between the two zones |

Rules that keep merges easy:

1. **Stay in your zone.** Edits outside it go through the shared seam and get a handoff note (`docs/handoffs/`).
2. **Extend, don't rewrite, shared files.** Add a new function or file rather than reformatting or reordering an existing one. Never reformat files you did not otherwise change.
3. **Interfaces are versioned.** `docs/interfaces.md` lists the seams (`ArmController`, `OperatorCommand`, the scene's body and joint names, the recording schema version). Changing one means: update that file, bump the version where it has one, write a handoff.
4. **Handoffs are new files, never edits** (`docs/handoffs/YYYY-MM-DD-<from>-<topic>.md`), so they never conflict.
5. **Generated or large files are not committed**: `data/`, `runs/`, `web/dist/`, `node_modules/`. World packages are shared out of band (see `docs/README.md`).
6. `web/package-lock.json` conflicts: take either side, then run `npm install` in the `web` container and commit the result.
7. Small commits with plain descriptions; branch per workstream (`env/<topic>`, `robot/<topic>`); merge `main` into your branch often.
8. **Add, don't break:** new fields optional with defaults, old names kept working until a handoff retires them, new features behind a URL option first. Details, the branch flow, and the 1-minute check before merging: [`docs/working-together.md`](docs/working-together.md).

## Run

Only Docker is needed. Day to day this is **one container** (`web`: Vite dev server with live reload, the code is mounted, edits apply instantly). The `jobs` container is only for generating new World Labs worlds and needs Sean's API key; robot work never needs it.

```sh
docker compose up -d web                    # http://127.0.0.1:5180
docker compose run --rm web npx tsc --noEmit -p tsconfig.json          # type check
docker compose run --rm web node --experimental-strip-types tools/simulationCheck.ts   # 5 s headless pick check
```

Without Docker: `cd web && npm install && npm run dev -- --port 5180` (Node 22+).

In the browser, `window.wefarm` exposes `{ simulation, layout, teleop }` for debugging. Errors show in a red box on the page.

## Testing policy

Keep it minimal and fast: type check, the 5-second headless pick check, and trying it in a real browser (headless Chrome cannot render the splat). Do not add test suites that take longer to maintain than to debug by hand.

## Conventions

- World frame: metres, **z up**, right-handed; +x along the path, +y to the left row. three.js is y-up: all world content sits under one root rotated −90° about x; nothing else converts.
- Descriptive names; comments that stand on their own.
- No secrets in the repo. The World Labs key lives outside the repository and is mounted only into the `jobs` container.
