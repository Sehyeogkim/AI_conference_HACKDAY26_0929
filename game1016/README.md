# WeFarm Coffee Lab — Game 1016

A local browser game for collecting human demonstrations in the original **RLWRLD RoboCasa Kitchen24** coffee tasks. Python MuJoCo runs a PandaOmron mobile manipulator; the browser sends keyboard commands and displays live cameras. Everything for this game lives in `game1016/`.

Implemented: three task selections, keyboard arm/base control, gripper toggle, practice/collection/benchmark modes, pause/resume, stop and submit, raw recording, LeRobot v2.1 export, structural QA, measured action replay QA, episode ZIP download, and saved-state playback. Native simulation determines task success.

## Setup and run

Current installation target: **Apple Silicon macOS, Python3.11, Git and [uv](https://docs.astral.sh/uv/getting-started/installation/)**. The setup script creates an independent `.venv`, downloads pinned source revisions and legacy assets, and records their provenance. First setup downloads roughly 1GB of fixtures/textures plus a 13MB mug subset and source dependencies; it may take several minutes. No model weights or NVIDIA GPU are needed for human play.

From the repository root:

```sh
cd game1016
bash scripts/setup.sh
bash scripts/run.sh
```

Open **[http://127.0.0.1:8786](http://127.0.0.1:8786)**. First scene initialization can take tens of seconds; wait for Ready. Keep one active player tab open. Stop the server with `Ctrl+C` in its terminal.

Equivalent explicit launch:

```sh
.venv/bin/python -m coffee_game.server --host 127.0.0.1 --port 8786
```

To choose a different port: `COFFEE_PORT=8787 bash scripts/run.sh`. Linux is not yet validated; its package/render setup differs from the included macOS lock. See [runtime notes](docs/runtime-notes.md).

## Play and collect

Choose a mission and mode, then start the session. Click the simulation view to enable keyboard control. Use the **Keyboard controls** button or `?` for the full help table.

| Input | Arm | Base |
| --- | --- | --- |
| W / S | Forward / backward | Drive forward / backward |
| A / D | Left / right | Drive left / right |
| R / F | Up / down | Raise / lower torso |
| Q / E | Yaw | Turn base |
| Z / X | Pitch | — |
| C / V | Roll | — |
| Space | Open / close gripper | Open / close gripper |
| Tab | Switch arm/base mode | Switch arm/base mode |
| Shift | Fine movement | Fine movement |
| Esc | Pause | Pause |

Leaving the view/window releases input and pauses. Display camera choices do not move the canonical learning cameras. Overview currently displays the larger left-camera image.

| Mission | Original objective |
| --- | --- |
| CoffeeSetupMug | Pick the mug from the counter and place it under the coffee dispenser; withdraw the gripper. |
| CoffeeServeMug | Move the mug from the dispenser to the counter; withdraw the gripper. |
| CoffeePressButton | Press the coffee machine button and withdraw the gripper. |

Practice does not export. Collection allows 2,400 control steps; benchmark mode allows 720. The simulator uses 20Hz simulation time; rendering and recording can run slower than real time, shown in the UI. These three missions reset and save independently.

Select **Stop & submit** to close writers, export the dataset, run structural and measured replay checks, and create an episode ZIP. Success, integrity QA, replay QA and training eligibility are separate values. A stopped or unsuccessful episode is preserved and excluded from positive demonstration candidates.

## Files and data

```text
runs/episodes/<episode_id>/
  metadata.json, submission.json
  raw/episode.hdf5
  replay/model.xml, environment.json
  lerobot/data/, videos/, meta/
  qa/report.json
runs/episodes/<episode_id>.zip
```

Each pre-action frame stores three RGB256 cameras, state53, dataset action12, the separately ordered native action, native world integration state, timestamps, native result and input provenance. Each episode exports a standalone LeRobot v2.1 dataset; merging episodes requires reindexing its metadata. [Recording contract and output details](docs/recording.md).

Validate a saved episode's file structure:

```sh
.venv/bin/python scripts/check_dataset.py runs/episodes/EPISODE_ID
```

The game performs native action replay when submitting; use the episode's QA report to inspect measured qpos drift and replay success. Saved-state playback is a different operation from replaying actions.

## Verification and limits

- Confirmed locally: original CoffeeSetupMug construction/reset, all three native cameras at 512 with flip/downsample to 256, state53 and native/dataset action12, a native step, original metadata and clean close.
- Dependency imports, setup shell syntax and Python syntax checks passed. Pinned fixture/texture archive hashes and 949 mug-file CRC/SHA256 entries were checked.
- Human completion of the tasks and actual RLDX/LeRobot training-loader compatibility have not yet been demonstrated. Export shape compatibility does not establish improved learning or real-robot transfer.
- Replay restores MuJoCo integration state and the coffee button latch. Initial controller goals are restored for step 0; arbitrary mid-episode controller caches are not fully serialized. Measured replay reports this limitation and fails on divergence.
- This is one local player session. Local tab ownership is not production authentication; multi-user hosting, marketplace integration and AI Player inference are outside this game.
- Historical asset parity with RLDX training is unverified because original archive hashes were not published. Source tasks, robot controller and success checks are pinned and unmodified. Asset licensing needs its own review before commercial redistribution.

## Development references

- [Development specification for Dohen](docs/game-spec.md) — original requirements and milestones; it is not a completion report.
- [Runtime/source/asset notes](docs/runtime-notes.md)
- [HTTP and WebSocket API](docs/http-api.md)
- [Recording and export contract](docs/recording.md)
- [Pinned sources and asset checksums](source-manifest.json), [Python lock](requirements.lock), [mug file hashes](mug-assets-manifest.json)
- [Workspace instructions](AGENTS.md)
