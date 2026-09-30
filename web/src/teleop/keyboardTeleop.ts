// Keyboard and mouse teleoperation. Arm keys move the gripper target in the cart's own frame
// (so "forward" is always the way the cart faces); base keys drive the wheeled cart; every change
// ends up in the simulation's operator command, which is what the recording stores.
//
//   Arm                                         Wheeled base
//   W / S   arm forward / backward              I / K   drive forward / backward
//   A / D   arm left / right                    J / L   drive sideways left / right
//   R / F   arm up / down                       U / O   turn left / right
//   Q / E   rotate the wrist left / right       [ / ]   self-drive to the previous / next stop
//   Z / C   tilt the wrist (down ↔ outward)
//   Space   open / close the gripper            Shift + any movement key: precise (slow)
//   H       arm back to the ready pose          click a tomato: glide the open gripper above it
import type { FarmLayout } from "../farm/farmLayout.ts";
import { TomatoHarvestSimulation } from "../sim/simulation.ts";

const ARM_SPEED_M_PER_S = 0.35;
const WRIST_TURN_RAD_PER_S = 1.4;
const WRIST_TILT_RAD_PER_S = 1.0;
const BASE_SPEED_M_PER_S = 0.5;
const BASE_TURN_RAD_PER_S = 0.6;
const PRECISE_FACTOR = 0.25;
const WRIST_TILT_RANGE: readonly [number, number] = [-0.3, 1.6];
/** Reachable box for the gripper target, in the cart's frame. */
const ARM_LIMITS = { x: [-0.55, 1.0], y: [-0.85, 0.85], z: [0.5, 1.6] } as const;
const ARM_KEYS = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "KeyR", "KeyF", "KeyQ", "KeyE", "KeyZ", "KeyC"]);
const BASE_KEYS = new Set(["KeyI", "KeyK", "KeyJ", "KeyL", "KeyU", "KeyO"]);
const AI_MOVEMENT_KEYS: Record<string, string> = {
  "move_arm:forward": "KeyW", "move_arm:backward": "KeyS", "move_arm:left": "KeyA", "move_arm:right": "KeyD",
  "move_arm:up": "KeyR", "move_arm:down": "KeyF", "orient_wrist:yaw_left": "KeyQ",
  "orient_wrist:yaw_right": "KeyE", "orient_wrist:pitch_up": "KeyZ", "orient_wrist:pitch_down": "KeyC",
  "move_base:forward": "KeyI", "move_base:backward": "KeyK", "move_base:left": "KeyJ",
  "move_base:right": "KeyL", "move_base:turn_left": "KeyU", "move_base:turn_right": "KeyO",
};

const clamp = (value: number, [low, high]: readonly [number, number]) => Math.min(high, Math.max(low, value));

export class KeyboardTeleop {
  private readonly held = new Set<string>();
  private readonly simulation: TomatoHarvestSimulation;
  private readonly layout: FarmLayout;
  private readonly readyPose: [number, number, number];
  private precise = false;
  private humanEnabled = true;
  /** How far the cart may move sideways from the path's centre line. */
  private readonly baseSidewaysLimitM: number;

  constructor(simulation: TomatoHarvestSimulation, layout: FarmLayout) {
    this.simulation = simulation;
    this.layout = layout;
    this.readyPose = [...simulation.command.handTargetInCart];
    this.baseSidewaysLimitM = Math.max(0.05, layout.parameters.pathWidthM / 2 - layout.cart.widthM / 2 - 0.05);
    window.addEventListener("keydown", (event) => {
      if (!this.humanEnabled) return;
      const tag = (event.target as HTMLElement).tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      this.precise = event.shiftKey;
      if (ARM_KEYS.has(event.code) || BASE_KEYS.has(event.code)) {
        this.held.add(event.code);
        if (ARM_KEYS.has(event.code)) this.simulation.handGoalInCart = null;
      }
      if (event.repeat) return;
      if (event.code === "Space") {
        event.preventDefault();
        this.toggleGripper();
      }
      if (event.code === "BracketRight") this.selfDriveToStop(1);
      if (event.code === "BracketLeft") this.selfDriveToStop(-1);
      if (event.code === "KeyH") this.returnToReadyPose();
    });
    window.addEventListener("keyup", (event) => {
      this.held.delete(event.code);
      this.precise = event.shiftKey;
    });
    window.addEventListener("blur", () => this.held.clear());
  }

  applyHeldKeys(dt: number): void {
    if (!this.humanEnabled) return;
    this.applyKeys(this.held, dt, this.precise ? PRECISE_FACTOR : 1);
  }

  setHumanEnabled(enabled: boolean): void {
    this.humanEnabled = enabled;
    this.held.clear();
    this.precise = false;
  }

