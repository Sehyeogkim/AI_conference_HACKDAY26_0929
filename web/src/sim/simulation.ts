// The simulation core: MuJoCo (WebAssembly) running the generated tomato-path scene at a fixed
// timestep, independent of the screen's frame rate. No DOM access, so it also runs in Node.
//
// Each control step (50 Hz) takes an operator command (cart station target, hand target relative
// to the cart, gripper yaw, gripper open/closed), turns the hand target into arm joint targets with
// damped-least-squares inverse kinematics on MuJoCo's Jacobian, then advances physics by
// several fixed physics steps. After every physics step it checks each tomato's stem weld: when the
// constraint force passes a threshold the weld is switched off and the tomato comes free.
import loadMujoco from "@mujoco/mujoco";
import type { FarmLayout } from "../farm/farmLayout.ts";
import { ARM_REST_POSTURE, DampedLeastSquaresArmController, type ArmController, type ArmState } from "../robot/armController.ts";
import { ARM_PREFIX, CART_ACTUATORS, CART_JOINTS, PANDA_MODEL_FILE, buildSceneXml, tomatoGripName, tomatoWeldName } from "./sceneXml.ts";

// The bindings' TypeScript types are very large; the simulation uses a small, explicit surface.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MujocoModule = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MjModel = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MjData = any;

export const CONTROL_RATE_HZ = 50;
/** Fallback stem detach force; each tomato's own `detachForceN` (by ripeness) takes precedence. */
export const DETACH_FORCE_N = 8;
/** Grasp assist: closing the gripper holds a tomato whose centre is this close to the fingertips. */
export const GRASP_ASSIST_RADIUS_M = 0.035;
/** MuJoCo stores this many numbers per equality constraint in eq_data (mjNEQDATA). */
const EQUALITY_DATA_SIZE = 11;
/** Hand pointing straight down; gripper centre this far beyond the hand body's origin. */
const GRIPPER_CENTRE_OFFSET_M = 0.103;
const ARM_JOINT_COUNT = 7;

export interface OperatorCommand {
  /** Cart base target in the world: x along the path, y across it (metres), heading (radians). */
  baseTarget: [number, number, number];
  /** Gripper centre target in the cart's own frame (metres: forward, left, up from the cart origin). */
  handTargetInCart: [number, number, number];
  /** Wrist rotation about the vertical axis, relative to the cart's heading (radians). */
  gripperYaw: number;
  /** Wrist tilt: 0 = gripper pointing straight down, π/2 = pointing out horizontally (radians). */
  gripperPitch: number;
  gripperOpen: boolean;
}

/** Fastest the hand target may glide towards a goal (keeps the gripper from slamming into fruit). */
export const HAND_TARGET_SPEED_M_PER_S = 0.4;

export type SimulationEvent =
  | { kind: "detach"; time: number; tomato: number; forceN: number }
  | { kind: "grasp"; time: number; tomato: number }
  | { kind: "release"; time: number; tomato: number }
  | { kind: "harvested"; time: number; tomato: number; ripeness: string }
  | { kind: "dropped"; time: number; tomato: number };

export interface FileSource {
  /** Returns the bytes of a file of the Menagerie Panda package, by path relative to the package. */
  readPandaFile(relativePath: string): Promise<Uint8Array>;
  pandaAssetList(): Promise<string[]>;
}

export class TomatoHarvestSimulation {
  readonly mujoco: MujocoModule;
  readonly model: MjModel;
  readonly data: MjData;
  readonly layout: FarmLayout;
  readonly sceneXml: string;
  readonly physicsStepsPerControl: number;
  readonly events: SimulationEvent[] = [];

  command: OperatorCommand;
  /**
   * Where the operator wants the hand (relative to the cart). Each control step the commanded hand
   * target glides towards it at a limited speed; the glided target is what gets recorded.
   */
  handGoalInCart: [number, number, number] | null = null;
  controlStep = 0;

  private readonly armQposAddresses: number[];
  private readonly armDofAddresses: number[];
  private readonly armActuatorIds: number[];
  private readonly armJointRanges: Array<[number, number]>;
  private readonly gripperActuatorId: number;
  private readonly cartActuatorIds: number[];
  private readonly cartQposAddresses: number[];
  private readonly handBodyId: number;
  private readonly weldIds: number[];
  private readonly gripIds: number[];
  /** Tomato currently held by the grasp assist, if any. */
  private heldTomato: number | null = null;
  private previousGripperOpen = true;
  private readonly tomatoBodyIds: number[];
  private readonly tomatoState: Array<"attached" | "free" | "harvested">;
  private readonly armController: ArmController;

