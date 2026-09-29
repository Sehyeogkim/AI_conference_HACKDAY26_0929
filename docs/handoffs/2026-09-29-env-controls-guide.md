# 2026-09-29 — env → robot: controls guide; wrist inset draws meshes only

**From:** environment side. **Branch:** `env/simulator` (also on `main`).

- **Controls guide.** A "How to play" pop-up shows a drawn keyboard with every used key coloured by group (arm, wrist, gripper, cart, view, precise), a legend, and a six-step "first harvest" walkthrough. It opens with the **Controls (?)** button or the **?** key, closes with Esc, and opens once by itself on a first visit (remembered in `localStorage`). Code: `web/src/ui/controlsGuide.ts`. If you add or change a key in `web/src/teleop/`, update `KEYBOARD_ROWS` there too.
- **New zone folder.** `web/src/ui/` (page UI widgets) belongs to the environment zone; `AGENTS.md` is updated.
- **Wrist inset.** It is on by default and draws the robot, cart, crate, and fruit, but not the photo splat. Drawing the splat from a second camera made the main view flicker on some machines, even when the inset reused the main camera's sort order. This replaces the note "wrist inset shows the photo scene again". The full wrist view with the splat is still available as the main camera (V).

Seams: none changed.
