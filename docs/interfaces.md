# Interfaces between the environment and robot work

Changing anything here needs a handoff note in `handoffs/`.

## 1. Arm controller (`web/src/robot/armController.ts`) — robot zone

```ts
interface ArmController {
  // Once per 50 Hz control step: hand target in, 7 Panda joint target angles (rad) out.
  jointTargets(arm: ArmState, target: HandPoseTarget): number[];
}
// HandPoseTarget: positionWorld [x,y,z] of the point between the fingertips (m, world, z up),
//                 yawWorld (rad), pitch (0 = gripper pointing down, π/2 = pointing out)
// ArmState: live MuJoCo model/data (read only), arm qpos/dof addresses, joint ranges,
//           hand body id, gripperCentreWorld
```

Default: `DampedLeastSquaresArmController` (damped least squares on MuJoCo's Jacobian with a null-space pull to a rest posture). Replace with `TomatoHarvestSimulation.create(layout, files, { armController: new YourController() })` in `web/src/main.ts`.

## 2. Operator command (`OperatorCommand` in `web/src/sim/simulation.ts`) — shared

| Field | Meaning |
| --- | --- |
| `baseTarget` | Cart base target `[x, y, heading]` in the world (m, m, rad) |
| `handTargetInCart` | Gripper-centre target in the cart's frame (forward, left, up; m) |
| `gripperYaw`, `gripperPitch` | Wrist rotation relative to the cart heading; wrist tilt (rad) |
| `gripperOpen` | Gripper open (true) or closed |

Input devices (keyboard today; gamepad, VR, or a policy later) only write this command.

## 3. Scene names (`web/src/sim/sceneXml.ts`) — shared

- Arm: Menagerie Panda attached with prefix `arm0/` (`arm0/joint1…7`, `arm0/actuator1…8`, `arm0/hand`). Gripper `arm0/actuator8`: 0 closed, 255 open (stiffness raised 10× at load).
- Cart: joints `cart_x`, `cart_y`, `cart_yaw`; actuators `cart_drive_x`, `cart_drive_y`, `cart_turn` (position servos).
- Tomatoes: bodies `tomato_<i>`; stem constraints `stem_<i>` (switched off when the pull exceeds that tomato's `detachForceN`: 6 N ripe, 9 N turning, 14 N green; `DETACH_FORCE_N_BY_RIPENESS` in `web/src/farm/farmLayout.ts`); mass from volume at 1000 kg/m³ (`massKg`, about 20–40 g for the current 1.7–2.1 cm radii); grasp-assist constraints `grip_<i>` (tomato ↔ `arm0/hand`).
- Physics constants robot code may depend on: timestep 2 ms; control 50 Hz (`CONTROL_RATE_HZ`); stem detach force per tomato by ripeness (above; `DETACH_FORCE_N` = 8 N is only the fallback); grasp-assist radius 3.5 cm (`GRASP_ASSIST_RADIUS_M`); hand-target glide speed 0.4 m/s (`HAND_TARGET_SPEED_M_PER_S`); gripper stiffness 10× Menagerie. All in `web/src/sim/simulation.ts`.

## 4. Recording (`web/src/recording/sessionRecording.ts`) — environment zone

Schema version **0**: gzip JSON Lines with a header, one step line per control step (`command`, `ctrl`, `qpos`, `attached`), event lines (`grasp`, `detach`, `release`, `harvested`, `dropped`), and the full MuJoCo state once per second. A change to fields bumps the version.
