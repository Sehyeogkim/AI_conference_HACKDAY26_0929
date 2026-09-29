// Generates the MuJoCo scene (MJCF) for the tomato-path task from a farm layout:
// the harvest cart on a driven slide joint, the Menagerie Franka Panda attached to the cart's arm
// post (name prefix "arm0/"), the harvest basket, and every tomato as a free body held to its truss
// by a point ("connect") constraint that the simulation switches off when the tomato is pulled hard
// enough. (A weld to the world does not hold in the MuJoCo 3.14.0 WASM build; a connect does.)
import type { FarmLayout } from "../farm/farmLayout.ts";

export const PANDA_MODEL_FILE = "panda.xml";
/** The cart base moves on three joints (forward, sideways, turn), each with a position servo. */
export const CART_JOINTS = ["cart_x", "cart_y", "cart_yaw"] as const;
export const CART_ACTUATORS = ["cart_drive_x", "cart_drive_y", "cart_turn"] as const;
export const ARM_PREFIX = "arm0/";

const format = (values: readonly number[]) => values.map((value) => Number(value.toFixed(5))).join(" ");

export function tomatoWeldName(index: number): string {
  return `stem_${index}`;
}

/** Grasp-assist constraint holding a tomato in the hand (off until the gripper closes on it). */
export function tomatoGripName(index: number): string {
  return `grip_${index}`;
}

