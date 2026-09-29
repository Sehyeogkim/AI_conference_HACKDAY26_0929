# WeFarm shared docs

Shared between Sean's and his teammate's people and coding agents. Start with [`../AGENTS.md`](../AGENTS.md) (zones and merge rules), then:

| File | What |
| --- | --- |
| [`interfaces.md`](interfaces.md) | The seams between the environment and robot work: `ArmController`, operator command, scene names, recording format |
| [`working-together.md`](working-together.md) | Branch flow, how environment updates stay safe for robot code, where each side adds code, the 1-minute check before merging, resolving conflicts |
| [`roadmap-environment.md`](roadmap-environment.md) | What the environment side does next, and what stays stable meanwhile |
| [`handoffs/`](handoffs/) | Dated notes from one side to the other: what changed, what to pull, what is next. One new file per handoff; never edit old ones. |
| [`../README.md`](../README.md) | Run it, controls, record/replay, credits |

## Getting the world package

The photoreal scene is not in Git. The page loads it by itself: first from a local copy at `data/worlds/<id>/<version>/`, otherwise from the public, read-only S3 bucket `wefarm-aiconf-2026-assets` (folder `worlds/`, CC BY-SA 4.0, credit in each package's `ATTRIBUTION.txt`). No setup is needed. To work offline, download the package folder into `data/worlds/` at the same path. `?world=none` runs on a plain ground. New worlds are uploaded by the environment side; the bucket's other folders are private.
