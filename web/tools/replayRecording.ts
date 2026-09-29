// Headless MuJoCo check for a browser recording. Run:
// node --experimental-strip-types tools/replayRecording.ts /path/to/session.jsonl.gz
// The final stdout line is machine-readable JSON for the marketplace QA worker.
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { gunzipSync } from "node:zlib";
import path from "node:path";
import { DEFAULT_FARM_PARAMETERS, generateFarmLayout } from "../src/farm/farmLayout.ts";
import { TomatoHarvestSimulation, CONTROL_RATE_HZ, type SimulationEvent } from "../src/sim/simulation.ts";
import { RECORDING_SCHEMA_VERSION, type RecordingHeader, type RecordingStep } from "../src/recording/sessionRecording.ts";

type Footer = { type: "footer"; steps: number; duration_s: number; harvested_ripe: number; harvested_unripe: number; dropped: number };
type Result = { replay_checked: boolean; passed: boolean; reasons: string[]; metrics?: Record<string, number> };
const MAX_REASONS = 8;
const QPOS_TOLERANCE = 0.02;
const CTRL_TOLERANCE = 0.05;
// Velocity and solver integration values are more sensitive to the browser's
// five-decimal command rounding than poses. Pose, actuator, stem, and event
// checks below remain much tighter and are the primary replay gate.
const FULL_STATE_TOLERANCE = 0.25;
const EVENT_TIME_TOLERANCE = 0.021;
const reasons: string[] = [];
const fail = (reason: string) => { if (reasons.length < MAX_REASONS) reasons.push(reason); };
const finiteArray = (value: unknown, count: number): value is number[] =>
  Array.isArray(value) && value.length === count && value.every((item) => typeof item === "number" && Number.isFinite(item));
const close = (left: number, right: number, tolerance: number) => Math.abs(left - right) <= tolerance;

