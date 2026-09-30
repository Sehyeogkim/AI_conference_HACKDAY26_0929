// Game-controller teleoperation through the browser Gamepad API. Any controller the browser reports
// with the "standard" button layout works (Xbox, PlayStation, Switch Pro, and look-alikes such as
// the IINE Mini Retro Ananke in its Xbox mode). Buttons are named by their POSITION, not their
// printed letter, because Nintendo-style and Xbox-style controllers print different letters on
// the same positions.
//
// Nothing needs holding: the controller is in ARM mode by default, and the left face button toggles
// CART mode for occasional fine driving. Movement buttons nudge on a tap (about 1 cm) and glide
// when held, starting slowly and speeding up, so there is no separate "precise" button.
//
// Every movement goes through KeyboardTeleop's motion model (the same speeds, limits, and operator
// command as the keyboard), so keyboard, mouse, and controller can be used at the same time, for
// example one person orbiting the camera with the mouse while another drives the arm.
import type { KeyboardTeleop } from "./keyboardTeleop.ts";

export type ControlGroup = "arm" | "wrist" | "grip" | "base" | "view" | "modifier" | "mode";
export type GamepadControlMode = "arm" | "cart";
export type GamepadPressAction = "grip" | "toggleMode" | "previousStop" | "nextStop";

/** Button indices of the W3C "standard" gamepad layout, named by position. */
export const STANDARD_GAMEPAD_BUTTONS = {
  faceBottom: 0,
  faceRight: 1,
  faceLeft: 2,
  faceTop: 3,
  shoulderLeft: 4,
  shoulderRight: 5,
  triggerLeft: 6,
  triggerRight: 7,
  minus: 8,
  plus: 9,
  dpadUp: 12,
  dpadDown: 13,
  dpadLeft: 14,
  dpadRight: 15,
  home: 16,
} as const;
export type GamepadButtonName = keyof typeof STANDARD_GAMEPAD_BUTTONS;

export interface GamepadBinding {
  group: ControlGroup;
  hint: string;
  /** A held movement, named by the keyboard key with the same effect (KeyboardTeleop's motion vocabulary). */
  motionKey?: string;
  /** A one-shot action on press. */
  press?: GamepadPressAction;
}

const SHARED_BINDINGS: Partial<Record<GamepadButtonName, GamepadBinding>> = {
  faceRight: { group: "grip", hint: "grip / release", press: "grip" },
  minus: { group: "base", hint: "previous stop", press: "previousStop" },
  plus: { group: "base", hint: "next stop", press: "nextStop" },
};

/** The controller layout, per mode. The controls guide draws itself from this table. */
export const GAMEPAD_LAYOUT: Record<GamepadControlMode, Partial<Record<GamepadButtonName, GamepadBinding>>> = {
  arm: {
    dpadUp: { group: "arm", hint: "forward", motionKey: "KeyW" },
    dpadDown: { group: "arm", hint: "back", motionKey: "KeyS" },
    dpadLeft: { group: "arm", hint: "left", motionKey: "KeyA" },
    dpadRight: { group: "arm", hint: "right", motionKey: "KeyD" },
    faceTop: { group: "arm", hint: "hand up", motionKey: "KeyR" },
    faceBottom: { group: "arm", hint: "hand down", motionKey: "KeyF" },
    faceLeft: { group: "mode", hint: "cart mode", press: "toggleMode" },
    shoulderLeft: { group: "wrist", hint: "rotate ⟲", motionKey: "KeyQ" },
    shoulderRight: { group: "wrist", hint: "rotate ⟳", motionKey: "KeyE" },
    triggerLeft: { group: "wrist", hint: "tilt down", motionKey: "KeyZ" },
    triggerRight: { group: "wrist", hint: "tilt out", motionKey: "KeyC" },
    ...SHARED_BINDINGS,
  },
  cart: {
    dpadUp: { group: "base", hint: "drive fwd", motionKey: "KeyI" },
    dpadDown: { group: "base", hint: "drive back", motionKey: "KeyK" },
    dpadLeft: { group: "base", hint: "slide left", motionKey: "KeyJ" },
    dpadRight: { group: "base", hint: "slide right", motionKey: "KeyL" },
    faceLeft: { group: "mode", hint: "arm mode", press: "toggleMode" },
    shoulderLeft: { group: "base", hint: "turn left", motionKey: "KeyU" },
    shoulderRight: { group: "base", hint: "turn right", motionKey: "KeyO" },
    ...SHARED_BINDINGS,
  },
};

/** A tap moves as far as this many seconds at full speed: about 1 cm for the hand, 2–3° for the wrist. */
const NUDGE_SECONDS_AT_FULL_SPEED = 0.03;
/** A held button starts gliding after this long (like key repeat), so a tap stays a single nudge. */
const GLIDE_DELAY_S = 0.25;
/** Gliding starts at this fraction of full speed… */
const GLIDE_START_FACTOR = 0.25;
/** …and reaches full speed after this much more holding. */
const GLIDE_RAMP_S = 0.6;
/** Analog sticks (or a D-pad set to "joystick" mode) count as the D-pad beyond this deflection. */
const STICK_AS_DPAD_THRESHOLD = 0.5;

