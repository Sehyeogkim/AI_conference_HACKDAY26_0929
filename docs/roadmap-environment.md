# Environment roadmap

Plan for the environment workstream (Sean's agents), written 2026-09-29. It is updated as items land; finished items link to their handoff. The robot workstream keeps its own plan.

## Review of the first draft (2026-09-29)

| Weakness seen in screenshots | Cause |
| --- | --- |
| Generated plants (flat, pale leaves) look fake next to the photoreal plants | Simple code-made leaves in front of real, photographed ones |
| Pickable tomatoes are hard to see | Cherry size, matte material |
| Head camera blocked by the arm | Placed directly behind the arm |
| World blurry at the edges, behind the start point, and at the far end | Generated from a single photo; World Labs guesses anything the photo does not show |

## Next steps, in order

| # | Step | Why | Touches seams? |
| --- | --- | --- | --- |
| 1 | **Photoreal plants stay; code-made leaves go.** Keep thin stems and trusses and the pickable tomatoes in front of the real plant walls; erasing the photo's plants becomes optional | The whole view looks photographic; only what you can pick is code-made | No |
| 2 | **Tomatoes easier to see and pick:** slightly larger, glossy, a green calyx, and a hover highlight | Demo readability | No (tomato size is a layout parameter) |
| 3 | **Cameras:** head camera raised and offset; a wrist-camera inset | Operator view; the views training data will need | Adds cameras; names listed in `interfaces.md` when added |
| 4 | **World registry and scene switcher** (`web/public/worlds/index.json`; `?world=<id>`) | Several farms, one robot: the product idea in one click | No (data file) |
| 5 | **Better worlds from more images:** multi-image input (up to 4 photos with directions, or up to 8 overlapping frames, or a short video). Candidates: other photos in the Crete series; the AgRobTomato robot-camera frames (CC BY 4.0); ideally our own phone video of a real row | Single-photo worlds break when you turn around | No |
| 6 | **Scale check tool:** a 1 m reference cube and a path-width readout in the page | World Labs' own scale was 36% off on the standard world | No |
| 7 | **Export for training:** render recorded sessions from named cameras; write episodes in LeRobot format | The product's training-data loop | Recording format read-only; export is new code |
| 8 | **Upload flow:** photos → world generation job (budget guard) → scale check → new world in the registry | "A farmer uploads images" | No |

Steps 1–4 are small and safe. Step 5 costs about $1.30 per standard world (World Labs credits) and runs in parallel.

## What the robot side can rely on while this happens

Everything listed in [`interfaces.md`](interfaces.md) stays stable: `ArmController`, `OperatorCommand`, the scene's joint, body and actuator names, the physics constants, and the recording schema version. Steps 1–8 add to these; none rename or remove anything. Any change there comes with a handoff note.
