// Tutorial-style controls guide with one page per input device:
// - Keyboard & mouse: a drawn keyboard (ui/keyboardDiagram.ts), keys lighting up while held.
// - Controller: a drawn game controller in arm or cart mode (ui/controllerDiagram.ts), with the
//   buttons you press lighting up live, and the browser's raw report for troubleshooting.
// Each page has a legend and a short "first harvest" walkthrough. Opens from the Controls button or
// the ? key, and once automatically on a first visit (remembered in localStorage when available).
import { createControllerDiagram } from "./controllerDiagram.ts";
import { KEYBOARD_ROWS, createKeyboardDiagram } from "./keyboardDiagram.ts";
import {
  GAMEPAD_LAYOUT,
  type ControlGroup,
  type GamepadButtonName,
  type GamepadControlMode,
  type GamepadStatus,
} from "../teleop/gamepadTeleop.ts";

const GROUP_LABELS: Record<ControlGroup, string> = {
  arm: "Move the gripper",
  wrist: "Rotate and tilt the wrist",
  grip: "Open / close the gripper",
  base: "Drive the cart",
  view: "Camera and view",
  modifier: "Precise (slow) moves",
  mode: "Switch arm ↔ cart mode",
};

const KEYBOARD_FIRST_HARVEST_STEPS = [
  "<b>Click</b> a red tomato (it lights up under the mouse). The arm glides above it with the gripper open.",
  "Press <b>F</b> to lower the gripper around the tomato. Hold <b>Shift</b> for fine moves.",
  "Press <b>Space</b> to close the gripper.",
  "Press <b>R</b> to lift and <b>S</b> to pull back: the stem snaps when you pull hard enough (ripe fruit comes off easiest).",
  "Move over the green crate on the cart and press <b>Space</b> to drop it. <b>Ripe harvested</b> goes up.",
  "Drive to the next plants with <b>]</b>, or with <b>I/K/J/L</b>. Leave the green tomatoes on the plant.",
];

const CONTROLLER_FIRST_HARVEST_STEPS = [
  "Use the <b>D-pad</b> to bring the gripper in front of a red tomato, and the <b>top</b> / <b>bottom</b> buttons to raise or lower it. <b>Tap</b> to nudge about 1 cm; <b>hold</b> to glide (it starts slow, then speeds up).",
  "Line the fingers up with <b>L / R</b> (rotate) and <b>ZL / ZR</b> (tilt) if needed.",
  "Press the <b>right</b> button to close the gripper.",
  "Press <b>top</b> to lift and <b>D-pad down</b> to pull back: the stem snaps when you pull hard enough.",
  "Move over the green crate and press the <b>right</b> button again to drop it. <b>Ripe harvested</b> goes up.",
  "Press <b>+</b> to self-drive to the next plants (<b>−</b> goes back). For fine driving, press the <b>left</b> button: <b>cart mode</b> (press it again to return to the arm).",
];

const SEEN_STORAGE_KEY = "wefarm.controlsGuideSeen";

export type ControlsGuidePage = "keyboard" | "controller";

export interface ControlsGuide {
  open(page?: ControlsGuidePage): void;
  close(): void;
  toggle(): void;
  /** Update the Controller page's connection line (and prefer that page while a controller is connected). */
  setGamepadStatus(status: GamepadStatus): void;
  /** Light up the buttons held on the controller, and show the layout of its current mode. */
  setGamepadButtons(pressed: ReadonlySet<GamepadButtonName>, mode: GamepadControlMode): void;
  /** Show the browser's raw controller report (troubleshooting line on the Controller page). */
  setGamepadRawReport(report: string): void;
}

const legendFor = (groups: Iterable<ControlGroup>) =>
  [...new Set(groups)].map((group) => `<li><span class="guide-swatch group-${group}"></span>${GROUP_LABELS[group]}</li>`).join("");

