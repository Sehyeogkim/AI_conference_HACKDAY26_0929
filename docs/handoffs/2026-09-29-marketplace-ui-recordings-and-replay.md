# 2026-09-29 — marketplace → environment: recordings stored on the server, replay from a URL

**From:** marketplace web app work (Sean's agent). **Branch:** `marketplace-ui` (from Jeff's `UI`).

## What changed in the simulator (`web/src/main.ts`)

| Before | Now |
| --- | --- |
| "Stop & save" outside a marketplace game downloaded `wefarm-session-*.jsonl.gz` in the browser | It uploads the recording to the marketplace server (`POST /api/wefarm/free-play`), stored under `runs/wefarm/free-play/`. The browser download happens only if the server is unreachable |
| Replay only from a file picked with "Replay…" | Also `?replay=<recording URL>` (used by the marketplace's "▶ Watch" links). The file-picker path is unchanged; both call one `startReplay(blob)` function |
| AI Mode panel always shown | Hidden during replay and when no Crusoe model is configured |

The recording format and schema version are unchanged. No seam files (`web/src/sim/`, `web/src/teleop/`) were touched on this branch.

## What you need to do

Nothing. When `env/simulator` and this branch meet in `main`, expect a small overlap in `main.ts` (this branch carries Jeff's AI Mode and upload code); keep both sides.

## Running the whole demo

`WEB_DEV_PORT=5180 docker compose up -d web marketplace`, then open `http://127.0.0.1:8765/`. The `marketplace` service reads optional keys from `../.env.secrets.marketplace` (template: `.env.secrets.marketplace.example`).
