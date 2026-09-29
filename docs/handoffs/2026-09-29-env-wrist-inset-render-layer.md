# 2026-09-29 — env → robot: wrist inset off by default; splat on its own render layer

**From:** environment side. **Branch:** `env/simulator` (local commit, not yet pushed).

- The wrist-camera inset is **off by default** (key M turns it on). With it on, the photo leaves flashed on Sean's machine, and hiding it stopped the flashing.
- **Attempted fix, to confirm on the affected machine.** The splat renderer (`SparkRenderer`) is on render layer 1 (`SPLAT_RENDER_LAYER` in `web/src/main.ts`). All cameras enable that layer. The inset pass leaves it out by disabling the layer on the wrist camera, instead of setting `spark.visible` false and true every frame. The inset pass also reuses the main pass's shadow map (`renderer.shadowMap.autoUpdate` is off during it).
- Checks run: type check; scripted pick check ends with `harvested`; the page loads with no errors, with the inset off and on. The flashing never reproduced on the machine these checks ran on, so it is not yet known whether this fixes it.

Seams: none changed.
