// A drawn game controller (shoulder buttons, D-pad, − / +, face buttons), coloured by what each
// button does in the current mode and generated from the controller layout table (GAMEPAD_LAYOUT).
// Used by the controls guide and by the on-screen controller map; pressed buttons light up.
import { GAMEPAD_LAYOUT, type GamepadButtonName, type GamepadControlMode } from "../teleop/gamepadTeleop.ts";

/** Which buttons sit where, with a short label on each. */
const CONTROLLER_SHAPE: {
  shoulders: [GamepadButtonName, string][][];
  dpad: [GamepadButtonName, string, string][];
  face: [GamepadButtonName, string, string][];
  middle: [GamepadButtonName, string][];
} = {
  shoulders: [
    [["triggerLeft", "ZL"], ["shoulderLeft", "L"]],
    [["shoulderRight", "R"], ["triggerRight", "ZR"]],
  ],
  dpad: [["dpadUp", "▲", "up"], ["dpadLeft", "◀", "left"], ["dpadRight", "▶", "right"], ["dpadDown", "▼", "down"]],
  face: [["faceTop", "top", "top"], ["faceLeft", "left", "left"], ["faceRight", "right", "right"], ["faceBottom", "bottom", "bottom"]],
  middle: [["minus", "−"], ["plus", "+"]],
};

export interface ControllerDiagram {
  element: HTMLElement;
  mode(): GamepadControlMode;
  showMode(mode: GamepadControlMode): void;
  setPressed(pressed: ReadonlySet<GamepadButtonName>): void;
}

export function createControllerDiagram(extraClass = ""): ControllerDiagram {
  const padButton = (name: GamepadButtonName, label: string, slotClass = "") =>
    `<div class="pad-button ${slotClass}" data-button="${name}"><span class="pad-button-label">${label}</span><span class="pad-button-hint"></span></div>`;
  const element = document.createElement("div");
  element.className = `guide-controller ${extraClass}`.trim();
  element.innerHTML = `
    <div class="pad-shoulders">
      ${CONTROLLER_SHAPE.shoulders.map((side) => `<div class="pad-shoulder-side">${side.map(([name, label]) => padButton(name, label, "pad-shoulder")).join("")}</div>`).join("")}
    </div>
    <div class="pad-body">
      <div class="pad-cluster pad-dpad">${CONTROLLER_SHAPE.dpad.map(([name, label, slot]) => padButton(name, label, `pad-slot-${slot}`)).join("")}</div>
      <div class="pad-middle">${CONTROLLER_SHAPE.middle.map(([name, label]) => padButton(name, label, "pad-small")).join("")}</div>
      <div class="pad-cluster pad-face">${CONTROLLER_SHAPE.face.map(([name, label, slot]) => padButton(name, label, `pad-round pad-slot-${slot}`)).join("")}</div>
    </div>`;
  const padButtons = new Map([...element.querySelectorAll<HTMLElement>(".pad-button")].map((button) => [button.dataset.button as GamepadButtonName, button]));
  let shownMode: GamepadControlMode | null = null;

  const showMode = (mode: GamepadControlMode) => {
    if (mode === shownMode) return;
    shownMode = mode;
    for (const [name, button] of padButtons) {
      const binding = GAMEPAD_LAYOUT[mode][name];
      button.className = button.className.replace(/\s*(group-\S+|unused)/g, "") + (binding ? ` group-${binding.group}` : " unused");
      button.querySelector<HTMLSpanElement>(".pad-button-hint")!.textContent = binding?.hint ?? "";
    }
  };
  const setPressed = (pressed: ReadonlySet<GamepadButtonName>) => {
    for (const [name, button] of padButtons) button.classList.toggle("pressed", pressed.has(name));
  };
  showMode("arm");
  return { element, mode: () => shownMode ?? "arm", showMode, setPressed };
}