export interface ConnectedGamepad {
  index: number;
  name: string;
  /** False when the browser does not report the standard layout; buttons may then be mislabelled. */
  layoutRecognised: boolean;
  mode: GamepadControlMode;
}

export interface GamepadStatus {
  enabled: boolean;
  gamepads: ConnectedGamepad[];
}

interface GamepadState {
  mode: GamepadControlMode;
  pressed: Set<GamepadButtonName>;
  /** Raw button indices held at the last poll, for logging every press (standard layout or not). */
  rawPressed: Set<number>;
  /** Seconds each movement button has been held while controlling. */
  heldSeconds: Map<GamepadButtonName, number>;
}

/** Human-readable controller name: the browser's id without its "(STANDARD GAMEPAD Vendor… )" suffix. */
export const gamepadDisplayName = (id: string) => id.replace(/\s*\(.*\)\s*$/, "").trim() || "Game controller";

const readPressedButtons = (gamepad: Gamepad): Set<GamepadButtonName> => {
  const pressed = new Set<GamepadButtonName>();
  for (const [name, index] of Object.entries(STANDARD_GAMEPAD_BUTTONS) as [GamepadButtonName, number][]) {
    if (gamepad.buttons[index]?.pressed) pressed.add(name);
  }
  const [stickX = 0, stickY = 0] = gamepad.axes;
  if (stickY < -STICK_AS_DPAD_THRESHOLD) pressed.add("dpadUp");
  if (stickY > STICK_AS_DPAD_THRESHOLD) pressed.add("dpadDown");
  if (stickX < -STICK_AS_DPAD_THRESHOLD) pressed.add("dpadLeft");
  if (stickX > STICK_AS_DPAD_THRESHOLD) pressed.add("dpadRight");
  return pressed;
};

export class GamepadTeleop {
  private readonly teleop: KeyboardTeleop;
  private readonly states = new Map<number, GamepadState>();
  private enabled: boolean;
  private statusKey = "";
  private readonly statusListeners: ((status: GamepadStatus) => void)[] = [];
  private readonly pressListeners: ((pressed: ReadonlySet<GamepadButtonName>, mode: GamepadControlMode) => void)[] = [];

  constructor(teleop: KeyboardTeleop, options: { enabled: boolean }) {
    this.teleop = teleop;
    this.enabled = options.enabled;
    // Console log of what the browser reports, so controller problems can be diagnosed from the logs.
    window.addEventListener("gamepadconnected", (event) => {
      const gamepad = (event as GamepadEvent).gamepad;
      console.info(`[gamepad] connected #${gamepad.index}: "${gamepad.id}" · layout ${gamepad.mapping || "non-standard"} · ${gamepad.buttons.length} buttons · ${gamepad.axes.length} axes`);
    });
    window.addEventListener("gamepaddisconnected", (event) => {
      const gamepad = (event as GamepadEvent).gamepad;
      console.info(`[gamepad] disconnected #${gamepad.index}: "${gamepad.id}"`);
    });
    if (!GamepadTeleop.supported()) console.info("[gamepad] this browser has no Gamepad API");
  }

  static supported(): boolean {
    return typeof navigator !== "undefined" && typeof navigator.getGamepads === "function";
  }

  /** Called whenever the set of controllers, their modes, or the on/off switch changes. */
  onStatusChange(listener: (status: GamepadStatus) => void): void {
    this.statusListeners.push(listener);
    listener(this.status());
  }

  /** Called on every poll with the buttons held on the most recently used controller (for the guide). */
  onButtons(listener: (pressed: ReadonlySet<GamepadButtonName>, mode: GamepadControlMode) => void): void {
    this.pressListeners.push(listener);
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    for (const state of this.states.values()) {
      state.heldSeconds.clear();
      state.mode = "arm";
    }
    this.publishStatus(true);
  }

  status(): GamepadStatus {
    const gamepads: ConnectedGamepad[] = [];
    for (const gamepad of this.connectedGamepads()) {
      gamepads.push({
        index: gamepad.index,
        name: gamepadDisplayName(gamepad.id),
        layoutRecognised: gamepad.mapping === "standard",
        mode: this.states.get(gamepad.index)?.mode ?? "arm",
      });
    }
    return { enabled: this.enabled, gamepads };
  }