  /**
   * Apply movements named by their keyboard keys (the motion vocabulary shared by every input
   * device), scaled by `factor`. Other devices, such as a game controller, drive the robot through
   * this, so they share the keyboard's speeds, limits, and operator command.
   */
  applyMotionKeys(keys: ReadonlySet<string>, dt: number, factor = 1): void {
    if (!this.humanEnabled) return;
    if ([...keys].some((key) => ARM_KEYS.has(key))) this.simulation.handGoalInCart = null;
    this.applyKeys(keys, dt, factor);
  }

  toggleGripper(): void {
    if (!this.humanEnabled) return;
    this.simulation.command.gripperOpen = !this.simulation.command.gripperOpen;
  }

  returnToReadyPose(): void {
    if (!this.humanEnabled) return;
    this.simulation.handGoalInCart = [...this.readyPose];
    this.simulation.command.gripperPitch = 0;
    this.simulation.command.gripperYaw = 0;
  }

  applyAiAction(action: string, direction: string, dt: number): void {
    if (action === "gripper") {
      this.simulation.command.gripperOpen = direction === "open";
      return;
    }
    const key = AI_MOVEMENT_KEYS[`${action}:${direction}`];
    if (!key) return;
    this.simulation.handGoalInCart = null;
    this.applyKeys(new Set([key]), dt, 1);
  }

  private applyKeys(keys: ReadonlySet<string>, dt: number, factor: number): void {
    if (keys.size === 0) return;
    const command = this.simulation.command;
    const axis = (plus: string, minus: string) => ((keys.has(plus) ? 1 : 0) - (keys.has(minus) ? 1 : 0)) * factor * dt;

    const target = command.handTargetInCart;
    command.handTargetInCart = [
      clamp(target[0] + axis("KeyW", "KeyS") * ARM_SPEED_M_PER_S, ARM_LIMITS.x),
      clamp(target[1] + axis("KeyA", "KeyD") * ARM_SPEED_M_PER_S, ARM_LIMITS.y),
      clamp(target[2] + axis("KeyR", "KeyF") * ARM_SPEED_M_PER_S, ARM_LIMITS.z),
    ];
    command.gripperYaw += axis("KeyQ", "KeyE") * WRIST_TURN_RAD_PER_S;
    command.gripperPitch = clamp(command.gripperPitch + axis("KeyC", "KeyZ") * WRIST_TILT_RAD_PER_S, WRIST_TILT_RANGE);

    // Base: forward/sideways in the cart's own frame, turning about its centre.
    const [x, y, heading] = command.baseTarget;
    const forward = axis("KeyI", "KeyK") * BASE_SPEED_M_PER_S;
    const sideways = axis("KeyJ", "KeyL") * BASE_SPEED_M_PER_S;
    const turn = axis("KeyU", "KeyO") * BASE_TURN_RAD_PER_S;
    if (forward || sideways || turn) {
      const stations = this.layout.cart.stationsX;
      command.baseTarget = [
        clamp(x + Math.cos(heading) * forward - Math.sin(heading) * sideways, [(stations[0] ?? 0) - 1, (stations[stations.length - 1] ?? 0) + 1.5]),
        clamp(y + Math.sin(heading) * forward + Math.cos(heading) * sideways, [-this.baseSidewaysLimitM, this.baseSidewaysLimitM]),
        clamp(heading + turn, [-0.6, 0.6]),
      ];
    }
  }

  /** "Self-driving": go to the previous / next stop on the path's centre line, facing along it. */
  selfDriveToStop(direction: 1 | -1): void {
    const stations = this.layout.cart.stationsX;
    const current = this.simulation.command.baseTarget[0];
    const next = direction > 0 ? stations.find((x) => x > current + 1e-3) : [...stations].reverse().find((x) => x < current - 1e-3);
    this.simulation.command.baseTarget = [next ?? current, 0, 0];
  }

  /** Glide the open gripper to just above a tomato (self-drives closer first if needed). */
  approachTomato(index: number): void {
    const tomato = this.layout.tomatoes[index];
    if (!tomato || this.simulation.tomatoStatus(index) !== "attached") return;
    const command = this.simulation.command;
    const armX = this.layout.cart.armMounts[0]!.position[0];
    const relativeX = TomatoHarvestSimulation.worldToCart(command.baseTarget, tomato.position)[0];
    if (relativeX < armX - 0.35 || relativeX > armX + 0.45) {
      const stations = this.layout.cart.stationsX;
      const bestStop = stations.reduce((best, x) => (Math.abs(x + armX - tomato.position[0]) < Math.abs(best + armX - tomato.position[0]) ? x : best));
      command.baseTarget = [bestStop, 0, 0];
    }
    command.gripperOpen = true;
    command.gripperYaw = -command.baseTarget[2];
    command.gripperPitch = 0;
    const above = TomatoHarvestSimulation.worldToCart(command.baseTarget, [tomato.position[0], tomato.position[1], tomato.position[2] + 0.11]);
    this.simulation.handGoalInCart = above;
  }
}