  private constructor(mujoco: MujocoModule, model: MjModel, layout: FarmLayout, sceneXml: string, armController: ArmController) {
    this.mujoco = mujoco;
    this.model = model;
    this.data = new mujoco.MjData(model);
    this.layout = layout;
    this.sceneXml = sceneXml;
    this.physicsStepsPerControl = Math.round(1 / CONTROL_RATE_HZ / model.opt.timestep);

    const id = (type: number, name: string) => {
      const found = mujoco.mj_name2id(model, type, name);
      if (found < 0) throw new Error(`MuJoCo scene is missing ${name}`);
      return found;
    };
    const obj = mujoco.mjtObj;
    const jointIds = Array.from({ length: ARM_JOINT_COUNT }, (_, index) => id(obj.mjOBJ_JOINT.value, `${ARM_PREFIX}joint${index + 1}`));
    this.armQposAddresses = jointIds.map((jointId) => model.jnt_qposadr[jointId]);
    this.armDofAddresses = jointIds.map((jointId) => model.jnt_dofadr[jointId]);
    this.armJointRanges = jointIds.map((jointId) => [model.jnt_range[jointId * 2], model.jnt_range[jointId * 2 + 1]]);
    this.armActuatorIds = Array.from({ length: ARM_JOINT_COUNT }, (_, index) => id(obj.mjOBJ_ACTUATOR.value, `${ARM_PREFIX}actuator${index + 1}`));
    this.gripperActuatorId = id(obj.mjOBJ_ACTUATOR.value, `${ARM_PREFIX}actuator8`);
    this.cartActuatorIds = CART_ACTUATORS.map((name) => id(obj.mjOBJ_ACTUATOR.value, name));
    this.cartQposAddresses = CART_JOINTS.map((name) => model.jnt_qposadr[id(obj.mjOBJ_JOINT.value, name)]);
    this.handBodyId = id(obj.mjOBJ_BODY.value, `${ARM_PREFIX}hand`);
    this.weldIds = layout.tomatoes.map((tomato) => id(obj.mjOBJ_EQUALITY.value, tomatoWeldName(tomato.index)));
    this.gripIds = layout.tomatoes.map((tomato) => id(obj.mjOBJ_EQUALITY.value, tomatoGripName(tomato.index)));
    this.tomatoBodyIds = layout.tomatoes.map((tomato) => id(obj.mjOBJ_BODY.value, tomato.name));
    this.tomatoState = layout.tomatoes.map(() => "attached");
    this.armController = armController;

    const startX = layout.cart.stationsX[0] ?? 0;
    this.command = { baseTarget: [startX, 0, 0], handTargetInCart: [0.55, 0, 1.15], gripperYaw: 0, gripperPitch: 0, gripperOpen: true };
    this.reset();
  }

  /**
   * Build the simulation. `armController` turns hand targets into arm joint targets; pass your own
   * to replace the built-in damped-least-squares IK (see web/src/robot/armController.ts).
   */
  static async create(layout: FarmLayout, files: FileSource, options: { armController?: ArmController; mujocoOptions?: Record<string, unknown> } = {}): Promise<TomatoHarvestSimulation> {
    const mujoco: MujocoModule = await loadMujoco(options.mujocoOptions);
    const vfs = new mujoco.MjVFS();
    const [pandaXml, assetNames] = await Promise.all([files.readPandaFile(PANDA_MODEL_FILE), files.pandaAssetList()]);
    vfs.addBuffer(PANDA_MODEL_FILE, strengthenGripper(pandaXml));
    const assetBytes = await Promise.all(assetNames.map((name) => files.readPandaFile(`assets/${name}`)));
    assetNames.forEach((name, index) => vfs.addBuffer(`assets/${name}`, assetBytes[index]));
    const sceneXml = buildSceneXml(layout);
    const model = mujoco.MjModel.from_xml_string(sceneXml, vfs);
    vfs.delete();
    return new TomatoHarvestSimulation(mujoco, model, layout, sceneXml, options.armController ?? new DampedLeastSquaresArmController());
  }

  get time(): number {
    return this.data.time;
  }