  /**
   * Read every controller once per animation frame. Handles one-shot presses (grip, mode, stops)
   * and a tap's nudge. `controlling` is false during replay or AI Mode: buttons are then still
   * read (the guide lights them up) but move nothing.
   */
  poll(controlling: boolean): void {
    const seen = new Set<number>();
    let latestPressed: ReadonlySet<GamepadButtonName> | null = null;
    let latestMode: GamepadControlMode = "arm";
    for (const gamepad of this.connectedGamepads()) {
      seen.add(gamepad.index);
      let state = this.states.get(gamepad.index);
      if (!state) {
        state = { mode: "arm", pressed: new Set(), rawPressed: new Set(), heldSeconds: new Map() };
        this.states.set(gamepad.index, state);
      }
      const pressed = readPressedButtons(gamepad);
      const active = controlling && this.enabled;
      const rawPressed = new Set(gamepad.buttons.flatMap((button, index) => (button.pressed ? [index] : [])));
      for (const index of rawPressed) {
        if (state.rawPressed.has(index)) continue;
        const name = (Object.entries(STANDARD_GAMEPAD_BUTTONS) as [GamepadButtonName, number][]).find(([, standardIndex]) => standardIndex === index)?.[0];
        const binding = name ? GAMEPAD_LAYOUT[state.mode][name] : undefined;
        const effect = !active ? "ignored (controller off, replay, or AI Mode)" : binding ? binding.hint : "no action";
        console.info(`[gamepad] #${gamepad.index} button ${index} (${name ?? "not in the standard layout"}) pressed · ${state.mode} mode · ${effect}`);
      }
      state.rawPressed = rawPressed;
      for (const name of pressed) {
        if (state.pressed.has(name)) continue;
        const binding = GAMEPAD_LAYOUT[state.mode][name];
        if (!active || !binding) continue;
        if (binding.press) this.runPressAction(binding.press, state);
        else if (binding.motionKey) {
          this.teleop.applyMotionKeys(new Set([binding.motionKey]), NUDGE_SECONDS_AT_FULL_SPEED);
          state.heldSeconds.set(name, 0);
        }
      }
      for (const name of [...state.heldSeconds.keys()]) if (!pressed.has(name) || !active) state.heldSeconds.delete(name);
      state.pressed = pressed;
      if (pressed.size > 0 || latestPressed === null) {
        latestPressed = pressed;
        latestMode = state.mode;
      }
    }
    for (const index of [...this.states.keys()]) if (!seen.has(index)) this.states.delete(index);
    for (const listener of this.pressListeners) listener(latestPressed ?? new Set(), latestMode);
    this.publishStatus(false);
  }

  /** Once per control step: held movement buttons glide, starting slowly and speeding up. */
  applyHeldButtons(dt: number): void {
    if (!this.enabled) return;
    for (const state of this.states.values()) {
      for (const [name, heldBefore] of state.heldSeconds) {
        const held = heldBefore + dt;
        state.heldSeconds.set(name, held);
        if (held < GLIDE_DELAY_S) continue;
        const motionKey = GAMEPAD_LAYOUT[state.mode][name]?.motionKey;
        if (!motionKey) continue;
        const factor = Math.min(1, GLIDE_START_FACTOR + ((held - GLIDE_DELAY_S) / GLIDE_RAMP_S) * (1 - GLIDE_START_FACTOR));
        this.teleop.applyMotionKeys(new Set([motionKey]), dt, factor);
      }
    }
  }

  /** One line per controller with what the browser reports right now (troubleshooting). */
  rawReport(): string {
    if (!GamepadTeleop.supported()) return "This browser has no Gamepad API.";
    const gamepads = this.connectedGamepads();
    if (gamepads.length === 0) return "Browser reports no controllers yet (press a button on the controller while this tab is in front).";
    return gamepads
      .map((gamepad) => {
        const held = gamepad.buttons.flatMap((button, index) => (button.pressed ? [index] : []));
        const axes = gamepad.axes.map((value) => value.toFixed(2)).join(", ");
        return `Browser reports #${gamepad.index} "${gamepad.id}" · layout ${gamepad.mapping || "non-standard"} · buttons held: ${held.length ? held.join(", ") : "none"} · axes: ${axes || "none"}`;
      })
      .join("\n");
  }

  /** True when an enabled controller is connected (recorded as part of the operator's device). */
  inUse(): boolean {
    return this.enabled && this.connectedGamepads().length > 0;
  }

  private runPressAction(action: GamepadPressAction, state: GamepadState): void {
    if (action === "grip") this.teleop.toggleGripper();
    else if (action === "previousStop") this.teleop.selfDriveToStop(-1);
    else if (action === "nextStop") this.teleop.selfDriveToStop(1);
    else if (action === "toggleMode") {
      state.mode = state.mode === "arm" ? "cart" : "arm";
      state.heldSeconds.clear();
      this.publishStatus(true);
    }
  }

  private connectedGamepads(): Gamepad[] {
    if (!GamepadTeleop.supported()) return [];
    return navigator.getGamepads().filter((gamepad): gamepad is Gamepad => gamepad !== null && gamepad.connected);
  }

  private publishStatus(force: boolean): void {
    const status = this.status();
    const key = JSON.stringify(status);
    if (!force && key === this.statusKey) return;
    this.statusKey = key;
    for (const listener of this.statusListeners) listener(status);
  }
}
