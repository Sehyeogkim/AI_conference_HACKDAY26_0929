# 2026-09-29 — env → robot: the world loads from the cloud; loading screen

**From:** environment side (Sean's agents). **Branch:** `env/simulator`.

## What changed

- **You no longer need the world package folder.** The page first looks for a local copy (`data/worlds/crete-path/v2/`). If there is none, it downloads the public copy from the S3 bucket `wefarm-aiconf-2026-assets` (read-only, folder `worlds/`). A fresh checkout plus `docker compose up -d web` shows the photoreal greenhouse.
- **Loading screen** with one progress row per step (find the scene, download it, load physics and the Panda, build the 3D scene) and an overall bar. The splat download now runs at the same time as physics loading.
- `?world=` accepts a package id (`?world=crete-path/v2`), an exact folder URL, or `none`.

## Seams

None changed. `ArmController`, `OperatorCommand`, scene names, physics constants, and the recording format are the same (see `docs/interfaces.md`).

## What you need to do

Nothing. Merge `main` (or `env/simulator`) into your branch when convenient. The first load downloads about 7 MB; the browser caches it after that.

## Files

`web/src/render/splatWorld.ts` (package sources, download with progress), `web/src/render/loadingScreen.ts` (new), `web/src/main.ts`, `web/index.html`, `web/src/style.css`, `README.md`, `docs/README.md`. All environment zone.