  reset(): void {
    this.mujoco.mj_resetData(this.model, this.data);
    const qpos = this.data.qpos;
    const startX = this.layout.cart.stationsX[0] ?? 0;
    this.cartQposAddresses.forEach((address, index) => (qpos[address] = index === 0 ? startX : 0));
    ARM_REST_POSTURE.forEach((angle, index) => (qpos[this.armQposAddresses[index]!] = angle));
    this.cartActuatorIds.forEach((actuator, index) => (this.data.ctrl[actuator] = index === 0 ? startX : 0));
    ARM_REST_POSTURE.forEach((angle, index) => (this.data.ctrl[this.armActuatorIds[index]!] = angle));
    this.data.ctrl[this.gripperActuatorId] = 255;
    const initiallyActive = new Array(this.model.neq).fill(1);
    for (const gripId of this.gripIds) initiallyActive[gripId] = 0;
    this.writeEqualityActive(initiallyActive);
    this.heldTomato = null;
    this.previousGripperOpen = true;
    this.tomatoState.fill("attached");
    this.events.length = 0;
    this.controlStep = 0;
    this.mujoco.mj_forward(this.model, this.data);
    const hand = this.gripperCentreWorld();
    this.command = { baseTarget: [startX, 0, 0], handTargetInCart: [hand[0] - startX, hand[1], hand[2]], gripperYaw: 0, gripperPitch: 0, gripperOpen: true };
    this.handGoalInCart = null;
  }

  cartX(): number {
    return this.data.qpos[this.cartQposAddresses[0]!];
  }

  /** Current cart pose in the world: [x, y, heading]. */
  cartPose(): [number, number, number] {
    const qpos = this.data.qpos;
    return [qpos[this.cartQposAddresses[0]!], qpos[this.cartQposAddresses[1]!], qpos[this.cartQposAddresses[2]!]];
  }

  /** Convert a point in the cart's frame to the world, for a given cart pose. */
  static cartToWorld(pose: readonly number[], local: readonly number[]): [number, number, number] {
    const cos = Math.cos(pose[2]!);
    const sin = Math.sin(pose[2]!);
    return [pose[0]! + cos * local[0]! - sin * local[1]!, pose[1]! + sin * local[0]! + cos * local[1]!, local[2]!];
  }

  /** Convert a world point to the cart's frame, for a given cart pose. */
  static worldToCart(pose: readonly number[], world: readonly number[]): [number, number, number] {
    const cos = Math.cos(pose[2]!);
    const sin = Math.sin(pose[2]!);
    const dx = world[0]! - pose[0]!;
    const dy = world[1]! - pose[1]!;
    return [cos * dx + sin * dy, -sin * dx + cos * dy, world[2]!];
  }

  tomatoStatus(index: number): "attached" | "free" | "harvested" {
    return this.tomatoState[index]!;
  }

  /** What an arm controller may read about the arm (live views into MuJoCo's state). */
  armState(): ArmState {
    return {
      mujoco: this.mujoco,
      model: this.model,
      data: this.data,
      armQposAddresses: this.armQposAddresses,
      armDofAddresses: this.armDofAddresses,
      armJointRanges: this.armJointRanges,
      handBodyId: this.handBodyId,
      gripperCentreWorld: this.gripperCentreWorld(),
    };
  }

  /** World position of the point between the fingertips. */
  gripperCentreWorld(): [number, number, number] {
    const position = this.data.xpos;
    const rotation = this.data.xmat;
    const base = this.handBodyId * 3;
    const matrix = this.handBodyId * 9;
    return [
      position[base] + rotation[matrix + 2] * GRIPPER_CENTRE_OFFSET_M,
      position[base + 1] + rotation[matrix + 5] * GRIPPER_CENTRE_OFFSET_M,
      position[base + 2] + rotation[matrix + 8] * GRIPPER_CENTRE_OFFSET_M,
    ];
  }

  handTargetWorld(): [number, number, number] {
    return TomatoHarvestSimulation.cartToWorld(this.cartPose(), this.command.handTargetInCart);
  }

  /** Joint targets sent to the arm, gripper, and cart actuators (recorded as the low-level action). */
  actuatorTargets(): number[] {
    return Array.from(this.data.ctrl as Float64Array);
  }

  /** Advance one control step: IK, actuator targets, then fixed physics steps. */
  controlTick(): void {
    this.applyCommand();
    for (let step = 0; step < this.physicsStepsPerControl; step += 1) {
      this.mujoco.mj_step(this.model, this.data);
      this.checkStemWelds();
    }
    this.updateHarvest();
    this.controlStep += 1;
  }

