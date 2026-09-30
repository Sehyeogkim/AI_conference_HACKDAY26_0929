# 2026-09-29 — special_demo: the marketplace demo runs end to end

**From:** Sean's agent. **Branch:** `special_demo` only (from `main` at `dfea38d`). Not merged into `main`; whether to bring these changes to `main` is still open.

## Why

On `main` (after `e0d488f`), demo mode could not finish the demo: a stored OpenRouter answer can never approve an episode, so every episode stayed "pending review" and could not be bought. Neo4j also ran in demo mode, adding about 5 s per write, and its approval query would have hidden demo episodes from the Data Miner. The simulator always talked to the marketplace on port 8765, even when another copy ran elsewhere.

## What changed

| Area | Change |
| --- | --- |
| Episode QA in demo mode | Still calls OpenRouter live, in the background (`WEFARM_DEMO_LIVE_QA`, default 1). Only if OpenRouter is unreachable or unconfigured, and every hard gate passed, a stored acceptance approves, with `qa_reason` starting "Demo approval: … live OpenRouter QA unavailable (…)". `_approved` accepts `openrouter-cached` only in demo mode. Outside demo mode nothing changed |
| Neo4j | `GraphStore.from_env` reports "not configured" in demo mode, so every caller uses the local index; the listing page says Neo4j is skipped in demo mode |
| Simulator ↔ marketplace | The simulator's default marketplace comes from `VITE_WEFARM_API_BASE`, set in `compose.yaml` from `MARKETPLACE_PORT`; the marketplace already sends players to `WEB_DEV_PORT`. One compose project = one matched pair |
| Replay event list | Grasp and release events were labelled "dropped"; now "gripper closed on" / "gripper released" |

## Checked (2026-09-29, ports 8766 and 5182)

Publish the example → play (scripted pick, 1 ripe harvested) → submit (0.3 s) → live OpenRouter QA approved (`google/gemini-2.5-flash`, under 5 s) → buy (wallet 1,000 → 996) → download (gzip, 200) → ▶ Watch and `/watch/sample` replay with correct event labels → free-play recording saved on the 8766 server. Tests: `tests/test_wefarm_marketplace.py`, `tests/test_neo4j_graph_lifecycle.py`, `tests/test_sponsor_integrations.py` (17 passed).
