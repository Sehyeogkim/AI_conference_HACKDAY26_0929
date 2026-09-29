// Keyboard and mouse teleoperation. Keys move the gripper target (relative to the cart), turn the
// gripper, open or close it, and send the cart to the previous or next stop. Every change ends up
// in the simulation's operator command, which is what the recording stores.
//
//   W / S   hand forward / back along the path      A / D   hand left / right (towards a row)
//   R / F   hand up / down                          Q / E   turn the gripper
//   Space   open / close the gripper                [ / ]   cart to previous / next stop
//   H       hand back to the ready pose             click   glide above a tomato
import type { FarmLayout } from "../farm/farmLayout.ts";
import type { TomatoHarvestSimulation } from "../sim/simulation.ts";

const HAND_SPEED_M_PER_S = 0.35;
const YAW_SPEED_RAD_PER_S = 1.4;
/** Reachable box for the gripper target, relative to the cart origin. */
const HAND_LIMITS = { x: [-0.55, 1.0], y: [-0.85, 0.85], z: [0.5, 1.6] } as const;
const MOVEMENT_KEYS = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "KeyR", "KeyF", "KeyQ", "KeyE"]);

export class KeyboardTeleop {
  private readonly held = new Set<string>();
  private readonly simulation: TomatoHarvestSimulation;
  private readonly layout: FarmLayout;
  private readonly readyPose: [number, number, number];

  constructor(simulation: TomatoHarvestSimulation, layout: FarmLayout) {
    this.simulation = simulation;
    this.layout = layout;
    this.readyPose = [...simulation.command.handTargetInCart];
    window.addEventListener("keydown", (event) => {
      if ((event.target as HTMLElement).tagName === "TEXTAREA" || (event.target as HTMLElement).tagName === "INPUT") return;
      if (MOVEMENT_KEYS.has(event.code)) {
        this.held.add(event.code);
        this.simulation.handGoalInCart = null;
      }
      if (event.repeat) return;
      if (event.code === "Space") {
        event.preventDefault();
        this.simulation.command.gripperOpen = !this.simulation.command.gripperOpen;
      }
      if (event.code === "BracketRight") this.moveToStation(1);
      if (event.code === "BracketLeft") this.moveToStation(-1);
      if (event.code === "KeyH") this.simulation.handGoalInCart = [...this.readyPose];
    });
    window.addEventListener("keyup", (event) => this.held.delete(event.code));
    window.addEventListener("blur", () => this.held.clear());
  }

  applyHeldKeys(dt: number): void {
    if (this.held.size === 0) return;
    const command = this.simulation.command;
    const step = HAND_SPEED_M_PER_S * dt;
    const axis = (plus: string, minus: string) => (this.held.has(plus) ? 1 : 0) - (this.held.has(minus) ? 1 : 0);
    const target = command.handTargetInCart;
    const clamp = (value: number, [low, high]: readonly [number, number]) => Math.min(high, Math.max(low, value));
    command.handTargetInCart = [
      clamp(target[0] + axis("KeyW", "KeyS") * step, HAND_LIMITS.x),
      clamp(target[1] + axis("KeyA", "KeyD") * step, HAND_LIMITS.y),
      clamp(target[2] + axis("KeyR", "KeyF") * step, HAND_LIMITS.z),
    ];
    command.gripperYaw += axis("KeyQ", "KeyE") * YAW_SPEED_RAD_PER_S * dt;
  }

  moveToStation(direction: 1 | -1): void {
    const stations = this.layout.cart.stationsX;
    const current = this.simulation.command.cartTargetX;
    const next = direction > 0 ? stations.find((x) => x > current + 1e-3) : [...stations].reverse().find((x) => x < current - 1e-3);
    if (next !== undefined) this.simulation.command.cartTargetX = next;
  }

  /** Glide the open gripper to just above a tomato (drives the cart closer first if needed). */
  approachTomato(index: number): void {
    const tomato = this.layout.tomatoes[index];
    if (!tomato || this.simulation.tomatoStatus(index) !== "attached") return;
    const command = this.simulation.command;
    const stations = this.layout.cart.stationsX;
    const armX = this.layout.cart.armMounts[0]!.position[0];
    const relativeX = tomato.position[0] - command.cartTargetX;
    if (relativeX < armX - 0.35 || relativeX > armX + 0.45) {
      command.cartTargetX = stations.reduce((best, x) => (Math.abs(x + armX - tomato.position[0]) < Math.abs(best + armX - tomato.position[0]) ? x : best));
    }
    command.gripperOpen = true;
    command.gripperYaw = 0;
    this.simulation.handGoalInCart = [tomato.position[0] - command.cartTargetX, tomato.position[1], tomato.position[2] + 0.11];
  }
}
