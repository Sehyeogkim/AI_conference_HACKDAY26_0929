// Arm control: turns a hand target (gripper position plus wrist yaw and tilt, in the world) into
// joint targets for the arm's position servos. This is the seam for robot-arm work: implement
// `ArmController` (for example analytical IK, a learned policy, or a planner) and pass it to
// `TomatoHarvestSimulation.create(..., { armController })`. Nothing else needs to change; the
// recording stores both the hand target (operator command) and the joint targets sent.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

/** A comfortable Panda posture the built-in IK drifts towards when the task leaves freedom. */
export const ARM_REST_POSTURE = [0, -0.3, 0, -2.2, 0, 1.9, 0.785];

export interface ArmState {
  mujoco: Any;
  model: Any;
  /** Live MuJoCo data: qpos, xpos, xmat, and so on. Read only; do not step or write state. */
  data: Any;
  /** Addresses of the arm's 7 joints in qpos and in the velocity (dof) vector. */
  armQposAddresses: number[];
  armDofAddresses: number[];
  armJointRanges: Array<[number, number]>;
  handBodyId: number;
  /** World position of the point between the fingertips. */
  gripperCentreWorld: [number, number, number];
}

export interface HandPoseTarget {
  /** Where the point between the fingertips should be (world metres, z up). */
  positionWorld: [number, number, number];
  /** Wrist heading about the vertical axis (world radians). */
  yawWorld: number;
  /** Wrist tilt: 0 = pointing straight down, π/2 = pointing out horizontally. */
  pitch: number;
}

export interface ArmController {
  /** Called once per 50 Hz control step; returns one target angle per arm joint (radians). */
  jointTargets(arm: ArmState, target: HandPoseTarget): number[];
}

export class DampedLeastSquaresArmController implements ArmController {
  private jacobianPosition: { GetView(): Float64Array; delete(): void } | null = null;
  private jacobianRotation: { GetView(): Float64Array; delete(): void } | null = null;
  private jacobianSize = 0;

  dispose(): void {
    this.jacobianPosition?.delete();
    this.jacobianRotation?.delete();
    this.jacobianPosition = null;
    this.jacobianRotation = null;
  }

