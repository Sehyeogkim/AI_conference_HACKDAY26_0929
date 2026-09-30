# 2026-09-29 — env → robot: game-controller teleop; AI panel stacked above the wrist view

**From:** environment side. **Branch:** `special_demo` (from `main` at `dfea38d`; not merged into `main`).

## What changed

- **Game controllers drive the robot.** New file `web/src/teleop/gamepadTeleop.ts` reads any controller the browser reports with the standard layout (Xbox, PlayStation, Switch Pro, look-alikes). Tested with a simulated standard controller in Chrome; a real Bluetooth controller (IINE Mini Retro Ananke in Xbox mode) pairs with macOS as an Xbox One S controller.
- **Layout (by button position):** arm mode by default: D-pad moves the hand, top/bottom face buttons raise/lower it, L/R rotate the wrist, ZL/ZR tilt it, right face button grips, −/+ self-drive to the previous/next stop, left face button toggles cart mode (D-pad drives and slides, L/R turn; a badge shows). A tap nudges about 1 cm; holding glides, starting at a quarter speed and reaching full speed after about 0.85 s. No button needs holding together with another.
- **Input devices share one motion model.** `KeyboardTeleop` gained public `applyMotionKeys(keys, dt, factor)`, `toggleGripper()`, and `returnToReadyPose()`. Other devices name movements by the keyboard key with the same effect and go through the keyboard's speeds, limits, and `OperatorCommand`. Keyboard behaviour is unchanged. Keyboard, mouse, and controller work at the same time (for example, one person orbits the camera with the mouse while another drives the arm).
- **Switch:** controllers are on by default; the 🎮 button in the bottom panel (remembered per browser) or `?gamepad=off` turns them off.
- **Recordings** made while a controller is connected and on say `operator.device: "keyboard+mouse+gamepad"`. No fields were added; the schema version stays 0.
- **Controls guide** has two pages, Keyboard & mouse and Controller. The Controller page is drawn from `GAMEPAD_LAYOUT`, so it stays in step with the code, and lights up the buttons being pressed.
- **On-screen controller map** (🎮 Map button or G): a small drawing of the controller at the top of the screen that shows the current mode and lights up pressed buttons. It appears when a controller connects, unless the viewer hid it. Drawing shared with the guide: `web/src/ui/controllerDiagram.ts`.
- **On-screen keyboard map** (⌨️ Keys button or B), the keyboard counterpart; keys light up while held. Drawing shared with the guide: `web/src/ui/keyboardDiagram.ts` (`KEYBOARD_ROWS` moved there from `controlsGuide.ts`).
- **Marketplace address:** the simulator asks the marketplace named by `VITE_WEFARM_API_BASE` (set in `compose.yaml` from `MARKETPLACE_PORT`) for AI Mode and free-play uploads, instead of always `127.0.0.1:8765`. A game opened from the marketplace still uses the marketplace that opened it.
- **Diagnostics:** the console logs `[gamepad]` lines for connect, disconnect, and every button press (index, position name, mode, effect); the guide's Controller page shows the browser's raw report.
- **AI panel:** it now sits above the wrist-camera inset instead of covering it (`--ai-panel-bottom`, set from the inset's size), and folds down to its title and state with its Hide button or the N key (remembered per browser).

## Seams

`web/src/teleop/` (shared): one new file, three new public methods on `KeyboardTeleop`, no renamed or removed names. `docs/interfaces.md` § 2 mentions the controller.

## What you need to do

Nothing. If you add a movement, add its key to `KeyboardTeleop` first; a controller button can then bind to that key in `GAMEPAD_LAYOUT`.

## Checks run

Type check; `tools/simulationCheck.ts` ends with `harvested`; in Chrome with a simulated controller: tap forward 1.05 cm, hold 1.2 s 26.8 cm, grip toggles, wrist rotates, + goes to the next stop, cart mode drives the cart without moving the hand, badge shows and hides, AI panel stacks and folds, no console errors.
