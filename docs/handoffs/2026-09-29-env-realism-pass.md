# 2026-09-29 — env → robot: realism pass (looks and tomato physics)

**From:** environment side (Sean's agents). **Branch:** `env/simulator` (also on `main`).

## What changed

**Looks (environment zone only):**

- **Photo plants.** The photo's own plants now show by default. The generated stems, strings, and leaves appear only in "generated plants" mode (key P). The pickable trusses and fruit hang in front of the real plants.
- **Shadows.** An invisible ground now shows the shadows of the cart, arm, and fruit on the photographed path.
- **Tomatoes.** Pickable tomatoes are drawn slightly flattened and lobed, with a glossy skin, a colour per fruit by ripeness, and a green calyx. The tomato under the mouse lights up. The physics shape is still MuJoCo's sphere of the same radius.
- **Loading.** A light 1.4 MB splat shows first; the full-detail 28 MB splat swaps in while you play. `?splat=500k` pins a lighter level.

**Physics (shared seam):**

| Before | Now |
| --- | --- |
| Every tomato 20 g | Mass from size at 1000 kg/m³ (`massKg` on each `TomatoSpec`, about 20–40 g) |
| Every stem lets go above 8 N | By ripeness (`detachForceN` on each `TomatoSpec`): ripe 6 N, turning 9 N, green 14 N. `DETACH_FORCE_N_BY_RIPENESS` in `web/src/farm/farmLayout.ts`. These are working defaults, not yet calibrated against measured detachment forces |

`DETACH_FORCE_N` (8 N) still exists, as the fallback. No names were removed or renamed. The farm layout (positions, sizes, ripeness) is unchanged for the same seed. The scene XML changes (tomato masses), so recordings made before this change report a different scene hash and will not replay.

**Debug handle:** `window.wefarm` now also has `orbitCamera`, `orbitControls`, and `splatWorld`.

## What you need to do

- If your controller or tests assumed 8 N or 20 g, read `tomato.detachForceN` and `tomato.massKg` from the layout instead.
- Green tomatoes need a harder pull, which is intended: the task is to leave them on the plant.
- Checks run: type check; `tools/simulationCheck.ts` (grasp → detach at about 13.5 N → release → harvested); a pick in Chrome (harvested, no errors).

## Files

`web/src/farm/farmLayout.ts`, `web/src/sim/sceneXml.ts`, `web/src/sim/simulation.ts` (seam). `web/src/render/tomatoFruit.ts` (new), `web/src/render/tomatoPlants.ts`, `web/src/render/splatWorld.ts`, `web/src/main.ts` (environment zone). `docs/interfaces.md`, `README.md`, `docs/roadmap-environment.md`.