export function createControlsGuide(root: HTMLElement): ControlsGuide {
  const keyboardGroups = KEYBOARD_ROWS.flat().flatMap((key) => (key.group ? [key.group] : []));
  const controllerGroups = (["arm", "cart"] as const).flatMap((mode) => Object.values(GAMEPAD_LAYOUT[mode]).map((binding) => binding!.group));
  root.innerHTML = `
    <div class="guide-card" role="dialog" aria-modal="true" aria-labelledby="guide-title">
      <div class="guide-header">
        <h2 id="guide-title">How to play</h2>
        <div class="guide-tabs" role="tablist">
          <button type="button" role="tab" class="guide-tab" data-page="keyboard">⌨️ Keyboard &amp; mouse</button>
          <button type="button" role="tab" class="guide-tab" data-page="controller">🎮 Controller</button>
        </div>
        <button type="button" class="guide-close" aria-label="Close">✕</button>
      </div>
      <section class="guide-page" data-page="keyboard">
        <div class="guide-body">
          <div class="guide-keyboard-slot"></div>
          <div class="guide-side">
            <ul class="guide-legend">${legendFor(keyboardGroups)}</ul>
            <h3>Your first harvest</h3>
            <ol class="guide-steps">${KEYBOARD_FIRST_HARVEST_STEPS.map((step) => `<li>${step}</li>`).join("")}</ol>
            <p class="guide-mouse"><b>Mouse:</b> drag to orbit, scroll to zoom, click a tomato to reach for it.</p>
          </div>
        </div>
      </section>
      <section class="guide-page" data-page="controller" hidden>
        <p class="guide-gamepad-status"></p>
        <div class="guide-body">
          <div class="guide-controller-column">
            <div class="guide-mode-chips" role="radiogroup" aria-label="Controller mode shown">
              <button type="button" class="guide-mode-chip" data-mode="arm">Arm mode (default)</button>
              <button type="button" class="guide-mode-chip" data-mode="cart">Cart mode</button>
            </div>
            <div class="guide-controller-slot"></div>
            <p class="guide-controller-note">Buttons are named by position; press any button and it lights up here. <b>Tap</b> a movement button to nudge, <b>hold</b> it to glide. Nothing needs holding together.</p>
          </div>
          <div class="guide-side">
            <ul class="guide-legend">${legendFor(controllerGroups)}</ul>
            <h3>Your first harvest</h3>
            <ol class="guide-steps">${CONTROLLER_FIRST_HARVEST_STEPS.map((step) => `<li>${step}</li>`).join("")}</ol>
            <p class="guide-mouse"><b>Two players:</b> the controller drives the robot while someone else orbits the camera with the mouse. The keyboard keeps working too.</p>
            <p class="guide-gamepad-raw" title="What the browser reports, for troubleshooting"></p>
          </div>
        </div>
      </section>
      <p class="guide-footer">Press <b>?</b> or the <b>Controls</b> button any time. <b>Esc</b> closes.</p>
    </div>`;

  const pages = [...root.querySelectorAll<HTMLElement>(".guide-page")];
  const tabs = [...root.querySelectorAll<HTMLButtonElement>(".guide-tab")];
  const modeChips = [...root.querySelectorAll<HTMLButtonElement>(".guide-mode-chip")];
  root.querySelector(".guide-keyboard-slot")!.replaceWith(createKeyboardDiagram().element);
  const diagram = createControllerDiagram();
  root.querySelector(".guide-controller-slot")!.replaceWith(diagram.element);
  const gamepadRawLine = root.querySelector<HTMLParagraphElement>(".guide-gamepad-raw")!;
  const gamepadStatusLine = root.querySelector<HTMLParagraphElement>(".guide-gamepad-status")!;
  let currentPage: ControlsGuidePage = "keyboard";
  let controllerConnected = false;
  let lastLiveMode: GamepadControlMode = "arm";

  const showPage = (page: ControlsGuidePage) => {
    currentPage = page;
    for (const element of pages) element.hidden = element.dataset.page !== page;
    for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.page === page));
  };
  const showMode = (mode: GamepadControlMode) => {
    diagram.showMode(mode);
    for (const chip of modeChips) chip.setAttribute("aria-checked", String(chip.dataset.mode === mode));
  };
  showPage("keyboard");
  showMode("arm");
  for (const tab of tabs) tab.addEventListener("click", () => showPage(tab.dataset.page as ControlsGuidePage));
  for (const chip of modeChips) chip.addEventListener("click", () => showMode(chip.dataset.mode as GamepadControlMode));

  const open = (page?: ControlsGuidePage) => {
    showPage(page ?? (controllerConnected ? "controller" : currentPage));
    root.hidden = false;
  };
  const close = () => {
    root.hidden = true;
    try {
      localStorage.setItem(SEEN_STORAGE_KEY, "1");
    } catch {
      /* storage unavailable: the guide simply shows again next time */
    }
  };
  const toggle = () => (root.hidden ? open() : close());
  root.querySelector<HTMLButtonElement>(".guide-close")!.addEventListener("click", close);
  root.addEventListener("click", (event) => {
    if (event.target === root) close();
  });
  window.addEventListener("keydown", (event) => {
    if ((event.target as HTMLElement).tagName === "TEXTAREA") return;
    if (event.key === "?" || (event.code === "Slash" && event.shiftKey)) toggle();
    else if (event.key === "Escape" && !root.hidden) close();
  });

  const setGamepadStatus = (status: GamepadStatus) => {
    controllerConnected = status.gamepads.length > 0;
    if (!controllerConnected) {
      gamepadStatusLine.innerHTML = "No controller detected. Pair one over Bluetooth (for example in Xbox mode), then <b>press any button</b> on it while this page is open: browsers only reveal a controller after a button press.";
    } else {
      const names = status.gamepads
        .map((gamepad) => `<b>${gamepad.name}</b> (${gamepad.mode} mode${gamepad.layoutRecognised ? "" : ", unrecognised button layout: some buttons may be mislabelled"})`)
        .join(", ");
      gamepadStatusLine.innerHTML = `Connected: ${names}.${status.enabled ? "" : " <b>Controller input is switched off</b> (the 🎮 button in the bottom panel turns it on)."}`;
    }
    gamepadStatusLine.classList.toggle("connected", controllerConnected);
  };
  const setGamepadButtons = (pressed: ReadonlySet<GamepadButtonName>, mode: GamepadControlMode) => {
    // Follow the controller's mode when it changes; in between, a clicked mode chip stays shown.
    if (mode !== lastLiveMode) {
      lastLiveMode = mode;
      showMode(mode);
    }
    if (root.hidden) return;
    diagram.setPressed(pressed);
  };
  const setGamepadRawReport = (report: string) => {
    if (!root.hidden && gamepadRawLine.textContent !== report) gamepadRawLine.textContent = report;
  };

  let seen = false;
  try {
    seen = localStorage.getItem(SEEN_STORAGE_KEY) === "1";
  } catch {
    /* treat as not seen */
  }
  if (!seen) open();
  return { open, close, toggle, setGamepadStatus, setGamepadButtons, setGamepadRawReport };
}