  /**
   * One damped-least-squares step from the current joint angles towards the hand target
   * (position of the gripper centre and a downward-pointing gripper with the commanded yaw),
   * with a null-space pull towards a comfortable posture.
   */
  jointTargets(arm: ArmState, target: HandPoseTarget): number[] {
    const { mujoco, model, data } = arm;
    const nv = model.nv;
    if (!this.jacobianPosition || this.jacobianSize !== 3 * nv) {
      this.dispose();
      this.jacobianPosition = new mujoco.DoubleBuffer(3 * nv);
      this.jacobianRotation = new mujoco.DoubleBuffer(3 * nv);
      this.jacobianSize = 3 * nv;
    }
    const gripper = arm.gripperCentreWorld;
    mujoco.mj_jac(model, data, this.jacobianPosition, this.jacobianRotation, gripper, arm.handBodyId);
    const jacobianP = this.jacobianPosition!.GetView();
    const jacobianR = this.jacobianRotation!.GetView();

    const targetPosition = target.positionWorld;
    const positionError = [targetPosition[0] - gripper[0], targetPosition[1] - gripper[1], targetPosition[2] - gripper[2]];
    const positionErrorNorm = Math.hypot(...positionError);
    const maximumStepM = 0.04;
    if (positionErrorNorm > maximumStepM) for (let axis = 0; axis < 3; axis += 1) positionError[axis]! *= maximumStepM / positionErrorNorm;

    // Desired hand axes: the gripper (hand z) points down, tilted by the pitch towards the wrist's
    // yaw direction (world yaw = cart heading + wrist yaw); hand x is perpendicular; y = z × x.
    const yaw = target.yawWorld;
    const pitch = target.pitch;
    const [cosPitch, sinPitch] = [Math.cos(pitch), Math.sin(pitch)];
    const desiredX = [cosPitch * Math.cos(yaw), cosPitch * Math.sin(yaw), sinPitch];
    const desiredZ = [sinPitch * Math.cos(yaw), sinPitch * Math.sin(yaw), -cosPitch];
    const desiredY = [desiredZ[1]! * desiredX[2]! - desiredZ[2]! * desiredX[1]!, desiredZ[2]! * desiredX[0]! - desiredZ[0]! * desiredX[2]!, desiredZ[0]! * desiredX[1]! - desiredZ[1]! * desiredX[0]!];
    const matrix = data.xmat;
    const offset = arm.handBodyId * 9;
    const currentAxis = (column: number) => [matrix[offset + column], matrix[offset + 3 + column], matrix[offset + 6 + column]];
    const rotationError = [0, 0, 0];
    [desiredX, desiredY, desiredZ].forEach((desired, column) => {
      const current = currentAxis(column);
      rotationError[0]! += 0.5 * (current[1]! * desired[2]! - current[2]! * desired[1]!);
      rotationError[1]! += 0.5 * (current[2]! * desired[0]! - current[0]! * desired[2]!);
      rotationError[2]! += 0.5 * (current[0]! * desired[1]! - current[1]! * desired[0]!);
    });
    const error = [...positionError, ...rotationError.map((value) => value * 0.6)];

    // 6×7 Jacobian restricted to the arm's joints.
    const jacobian: number[][] = [];
    for (let row = 0; row < 3; row += 1) jacobian.push(arm.armDofAddresses.map((dof) => jacobianP[row * nv + dof]!));
    for (let row = 0; row < 3; row += 1) jacobian.push(arm.armDofAddresses.map((dof) => jacobianR[row * nv + dof]!));

    const damping = 0.05;
    const jjt = jacobian.map((rowA) => jacobian.map((rowB) => rowA.reduce((sum, value, index) => sum + value * rowB[index]!, 0)));
    for (let index = 0; index < 6; index += 1) jjt[index]![index]! += damping * damping;
    const pseudoInverseTimes = (vector: number[]) => {
      const solved = solveLinearSystem(jjt, vector);
      return Array.from({ length: arm.armDofAddresses.length }, (_, joint) => jacobian.reduce((sum, row, rowIndex) => sum + row[joint]! * solved[rowIndex]!, 0));
    };
    const taskStep = pseudoInverseTimes(error);

    const current = arm.armQposAddresses.map((address) => data.qpos[address] as number);
    const postureStep = current.map((angle, index) => 0.05 * ((ARM_REST_POSTURE[index] ?? angle) - angle));
    const postureInTask = jacobian.map((row) => row.reduce((sum, value, index) => sum + value * postureStep[index]!, 0));
    const postureCorrection = pseudoInverseTimes(postureInTask);
    const nullSpaceStep = postureStep.map((value, index) => value - postureCorrection[index]!);

    return current.map((angle, index) => {
      const [low, high] = arm.armJointRanges[index]!;
      return Math.min(high - 0.01, Math.max(low + 0.01, angle + taskStep[index]! + nullSpaceStep[index]!));
    });
  }
}

/** Solve A x = b for a small dense system (Gaussian elimination with partial pivoting). */
function solveLinearSystem(matrix: number[][], vector: number[]): number[] {
  const size = vector.length;
  const augmented = matrix.map((row, index) => [...row, vector[index]!]);
  for (let column = 0; column < size; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < size; row += 1) if (Math.abs(augmented[row]![column]!) > Math.abs(augmented[pivot]![column]!)) pivot = row;
    [augmented[column], augmented[pivot]] = [augmented[pivot]!, augmented[column]!];
    const pivotRow = augmented[column]!;
    for (let row = column + 1; row < size; row += 1) {
      const factor = augmented[row]![column]! / pivotRow[column]!;
      for (let entry = column; entry <= size; entry += 1) augmented[row]![entry]! -= factor * pivotRow[entry]!;
    }
  }
  const solution = new Array<number>(size).fill(0);
  for (let row = size - 1; row >= 0; row -= 1) {
    let sum = augmented[row]![size]!;
    for (let column = row + 1; column < size; column += 1) sum -= augmented[row]![column]! * solution[column]!;
    solution[row] = sum / augmented[row]![row]!;
  }
  return solution;
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
