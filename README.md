# WeFarm

**Play the harvest. Capture robot data.**

![WeFarm concept cover: requester, game, player, and QA](docs/assets/wefarm-readme-cover.png)


https://github.com/user-attachments/assets/41123ccf-e880-4393-8777-31ea22ba8e8e


![WeFarm architecture concept](docs/assets/wefarm-readme-architecture-concept.png)

**What runs today:** the two images are concept visuals. The browser game has **one Franka Panda arm on a wheeled cart** and records **`.jsonl.gz`**, not HDF5. An uploaded farm photo is stored with the request; the demo opens a prepared World Labs greenhouse and a procedural MuJoCo work cell. OpenRouter interprets requests and reviews QA evidence, while Crusoe is the optional AI Player VLM. Neo4j indexes metadata when configured; recording files stay in local storage.

**This is the `special_demo` branch:** the version prepared for live demos. Compared with `main`, it adds game-controller play, on-screen keyboard and controller maps, an AI Mode panel that no longer covers the wrist camera, a demo mode that runs the whole flow through to buying, and marketplace layout fixes. See [what this branch changes](#what-this-branch-changes).

## Try it on localhost

This path needs **no API keys** (with an OpenRouter key, QA runs live; see [demo mode](#demo-mode)). Use a macOS or Linux terminal with Python 3.12+, Node.js 22+, npm, and `uv`.

```bash
git clone https://github.com/Sehyeogkim/AI_conference_HACKDAY26_0929.git
cd AI_conference_HACKDAY26_0929
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
npm ci --prefix web
```

If `uv` is unavailable, replace its two lines with `python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`.

Start the **game** in terminal 1:

```bash
cd web
npm run dev -- --host 127.0.0.1 --port 5180
```

Start the **marketplace** from the repository root in terminal 2:

```bash
WEFARM_DEMO_CACHE=1 .venv/bin/python -m agriphilo.web --host 127.0.0.1 --port 8765 --demo
```

To run live QA without Docker, also set `OPENROUTER_API_KEY` in that terminal's environment (never in a file inside the repository).

Open **[WeFarm](http://127.0.0.1:8765/)**. Choose Requester or Player on the landing page; use **Switch role** if that browser already has a demo identity. The simulator is also available directly at [localhost:5180](http://127.0.0.1:5180/).

| Demo step | What to do |
| --- | --- |
| 1. Publish | As **Requester**, click **New farm request → Use example → Publish game**. The example photo and brief are bundled in the repository. |
| 2. Play | As **Player**, open the new marketplace listing and click **Play**. In the game, click **Record**, harvest a ripe tomato (keyboard, mouse, or a game controller; see [playing](#playing)), then click **Stop & submit**. |
| 3. Inspect | Return to the Requester marketplace, open the listing, and inspect **Submitted episodes**. QA runs in the background and shows **Approved**, **Review needed**, or **Rejected** within a few seconds, with the reason. **▶ Watch** replays an episode in the simulator. The Player page also shows the submitted run and earned credits. |
| 4. Buy | On the listing, click **Buy episodes** (test credits; the demo wallet starts with 1,000). Bought episodes get a **↓ Download** link for the `.jsonl.gz` recording, and **Mine selected data** filters them. |
| 5. Watch a sample | Open [the bundled sample replay](http://127.0.0.1:8765/watch/sample) if you want to see a completed recording before playing. It is a sample, not a new marketplace submission. |

The first game load may download the public `crete-path/v2` greenhouse. If that asset is unavailable, the procedural work cell remains playable; `http://127.0.0.1:5180/?world=none` opens it directly. The [simulator guide](docs/simulator.md) has the full controls and a first-harvest recipe.

### Playing

| Input | How |
| --- | --- |
| Mouse | Drag to orbit the camera, scroll to zoom, click a red tomato to send the gripper above it |
| Keyboard | W/S A/D R/F move the gripper, Q/E Z/C turn and tilt the wrist, Space grips, I/K J/L U/O drive the cart, `[` `]` self-drive to the previous or next stop, Shift for slow moves |
| Game controller | Any controller the browser reports with the standard layout (Xbox, PlayStation, Switch Pro, and look-alikes). Pair it over Bluetooth, then press any button on the page. Arm mode by default: D-pad moves the gripper, top/bottom buttons raise/lower it, L/R and ZL/ZR turn and tilt the wrist, right button grips, − / + self-drive between stops; the left button toggles cart mode. A tap nudges about 1 cm, holding glides |

Keyboard, mouse, and controller work at the same time, so one person can steer the camera with the mouse while another drives the robot with the controller. **Controls (?)** opens the guide (Keyboard and Controller pages; pressed buttons light up). **B** and **G** show small on-screen keyboard and controller maps while you play, **M** hides the wrist camera, and **N** folds the AI Mode panel. The 🎮 button in the bottom panel, or `?gamepad=off`, turns controller input off.

### Docker option

If Docker is available, run `docker compose up -d web marketplace` and open [localhost:8765](http://127.0.0.1:8765/). The optional marketplace key file is `../.env.secrets.marketplace`; start from [.env.secrets.marketplace.example](.env.secrets.marketplace.example) and keep the filled copy outside this repository. Docker runs in [demo mode](#demo-mode) by default.

To run a second copy beside the first (for example a demo copy), give it other ports in an `.env` file next to `compose.yaml`, such as `WEB_DEV_PORT=5182` and `MARKETPLACE_PORT=8766`. Each copy's simulator and marketplace talk only to each other: the marketplace sends players to its own simulator, and the simulator sends recordings, AI Mode requests, and free-play saves to its own marketplace.

## How a recording becomes data

1. A requester submits a farm image and data brief. WeFarm publishes a task and marketplace listing using a prepared simulator scene. OpenRouter can interpret the brief; without it, a fixed task template is used.
2. A human controls the browser MuJoCo robot, or the optional Crusoe AI Mode sends separate head and wrist camera images to its VLM for bounded actions. The game records states, actions, timing, and harvest events in `.jsonl.gz`.
3. Server-side structural checks and MuJoCo replay enforce measurable gates. OpenRouter evaluates the supplied evidence; its response cannot override a failed gate.
4. An **approved** episode (live OpenRouter QA, or a labelled demo approval in demo mode) can be indexed as metadata in Neo4j, selected by Data Miner, purchased with test credits, and downloaded by the requester. The recording file remains in local storage.

### Demo mode

`WEFARM_DEMO_CACHE=1` (the Docker default) keeps the demo fast. Request reading replays a stored OpenRouter answer, and Neo4j is skipped (the local episode index is used). Episode QA still asks OpenRouter live, in the background, so the page never waits and each episode shows a real pass or fail. If OpenRouter is unreachable or no key is configured, an episode whose hard checks all passed gets a **Demo approval**, labelled as such, from a stored answer, so the demo never gets stuck. `WEFARM_DEMO_LIVE_QA=0` uses the stored answer only. With `WEFARM_DEMO_CACHE=0`, only a live OpenRouter acceptance approves an episode, and Neo4j is used when configured.

### Keys

Crusoe AI Mode needs `CRUSOE_API_KEY` for Serverless vision inference, or `CRUSOE_VLM_ENDPOINT`, `CRUSOE_VLM_MODEL`, and `CRUSOE_VLM_API_KEY` for a dedicated compatible server. `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD` enable graph indexing. `STRIPE_SECRET_KEY` enables **Stripe test-mode** Checkout; credits are test credits, not cash payouts. Keep all keys out of Git.

## Scope and evidence

- The game runs MuJoCo in the browser and renders with three.js. The visible greenhouse is a prepared World Labs Marble scene; the request image does **not** generate a new 3D farm in this repository.
- Local Python tests, TypeScript checks, Vite build, and a headless MuJoCo harvest smoke test have passed. A live Crusoe call returned a bounded action from separate head and wrist images; a complete autonomous harvest has not been verified.
- On this branch (2026-09-29, Docker on macOS): publish → play → submit → live OpenRouter QA approved (a few seconds, in the background) → buy → download → Watch ran end to end; the marketplace tests passed; a Bluetooth controller (an IINE Mini Retro Ananke in Xbox mode) played the game in Chrome; the marketplace pages were checked at laptop and phone widths.
- The `.jsonl.gz` tomato game is the active path. The RoboCasa/HDF5 coffee demo under `sim/web` is legacy and uses a different data contract.

More detail: [architecture and limits](final_architecutre.md) · [QA contract](openroture_QA.md) · [AI Player](crusoe_player.md) · [simulator and asset credits](docs/simulator.md).

## What this branch changes

Compared with `main` at `dfea38d`. Details for the teammate are in [`docs/handoffs/`](docs/handoffs/) (game controller and AI panel; marketplace demo).

| Area | Change |
| --- | --- |
| Game controller | Standard-layout gamepads drive the robot through the keyboard's motion model (`web/src/teleop/gamepadTeleop.ts`); `[gamepad]` console logs and a raw report in the guide for troubleshooting |
| Guides and maps | Controls guide with Keyboard and Controller pages; on-screen keyboard (B) and controller (G) maps that light up what you press |
| AI Mode panel | Stacked above the wrist-camera view instead of covering it; folds with N |
| Simulator ↔ marketplace | Each compose project pairs its own simulator and marketplace (`VITE_WEFARM_API_BASE` from `MARKETPLACE_PORT`) |
| Demo mode | Live OpenRouter QA in the background with a labelled fallback; Neo4j skipped; buying works |
| Marketplace pages | The request dialog scrolls with Publish always visible; listing cards and the top bar no longer overflow |
| Replay | Event list names grasps and releases correctly |
