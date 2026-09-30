// A drawn keyboard with every used key coloured by what it controls. Used by the controls guide and
// by the on-screen keyboard map; keys light up while they are held. Keep KEYBOARD_ROWS in step
// with teleop/keyboardTeleop.ts and the page's own shortcuts in main.ts.
import type { ControlGroup } from "../teleop/gamepadTeleop.ts";

export interface KeyCap {
  label: string;
  /** KeyboardEvent.code values that light this key up (defaults to "Key" + label for letters). */
  codes?: string[];
  group?: ControlGroup;
  hint?: string;
  widthUnits?: number;
}

export const KEYBOARD_ROWS: KeyCap[][] = [
  [
    { label: "Q", group: "wrist", hint: "rotate ⟲" },
    { label: "W", group: "arm", hint: "forward" },
    { label: "E", group: "wrist", hint: "rotate ⟳" },
    { label: "R", group: "arm", hint: "up" },
    { label: "T" },
    { label: "Y" },
    { label: "U", group: "base", hint: "turn left" },
    { label: "I", group: "base", hint: "drive fwd" },
    { label: "O", group: "base", hint: "turn right" },
    { label: "P", group: "view", hint: "plants" },
    { label: "[", codes: ["BracketLeft"], group: "base", hint: "prev stop" },
    { label: "]", codes: ["BracketRight"], group: "base", hint: "next stop" },
  ],
  [
    { label: "A", group: "arm", hint: "left" },
    { label: "S", group: "arm", hint: "back" },
    { label: "D", group: "arm", hint: "right" },
    { label: "F", group: "arm", hint: "down" },
    { label: "G", group: "view", hint: "pad map" },
    { label: "H", group: "arm", hint: "ready pose" },
    { label: "J", group: "base", hint: "slide left" },
    { label: "K", group: "base", hint: "drive back" },
    { label: "L", group: "base", hint: "slide right" },
    { label: "?", codes: ["Slash"], group: "view", hint: "this guide" },
  ],
  [
    { label: "Shift", codes: ["ShiftLeft", "ShiftRight"], group: "modifier", hint: "precise", widthUnits: 1.8 },
    { label: "Z", group: "wrist", hint: "tilt down" },
    { label: "X" },
    { label: "C", group: "wrist", hint: "tilt out" },
    { label: "V", group: "view", hint: "camera" },
    { label: "B", group: "view", hint: "key map" },
    { label: "N", group: "view", hint: "AI panel" },
    { label: "M", group: "view", hint: "wrist view" },
  ],
  [{ label: "Space", codes: ["Space"], group: "grip", hint: "grip / release", widthUnits: 6 }],
];

const codesFor = (key: KeyCap) => key.codes ?? (/^[A-Z]$/.test(key.label) ? [`Key${key.label}`] : []);

export interface KeyboardDiagram {
  element: HTMLElement;
}

export function createKeyboardDiagram(extraClass = ""): KeyboardDiagram {
  const element = document.createElement("div");
  element.className = `guide-keyboard ${extraClass}`.trim();
  element.innerHTML = KEYBOARD_ROWS.map(
    (row) =>
      `<div class="guide-key-row">${row
        .map((key) => `<div class="guide-key ${key.group ? `group-${key.group}` : "unused"}" data-codes="${codesFor(key).join(" ")}" style="--key-units:${key.widthUnits ?? 1}"><span class="guide-key-label">${key.label}</span>${key.hint ? `<span class="guide-key-hint">${key.hint}</span>` : ""}</div>`)
        .join("")}</div>`,
  ).join("");
  const keysByCode = new Map<string, HTMLElement>();
  for (const keyElement of element.querySelectorAll<HTMLElement>(".guide-key")) {
    for (const code of (keyElement.dataset.codes ?? "").split(" ").filter(Boolean)) keysByCode.set(code, keyElement);
  }
  window.addEventListener("keydown", (event) => keysByCode.get(event.code)?.classList.add("pressed"));
  window.addEventListener("keyup", (event) => keysByCode.get(event.code)?.classList.remove("pressed"));
  window.addEventListener("blur", () => {
    for (const keyElement of keysByCode.values()) keyElement.classList.remove("pressed");
  });
  return { element };
}
