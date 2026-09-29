# 2026-09-29 — env → robot: page ready in about 2 s; flicker fixed

**From:** environment side. **Branch:** `env/simulator` (also on `main`).

## What changed

- **Physics setup is about 10× faster: 0.9 s instead of 9–12 s.** `TomatoHarvestSimulation.create` now writes the Panda files and the scene XML to MuJoCo's in-memory file system (`mujoco.FS.writeFile`) and compiles with `MjModel.from_xml_path`. Before, it passed them through `MjVFS.addBuffer`. In the 3.14.0 WASM build, `addBuffer` copies byte by byte: 34 MB of meshes took about 12 s. `FS.writeFile` is a plain copy. The source files are deleted after compiling. The API and the resulting model are unchanged; the scripted pick check passes.
- **Flicker fixed.** The splat renderer keeps one sort order for one camera. The wrist-camera inset drew the splat from a second camera every frame, and the main view flickered. The inset now draws only the meshes and is off by default (key M).
- **Caching.** The dev server sends `ETag` and `Cache-Control: public, max-age=3600` for `/data/...`, so a refresh re-downloads nothing.
- **The photo-plant eraser** is added to or removed from the scene, instead of being hidden, when switching modes (key P).

## Seams

`web/src/sim/simulation.ts` changed internally (how the model is compiled). No interface changed.

## For robot code

Use `mujoco.FS.writeFile` instead of `MjVFS.addBuffer` for any large file you give MuJoCo in the browser.
