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

The photoreal scene (`data/worlds/crete-path/v2/`, about 40 MB) is not in Git. Get the folder from Sean and put it at the same path; the page finds it automatically. Without it the simulator runs on a plain ground (`?world=none`).