async function replay(filePath: string): Promise<Result> {
  const compressed = await readFile(filePath);
  const text = compressed[0] === 0x1f && compressed[1] === 0x8b
    ? gunzipSync(compressed, { maxOutputLength: 128 * 1024 * 1024 }).toString("utf8")
    : compressed.toString("utf8");
  const rawLines = text.trimEnd().split("\n");
  if (rawLines.length < 3 || rawLines.length > 100_000) return { replay_checked: true, passed: false, reasons: ["Recording has an invalid number of lines"] };
  let lines: Array<Record<string, unknown>>;
  try {
    lines = rawLines.map((line) => JSON.parse(line) as Record<string, unknown>);
  } catch {
    return { replay_checked: true, passed: false, reasons: ["Recording contains invalid JSON"] };
  }
  const header = lines[0] as unknown as RecordingHeader;
  const footer = lines.at(-1) as unknown as Footer;
  if (header?.type !== "header" || header.schema_version !== RECORDING_SCHEMA_VERSION || footer?.type !== "footer") {
    return { replay_checked: true, passed: false, reasons: ["Missing header/footer or unsupported schema"] };
  }
  if (header.control_rate_hz !== CONTROL_RATE_HZ || !header.task?.layout_parameters || !Number.isInteger(header.task.layout_parameters.seed)) {
    return { replay_checked: true, passed: false, reasons: ["Invalid task parameters or control rate"] };
  }
  const parameters = header.task.layout_parameters;
  const fixedKeys = ["rowStartX", "rowLengthM", "plantSpacingM", "ripeFraction", "turningFraction", "armCount"] as const;
  if (parameters.seed < 0 || parameters.seed > 0xffffffff || !Number.isFinite(parameters.pathWidthM) || parameters.pathWidthM < 1 || parameters.pathWidthM > 1.5 ||
    fixedKeys.some((key) => parameters[key] !== DEFAULT_FARM_PARAMETERS[key]) ||
    JSON.stringify(parameters.tomatoesPerTruss) !== JSON.stringify(DEFAULT_FARM_PARAMETERS.tomatoesPerTruss)) {
    return { replay_checked: true, passed: false, reasons: ["Unsupported farm layout parameters"] };
  }
  const steps = lines.filter((line) => line.type === "step") as unknown as RecordingStep[];
  const events = lines.filter((line) => line.type === "event").map((line) => line.event as SimulationEvent);
  const stateLines = lines.filter((line) => line.type === "state");
  if (!steps.length || footer.steps !== steps.length || lines.some((line) => !["header", "step", "event", "state", "footer"].includes(String(line.type)))) {
    return { replay_checked: true, passed: false, reasons: ["Invalid step count or line type"] };
  }
  if (lines.slice(1, -1).some((line) => line.type === "header" || line.type === "footer")) {
    return { replay_checked: true, passed: false, reasons: ["Header or footer appears inside the recording"] };
  }
  const layout = generateFarmLayout(header.task.layout_parameters);
  if (layout.tomatoes.length !== header.task.tomato_count || layout.tomatoes.filter((tomato) => tomato.ripeness === "ripe").length !== header.task.ripe_count) {
    return { replay_checked: true, passed: false, reasons: ["Task layout does not match recorded tomato counts"] };
  }
  const pandaFolder = path.resolve(import.meta.dirname, "../public/models/franka_emika_panda");
  const simulation = await TomatoHarvestSimulation.create(layout, {
    readPandaFile: async (relativePath) => new Uint8Array(await readFile(path.join(pandaFolder, relativePath))),
    pandaAssetList: async () => JSON.parse(await readFile(path.join(pandaFolder, "asset-list.json"), "utf8")),
  });
  const hash = createHash("sha256").update(simulation.sceneXml).digest("hex");
  if (hash !== header.scene_xml_sha256 || header.nq !== simulation.model.nq || !close(header.physics_timestep_s, simulation.model.opt.timestep, 1e-9)) {
    return { replay_checked: true, passed: false, reasons: ["Scene hash, state size, or timestep differs from this simulator"] };
  }
  let maxQposError = 0;
  let maxCtrlError = 0;
  let maxFullStateError = 0;
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index]!;
    if (step.i !== index || !close(step.t, (index + 1) / CONTROL_RATE_HZ, 0.001) ||
      !finiteArray(step.qpos, simulation.model.nq) || !finiteArray(step.ctrl, simulation.model.nu) ||
      !finiteArray(step.attached, layout.tomatoes.length) ||
      step.attached.some((value) => value !== 0 && value !== 1) ||
      !finiteArray(step.command?.baseTarget, 3) || !finiteArray(step.command?.handTargetInCart, 3) ||
      !Number.isFinite(step.command?.gripperYaw) || !Number.isFinite(step.command?.gripperPitch) ||
      typeof step.command?.gripperOpen !== "boolean") {
      fail(`Malformed step ${index}`);
      break;
    }
    simulation.command = {
      baseTarget: [...step.command.baseTarget],
      handTargetInCart: [...step.command.handTargetInCart],
      gripperYaw: step.command.gripperYaw,
      gripperPitch: step.command.gripperPitch,
      gripperOpen: step.command.gripperOpen,
    };
    simulation.handGoalInCart = null;
    simulation.controlTick();
    const actualAttached = simulation.snapshot().attached;
    const actualQpos = simulation.data.qpos as Float64Array;
    const actualCtrl = simulation.actuatorTargets();
    for (let component = 0; component < step.qpos.length; component += 1) maxQposError = Math.max(maxQposError, Math.abs(actualQpos[component]! - step.qpos[component]!));
    for (let component = 0; component < step.ctrl.length; component += 1) maxCtrlError = Math.max(maxCtrlError, Math.abs(actualCtrl[component]! - step.ctrl[component]!));
    if (maxQposError > QPOS_TOLERANCE) { fail(`State diverged at step ${index} (maximum qpos error ${maxQposError.toFixed(5)})`); break; }
    if (maxCtrlError > CTRL_TOLERANCE) { fail(`Actuator targets diverged at step ${index} (maximum error ${maxCtrlError.toFixed(5)})`); break; }
    if (actualAttached.some((value, component) => value !== step.attached[component])) {
      fail(`Tomato stem state diverged at step ${index}`);
      break;
    }
    if (index % CONTROL_RATE_HZ === CONTROL_RATE_HZ - 1) {
      const state = stateLines[Math.floor(index / CONTROL_RATE_HZ)];
      const actualState = simulation.fullState();
      if (!state || state.i !== index || !close(Number(state.t), step.t, 0.001) || !finiteArray(state.state, actualState.length)) {
        fail(`Missing or invalid full-state checkpoint at step ${index}`);
        break;
      }
      for (let component = 0; component < actualState.length; component += 1) maxFullStateError = Math.max(maxFullStateError, Math.abs(actualState[component]! - (state.state as number[])[component]!));
      if (maxFullStateError > FULL_STATE_TOLERANCE && !reasons.some((reason) => reason.startsWith("Full-state checkpoint diverged"))) {
        fail(`Full-state checkpoint diverged at step ${index} (maximum error ${maxFullStateError.toFixed(5)})`);
      }
    }
  }
  if (stateLines.length !== Math.floor(steps.length / CONTROL_RATE_HZ)) fail("Full-state checkpoint count differs from the recording duration");
  const actualEvents = simulation.events;
  if (actualEvents.length !== events.length) fail(`Event count differs (recorded ${events.length}, replayed ${actualEvents.length})`);
  for (let index = 0; index < Math.min(actualEvents.length, events.length); index += 1) {
    const actual = actualEvents[index]!;
    const recorded = events[index]!;
    if (actual.kind !== recorded?.kind || actual.tomato !== recorded.tomato || !close(actual.time, recorded.time, EVENT_TIME_TOLERANCE) ||
      (actual.kind === "harvested" && actual.ripeness !== (recorded as typeof actual).ripeness)) {
      fail(`Event ${index} differs from physics replay`);
      break;
    }
  }
  const tally = (kind: SimulationEvent["kind"], ripeness?: string) => events.filter((event) => event.kind === kind && (ripeness === undefined || (event.kind === "harvested" && event.ripeness === ripeness))).length;
  if (footer.harvested_ripe !== tally("harvested", "ripe") || footer.harvested_unripe !== tally("harvested", "green") + tally("harvested", "turning") || footer.dropped !== tally("dropped")) {
    fail("Footer totals do not match recorded events");
  }
  if (!close(footer.duration_s, steps.length / CONTROL_RATE_HZ, 0.01)) fail("Footer duration differs from step count");
  return { replay_checked: true, passed: reasons.length === 0, reasons, metrics: {
    steps: steps.length,
    recorded_events: events.length,
    replayed_events: actualEvents.length,
    max_qpos_error: Number(maxQposError.toFixed(6)),
    max_ctrl_error: Number(maxCtrlError.toFixed(6)),
    max_full_state_error: Number(maxFullStateError.toFixed(6)),
    ripe_harvested: footer.harvested_ripe,
  } };
}

const input = process.argv[2];
if (!input) {
  console.log(JSON.stringify({ replay_checked: false, passed: false, reasons: ["Usage: replayRecording.ts /path/to/session.jsonl.gz"] } satisfies Result));
  process.exitCode = 1;
} else {
  try {
    console.log(JSON.stringify(await replay(input)));
  } catch (error) {
    console.log(JSON.stringify({ replay_checked: false, passed: false, reasons: [(error as Error).message] } satisfies Result));
    process.exitCode = 1;
  }
}
