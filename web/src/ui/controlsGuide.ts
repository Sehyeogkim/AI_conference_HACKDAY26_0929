// Tutorial-style controls guide: a drawn keyboard with every used key coloured by what it controls,
// a legend, and a short "first harvest" walkthrough. Opens from the Controls button or the ? key,
// and once automatically on a first visit (remembered in localStorage when available).

type KeyGroup = "arm" | "wrist" | "grip" | "base" | "view" | "modifier";

interface KeyCap {
  label: string;
  code?: string;
  group?: KeyGroup;
  hint?: string;
  widthUnits?: number;
}

const GROUP_LABELS: Record<KeyGroup, string> = {
  arm: "Move the gripper",
  wrist: "Rotate and tilt the wrist",
  grip: "Open / close the gripper",
  base: "Drive the cart",
  view: "Camera and view",
  modifier: "Precise (slow) moves",
};

const KEYBOARD_ROWS: KeyCap[][] = [
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
    { label: "[", group: "base", hint: "prev stop" },
    { label: "]", group: "base", hint: "next stop" },
  ],
  [
    { label: "A", group: "arm", hint: "left" },
    { label: "S", group: "arm", hint: "back" },
    { label: "D", group: "arm", hint: "right" },
    { label: "F", group: "arm", hint: "down" },
    { label: "G" },
    { label: "H", group: "arm", hint: "ready pose" },
    { label: "J", group: "base", hint: "slide left" },
    { label: "K", group: "base", hint: "drive back" },
    { label: "L", group: "base", hint: "slide right" },
    { label: "?", group: "view", hint: "this guide" },
  ],
  [
    { label: "Shift", group: "modifier", hint: "precise", widthUnits: 1.8 },
    { label: "Z", group: "wrist", hint: "tilt down" },
    { label: "X" },
    { label: "C", group: "wrist", hint: "tilt out" },
    { label: "V", group: "view", hint: "camera" },
    { label: "B" },
    { label: "N" },
    { label: "M", group: "view", hint: "wrist view" },
  ],
  [{ label: "Space", group: "grip", hint: "grip / release", widthUnits: 6 }],
];

const FIRST_HARVEST_STEPS = [
  "<b>Click</b> a red tomato (it lights up under the mouse). The arm glides above it with the gripper open.",
  "Press <b>F</b> to lower the gripper around the tomato. Hold <b>Shift</b> for fine moves.",
  "Press <b>Space</b> to close the gripper.",
  "Press <b>R</b> to lift and <b>S</b> to pull back: the stem snaps when you pull hard enough (ripe fruit comes off easiest).",
  "Move over the green crate on the cart and press <b>Space</b> to drop it. <b>Ripe harvested</b> goes up.",
  "Drive to the next plants with <b>]</b>, or with <b>I/K/J/L</b>. Leave the green tomatoes on the plant.",
];

const SEEN_STORAGE_KEY = "wefarm.controlsGuideSeen";

export interface ControlsGuide {
  open(): void;
  close(): void;
  toggle(): void;
}

export function createControlsGuide(root: HTMLElement): ControlsGuide {
  const keyboard = KEYBOARD_ROWS.map(
    (row) =>
      `<div class="guide-key-row">${row
        .map((key) => `<div class="guide-key ${key.group ? `group-${key.group}` : "unused"}" style="--key-units:${key.widthUnits ?? 1}"><span class="guide-key-label">${key.label}</span>${key.hint ? `<span class="guide-key-hint">${key.hint}</span>` : ""}</div>`)
        .join("")}</div>`,
  ).join("");
  const legend = (Object.keys(GROUP_LABELS) as KeyGroup[]).map((group) => `<li><span class="guide-swatch group-${group}"></span>${GROUP_LABELS[group]}</li>`).join("");
  const steps = FIRST_HARVEST_STEPS.map((step) => `<li>${step}</li>`).join("");
  root.innerHTML = `
    <div class="guide-card" role="dialog" aria-modal="true" aria-labelledby="guide-title">
      <div class="guide-header">
        <h2 id="guide-title">How to play</h2>
        <button type="button" class="guide-close" aria-label="Close">✕</button>
      </div>
      <div class="guide-body">
        <div class="guide-keyboard">${keyboard}</div>
        <div class="guide-side">
          <ul class="guide-legend">${legend}</ul>
          <h3>Your first harvest</h3>
          <ol class="guide-steps">${steps}</ol>
          <p class="guide-mouse"><b>Mouse:</b> drag to orbit, scroll to zoom, click a tomato to reach for it.</p>
        </div>
      </div>
      <p class="guide-footer">Press <b>?</b> or the <b>Controls</b> button any time. <b>Esc</b> closes.</p>
    </div>`;

  const open = () => {
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

  let seen = false;
  try {
    seen = localStorage.getItem(SEEN_STORAGE_KEY) === "1";
  } catch {
    /* treat as not seen */
  }
  if (!seen) open();
  return { open, close, toggle };
}