  private applyCommand(): void {
    if (this.handGoalInCart) {
      const target = this.command.handTargetInCart;
      const delta = this.handGoalInCart.map((value, axis) => value - target[axis]!);
      const distance = Math.hypot(...delta);
      const maximumStep = HAND_TARGET_SPEED_M_PER_S / CONTROL_RATE_HZ;
      const fraction = distance > maximumStep ? maximumStep / distance : 1;
      this.command.handTargetInCart = [target[0] + delta[0]! * fraction, target[1] + delta[1]! * fraction, target[2] + delta[2]! * fraction];
      if (fraction === 1) this.handGoalInCart = null;
    }
    this.updateGraspAssist();
    const ctrl = this.data.ctrl;
    this.cartActuatorIds.forEach((actuator, index) => (ctrl[actuator] = this.command.baseTarget[index]!));
    ctrl[this.gripperActuatorId] = this.command.gripperOpen ? 255 : 0;
    const yawWorld = this.command.gripperYaw + this.cartPose()[2];
    const jointTargets = this.armController.jointTargets(this.armState(), { positionWorld: this.handTargetWorld(), yawWorld, pitch: this.command.gripperPitch });
    jointTargets.forEach((angle, index) => (ctrl[this.armActuatorIds[index]!] = angle));
  }

  /**
   * Grasp assist. When the gripper closes, the nearest tomato within reach of the fingertips is
   * held in the hand where it is (a hand–tomato point constraint anchored at its current offset).
   * Opening the gripper releases it. The stem still has to be pulled free by force.
   */
  private updateGraspAssist(): void {
    const open = this.command.gripperOpen;
    if (open && this.heldTomato !== null) {
      this.setEqualityActive(this.gripIds[this.heldTomato]!, false);
      this.events.push({ kind: "release", time: this.data.time, tomato: this.heldTomato });
      this.heldTomato = null;
    }
    if (!open && this.previousGripperOpen && this.heldTomato === null) {
      const gripper = this.gripperCentreWorld();
      const positions = this.data.xpos;
      let nearest: number | null = null;
      let nearestDistance = GRASP_ASSIST_RADIUS_M;
      this.tomatoBodyIds.forEach((bodyId, index) => {
        if (this.tomatoState[index] === "harvested") return;
        const distance = Math.hypot(positions[bodyId * 3] - gripper[0], positions[bodyId * 3 + 1] - gripper[1], positions[bodyId * 3 + 2] - gripper[2]);
        if (distance < nearestDistance) {
          nearest = index;
          nearestDistance = distance;
        }
      });
      if (nearest !== null) {
        const index: number = nearest;
        const bodyId = this.tomatoBodyIds[index]!;
        const hand = this.handBodyId;
        const rotation = this.data.xmat;
        const offset = [0, 1, 2].map((axis) => positions[bodyId * 3 + axis] - positions[hand * 3 + axis]);
        // Tomato centre in the hand's frame: Rᵀ (p_tomato − p_hand).
        const local = [0, 1, 2].map((column) => rotation[hand * 9 + column] * offset[0]! + rotation[hand * 9 + 3 + column] * offset[1]! + rotation[hand * 9 + 6 + column] * offset[2]!);
        const equalityData = this.model.eq_data;
        const base = this.gripIds[index]! * EQUALITY_DATA_SIZE;
        for (let axis = 0; axis < 3; axis += 1) {
          equalityData[base + axis] = 0;
          equalityData[base + 3 + axis] = local[axis]!;
        }
        this.setEqualityActive(this.gripIds[index]!, true);
        this.heldTomato = index;
        this.events.push({ kind: "grasp", time: this.data.time, tomato: index });
      }
    }
    this.previousGripperOpen = open;
  }

  /** Switch off a tomato's stem weld once the pull on it passes the threshold. */
  private checkStemWelds(): void {
    const { data, model } = this;
    const nefc = data.nefc;
    if (nefc === 0) return;
    const efcType = data.efc_type;
    const efcId = data.efc_id;
    const efcForce = data.efc_force;
    const equalityType = this.mujoco.mjtConstraint.mjCNSTR_EQUALITY.value;
    const forceSquaredByWeld = new Map<number, number>();
    const rowsSeen = new Map<number, number>();
    for (let row = 0; row < nefc; row += 1) {
      if (efcType[row] !== equalityType) continue;
      const weldId = efcId[row];
      const seen = rowsSeen.get(weldId) ?? 0;
      rowsSeen.set(weldId, seen + 1);
      if (seen < 3) forceSquaredByWeld.set(weldId, (forceSquaredByWeld.get(weldId) ?? 0) + efcForce[row] * efcForce[row]);
    }
    this.weldIds.forEach((weldId, tomatoIndex) => {
      if (this.tomatoState[tomatoIndex] !== "attached") return;
      const force = Math.sqrt(forceSquaredByWeld.get(weldId) ?? 0);
      if (force > (this.layout.tomatoes[tomatoIndex]?.detachForceN ?? DETACH_FORCE_N)) {
        this.setEqualityActive(weldId, false);
        this.tomatoState[tomatoIndex] = "free";
        this.events.push({ kind: "detach", time: data.time, tomato: tomatoIndex, forceN: Number(force.toFixed(2)) });
      }
    });
    void model;
  }