export function buildSceneXml(layout: FarmLayout): string {
  const { cart } = layout;
  const deckTopZ = cart.deckHeightM + cart.deckThicknessM / 2;
  const basket = cart.basket;
  const basketFloorZ = deckTopZ + 0.006;
  const halfInnerLength = basket.innerLengthM / 2;
  const halfInnerWidth = basket.innerWidthM / 2;
  const halfWallHeight = basket.heightM / 2;
  const wallCentreZ = basketFloorZ + halfWallHeight;
  const bx = basket.centre[0];

  const wheels = [
    [cart.lengthM / 2 - 0.15, cart.widthM / 2],
    [cart.lengthM / 2 - 0.15, -cart.widthM / 2],
    [-cart.lengthM / 2 + 0.15, cart.widthM / 2],
    [-cart.lengthM / 2 + 0.15, -cart.widthM / 2],
  ]
    .map(
      ([x, y], index) =>
        `      <geom name="wheel_${index}" type="cylinder" size="${cart.wheelRadiusM} 0.035" pos="${format([x!, y!, cart.wheelRadiusM])}" euler="1.5708 0 0" rgba="0.12 0.12 0.12 1" contype="0" conaffinity="0" mass="2"/>`,
    )
    .join("\n");

  const armMounts = cart.armMounts
    .map((mount) => {
      const postHeight = mount.position[2] - deckTopZ;
      return `      <geom name="${mount.name}_post" type="box" size="0.07 0.07 ${format([postHeight / 2])}" pos="${format([mount.position[0], mount.position[1], deckTopZ + postHeight / 2])}" rgba="0.2 0.22 0.25 1" mass="8"/>
      <body name="${mount.name}_mount" pos="${format(mount.position)}">
        <attach model="panda" body="link0" prefix="${mount.name}/"/>
      </body>`;
    })
    .join("\n");

  const tomatoBodies = layout.tomatoes
    .map(
      (tomato) =>
        `    <body name="${tomato.name}" pos="${format(tomato.position)}">
      <freejoint name="${tomato.name}_free"/>
      <geom name="${tomato.name}_geom" type="sphere" size="${format([tomato.radiusM])}" rgba="${format(tomato.rgba)}" mass="0.02" friction="1.5 0.02 0.002" condim="4" solref="0.004 1"/>
    </body>`,
    )
    .join("\n");

  const welds = layout.tomatoes
    .map(
      (tomato) =>
        `    <connect name="${tomatoWeldName(tomato.index)}" body1="${tomato.name}" anchor="0 0 0" solref="0.02 1"/>
    <connect name="${tomatoGripName(tomato.index)}" body1="${tomato.name}" body2="${ARM_PREFIX}hand" anchor="0 0 0" solref="0.01 1" active="false"/>`,
    )
    .join("\n");

  return `<mujoco model="wefarm_tomato_path">
  <compiler angle="radian" meshdir="." autolimits="true"/>
  <option timestep="0.002" integrator="implicitfast" cone="elliptic" impratio="10"/>
  <visual><global offwidth="1280" offheight="720"/></visual>
  <asset>
    <model name="panda" file="${PANDA_MODEL_FILE}"/>
  </asset>
  <worldbody>
    <geom name="ground" type="plane" size="0 0 0.05" rgba="0.45 0.36 0.26 1"/>
    <body name="cart" pos="0 0 0">
      <joint name="cart_x" type="slide" axis="1 0 0" damping="400" armature="5"/>
      <joint name="cart_y" type="slide" axis="0 1 0" damping="400" armature="5"/>
      <joint name="cart_yaw" type="hinge" axis="0 0 1" damping="200" armature="5"/>
      <geom name="cart_deck" type="box" size="${format([cart.lengthM / 2, cart.widthM / 2, cart.deckThicknessM / 2])}" pos="${format([0, 0, cart.deckHeightM])}" rgba="0.16 0.42 0.26 1" mass="60"/>
      <geom name="cart_frame" type="box" size="${format([cart.lengthM / 2 - 0.05, cart.widthM / 2 - 0.04, 0.1])}" pos="${format([0, 0, cart.deckHeightM - 0.13])}" rgba="0.2 0.2 0.22 1" mass="20" contype="0" conaffinity="0"/>
${wheels}
      <geom name="basket_floor" type="box" size="${format([halfInnerLength + basket.wallM, halfInnerWidth + basket.wallM, 0.006])}" pos="${format([bx, 0, deckTopZ + 0.003])}" rgba="0.55 0.36 0.18 1" mass="1"/>
      <geom name="basket_wall_front" type="box" size="${format([basket.wallM / 2, halfInnerWidth + basket.wallM, halfWallHeight])}" pos="${format([bx + halfInnerLength + basket.wallM / 2, 0, wallCentreZ])}" rgba="0.62 0.42 0.2 1" mass="0.3"/>
      <geom name="basket_wall_back" type="box" size="${format([basket.wallM / 2, halfInnerWidth + basket.wallM, halfWallHeight])}" pos="${format([bx - halfInnerLength - basket.wallM / 2, 0, wallCentreZ])}" rgba="0.62 0.42 0.2 1" mass="0.3"/>
      <geom name="basket_wall_left" type="box" size="${format([halfInnerLength, basket.wallM / 2, halfWallHeight])}" pos="${format([bx, halfInnerWidth + basket.wallM / 2, wallCentreZ])}" rgba="0.62 0.42 0.2 1" mass="0.3"/>
      <geom name="basket_wall_right" type="box" size="${format([halfInnerLength, basket.wallM / 2, halfWallHeight])}" pos="${format([bx, -halfInnerWidth - basket.wallM / 2, wallCentreZ])}" rgba="0.62 0.42 0.2 1" mass="0.3"/>
${armMounts}
    </body>
${tomatoBodies}
  </worldbody>
  <equality>
${welds}
  </equality>
  <actuator>
    <position name="cart_drive_x" joint="cart_x" kp="4000" kv="1500" ctrlrange="-5 50" forcerange="-3000 3000"/>
    <position name="cart_drive_y" joint="cart_y" kp="4000" kv="1500" ctrlrange="-2 2" forcerange="-3000 3000"/>
    <position name="cart_turn" joint="cart_yaw" kp="3000" kv="900" ctrlrange="-3.2 3.2" forcerange="-2000 2000"/>
  </actuator>
</mujoco>
`;
}
