# 2026-09-29 — env → robot: the wrist-camera inset shows the photo scene again, on by default

**From:** environment side. **Branch:** `env/simulator` (also on `main`).

The wrist inset (key M) is **on by default** and draws the full scene again, including the splat. The flicker is fixed differently now. The splat renderer (`SparkRenderer`) keeps one sort order. For the inset render, `spark.autoUpdate` is switched off, so the inset reuses the main camera's order and never re-sorts. The main view therefore never gets an order meant for another camera. The inset's blending can be slightly off because of this; that is fine for an operator preview.

The earlier handoff said "meshes only, off by default"; that is replaced by this note.

For later training-data rendering from several cameras, render one camera per pass, each with its own sort. Do not reuse this preview shortcut.

Seams: none changed.