  /** A free tomato resting inside the basket counts as harvested; one on the ground as dropped. */
  private updateHarvest(): void {
    const basket = this.layout.cart.basket;
    const pose = this.cartPose();
    const positions = this.data.xpos;
    this.tomatoBodyIds.forEach((bodyId, index) => {
      if (this.tomatoState[index] !== "free") return;
      const local = TomatoHarvestSimulation.worldToCart(pose, [positions[bodyId * 3], positions[bodyId * 3 + 1], positions[bodyId * 3 + 2]]);
      const x = local[0] - basket.centre[0];
      const y = local[1] - basket.centre[1];
      const z = local[2];
      const inBasket = Math.abs(x) < basket.innerLengthM / 2 && Math.abs(y) < basket.innerWidthM / 2 && z < basket.centre[2] + basket.heightM && z > basket.centre[2];
      if (inBasket) {
        this.tomatoState[index] = "harvested";
        this.events.push({ kind: "harvested", time: this.data.time, tomato: index, ripeness: this.layout.tomatoes[index]!.ripeness });
      } else if (z < 0.05) {
        const alreadyDropped = this.events.some((event) => event.kind === "dropped" && event.tomato === index);
        if (!alreadyDropped) this.events.push({ kind: "dropped", time: this.data.time, tomato: index });
      }
    });
  }

  /** Compact physics state for playback: joint positions plus which stem welds are still on. */
  snapshot(): { qpos: number[]; attached: number[] } {
    return {
      qpos: Array.from(this.data.qpos as Float64Array, (value) => Number(value.toFixed(5))),
      attached: (() => {
        const active = this.readEqualityActive();
        return this.weldIds.map((weldId) => active[weldId]!);
      })(),
    };
  }

  // The WASM bindings cannot expose MjData.eq_active (a boolean array) directly, so equality
  // on/off flags are read and written through mj_getState / mj_setState with mjSTATE_EQ_ACTIVE.
  private readEqualityActive(): number[] {
    const spec = this.mujoco.mjtState.mjSTATE_EQ_ACTIVE.value;
    const buffer = new this.mujoco.DoubleBuffer(this.model.neq);
    try {
      this.mujoco.mj_getState(this.model, this.data, buffer, spec);
      return Array.from(buffer.GetView() as Float64Array);
    } finally {
      buffer.delete();
    }
  }

  private writeEqualityActive(values: number[]): void {
    this.mujoco.mj_setState(this.model, this.data, values, this.mujoco.mjtState.mjSTATE_EQ_ACTIVE.value);
  }

  private setEqualityActive(equalityId: number, active: boolean): void {
    const values = this.readEqualityActive();
    values[equalityId] = active ? 1 : 0;
    this.writeEqualityActive(values);
  }

  /** Full MuJoCo integration state (for exact restarts and later re-simulation checks). */
  fullState(): number[] {
    const spec = this.mujoco.mjtState.mjSTATE_INTEGRATION.value;
    const size = this.mujoco.mj_stateSize(this.model, spec);
    const buffer = new this.mujoco.DoubleBuffer(size);
    try {
      this.mujoco.mj_getState(this.model, this.data, buffer, spec);
      return Array.from(buffer.GetView() as Float64Array);
    } finally {
      buffer.delete();
    }
  }

  /** Pose the scene from a recorded snapshot (playback: no physics is stepped). */
  applySnapshot(qpos: number[], attached: number[]): void {
    const target = this.data.qpos;
    for (let index = 0; index < qpos.length; index += 1) target[index] = qpos[index]!;
    const active = this.readEqualityActive();
    this.weldIds.forEach((weldId, index) => (active[weldId] = attached[index] ?? 1));
    this.writeEqualityActive(active);
    this.mujoco.mj_kinematics(this.model, this.data);
  }
}

/**
 * Menagerie's gripper actuator squeezes with only about 2 N on a 4 cm object (a position servo with
 * stiffness 100 N/m on the finger tendon). Raise the stiffness tenfold (about 20 N on a tomato,
 * still within the actuator's ±100 N force range) so a gripped tomato does not slip out.
 */
function strengthenGripper(pandaXml: Uint8Array): Uint8Array {
  const text = new TextDecoder().decode(pandaXml);
  const original = 'gainprm="0.01568627451 0 0" biasprm="0 -100 -10"';
  if (!text.includes(original)) throw new Error("Unexpected Panda gripper actuator definition");
  return new TextEncoder().encode(text.replace(original, 'gainprm="0.1568627451 0 0" biasprm="0 -1000 -60"'));
}
