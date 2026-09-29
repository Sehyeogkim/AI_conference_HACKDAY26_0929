# Environment roadmap

Plan for the environment workstream (Sean's agents), written 2026-09-29. It is updated as items land; finished items link to their handoff. The robot workstream keeps its own plan.

## Review of the first draft (2026-09-29)

| Weakness seen in screenshots | Cause |
| --- | --- |
| Generated plants (flat, pale leaves) look fake next to the photoreal plants | Simple code-made leaves in front of real, photographed ones |
| Pickable tomatoes are hard to see | Cherry size, matte material |
| Head camera blocked by the arm | Placed directly behind the arm |
| World blurry at the edges, behind the start point, and at the far end | Generated from a single photo; World Labs guesses anything the photo does not show |

## Done since the first draft

| Date | What | Handoff |
| --- | --- | --- |
| 2026-09-29 | World package loads from the public S3 bucket when there is no local copy; loading screen with per-step progress | [handoff](handoffs/2026-09-29-env-world-from-cloud-and-loading-screen.md) |
| 2026-09-29 | Realism pass: photo plants kept (generated foliage optional), shadows on the photo ground, glossy lobed tomatoes with calyx, hover highlight, sharper splat swapped in; tomato mass from size, detach force by ripeness | [handoff](handoffs/2026-09-29-env-realism-pass.md) |
| 2026-09-29 | Robot, cart, and fruit lit by the world's own panorama (`files.lighting_pano` in `world.json`, optional); galvanised-steel cart and green plastic crate (drawing only) | none needed (environment zone only) |
| 2026-09-29 | Head camera moved to a rear-corner mast (sees past the arm); wrist-camera inset (key M); cameras listed in `interfaces.md` § 5 | none needed (environment zone only) |

## Next steps, in order

Steps 1–3 of the first plan (photo plants, tomato look, cameras) are done; see the table above.

| # | Step | Why | Touches seams? |
| --- | --- | --- | --- |
| 1 | **Better worlds from more images:** multi-image input (up to 4 photos with directions, or up to 8 overlapping frames, or a video up to 30 s). Best: our own phone video of a real row; also the AgRobTomato robot-camera frames (CC BY 4.0) or the four "Tomato cultivation in France" glasshouse photos | Single-photo worlds are blurry off-axis and have holes behind the plants | No |
| 2 | **World registry and scene switcher** (`worlds/index.json` in the bucket; `?world=<id>` already works) | Several farms, one robot: the product idea in one click | No (data file) |
| 3 | **Plant walls:** invisible collision shapes along each row, fitted to the world's collider mesh, so the arm is blocked by plants instead of passing through | Behaves real; policies learn to avoid foliage | Yes (scene geoms; handoff) |
| 4 | **Honest grasp data:** record when grasp assist held a fruit and how far off-centre it was; record peak squeeze force per fruit and mark bruising above a limit | Training data must not teach that a sloppy grasp works | Yes (recording fields; version bump, handoff) |
| 5 | **Scale check tool:** a 1 m reference cube and a path-width readout | World Labs' own scale was 36% off on the standard world | No |
| 6 | **Export for training:** render recorded sessions from the named cameras (`interfaces.md` § 5); write LeRobot episodes | The product's training-data loop | Recording format read-only; export is new code |
| 7 | **Upload flow:** photos or video → world generation job (budget guard) → scale check → new world in the registry | "A farmer uploads images" | No |

## Next sprint: from customer photos to a simulator that looks and behaves real

Goal (Sean, 2026-09-29): a customer's photos become a simulator whose **interactable** parts (fruit, trusses, plants in reach) match the real farm in look and behaviour, not only the backdrop.

The problem in one sentence: World Labs gives a picture you can walk through, but physics cannot touch it, so everything the robot touches must be rebuilt as objects with a shape, an appearance, and physical numbers.

```text
customer video of a row
   ├─► World Labs ───────────► backdrop (splat) + rough shape (collider) ─► plant walls
   ├─► find the fruit ───────► where each truss hangs; fruit size and ripeness colour
   │   (object-outlining image model on the frames, placed in 3D with the collider)
   ├─► fruit appearance ─────► skin colours / textures sampled from the photos
   └─► crop settings ────────► variety, mass, detach force (defaults + customer input)
                                     │
                                     ▼
   work cell generated from measurements instead of a random seed; the photo's own copies
   of those fruit erased; bendy trusses; the same seams (interfaces.md) as today
```

| Piece | First version | Later |
| --- | --- | --- |
| Fruit placement | Detect trusses in the input frames; place generated trusses at those spots (layout parameters become measured data) | Track every visible fruit |
| Fruit look | Per-farm colour palette sampled from detected fruit (today: a fixed palette in `render/tomatoFruit.ts`) | Photo textures; one image-to-3D truss model per variety; lifting the real fruit's splat blobs onto physics bodies (research) |
| Stems | Point constraint that snaps at a ripeness-based force (today) | MuJoCo cables: the truss bends and sways before it snaps; cutting at the stalk |
| Physical numbers | Mass from size; detach force by ripeness (working defaults, today) | Calibrated against published detachment-force measurements per variety |
| Plants in reach | Plant walls from the collider (step 3) | Soft, pushable foliage |

Seam impact: fruit placement changes the layout's data source, not its shape (`TomatoSpec` stays). Stems and recording changes follow the "add, don't break" rules in [`working-together.md`](working-together.md).

## What the robot side can rely on while this happens

Everything listed in [`interfaces.md`](interfaces.md) stays stable: `ArmController`, `OperatorCommand`, the scene's joint, body and actuator names, the physics constants, and the recording schema version. The steps above add to these; none rename or remove anything. Any change there comes with a handoff note.
