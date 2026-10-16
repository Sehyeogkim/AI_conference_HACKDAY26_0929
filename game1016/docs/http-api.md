# Local game API

This server runs one native simulation with one active browser tab. A tab supplies its sessionStorage ID in the WebSocket query `client_id` and the `X-Player-ID` header on mutating HTTP requests. A second connected tab is rejected. This is a local development ownership lock, not production authentication.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Native task, phase, robot mode, recording state, simulation time, target capture parameters, measured tick time |
| `GET /api/episodes` | Persisted local episode summaries, structural and measured replay QA |
| `WS /ws?client_id=...` | Keyboard heartbeat, release/pause; receive status and camera frames |
| `POST /api/reset` | `{task_id, seed, mode}`; mode is practice, collection, or benchmark |
| `POST /api/start` | Begin simulation; collection and benchmark also create a recorder |
| `POST /api/pause`, `/api/resume` | Stop/restart simulation time and recording together |
| `POST /api/submit` | Stop, finalize files, inspect them, run measured native action replay, create ZIP |
| `POST /api/discard` | Preserve an interrupted recording as excluded, then reset |
| `POST /api/view` | `{view}`; display camera selection does not modify canonical training cameras |
| `POST /api/replay` | `{episode_id}`; play saved simulator states, then return to a fresh practice scene |
| `GET /api/episodes/{id}/download` | Raw episode, LeRobot dataset, replay files, QA report in one ZIP |
| `GET /api/episodes/{id}/report` | Actual QA report JSON |

Keyboard messages contain `type: input`, `keys: KeyboardEvent.code[]`, `mode: arm|base`, `gripper: boolean`, and `fine: boolean`. The server converts these to native controls, then separately maps the applied action to dataset order. Missing heartbeat for one second pauses an active session.

Practice is not exported. Collection defaults to a 2,400-step horizon; benchmark uses 720 steps. Both use 20Hz simulation time. Wall-clock rendering and compression can run slower, and timing is reported separately. A QA pass does not establish improved policy learning or real-robot transfer.
