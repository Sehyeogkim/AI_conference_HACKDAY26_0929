// Session recording format (version 0, JSON Lines, gzip-compressed):
//   line 1        {"type":"header", ...}   what was simulated: world, task layout, scene hash, rates
//   step lines    {"type":"step", ...}     per control step: operator command, actuator targets,
//                                          joint positions (qpos) and which tomato stems still hold
//   event lines   {"type":"event", ...}    detach / harvested / dropped, in time order
//   state lines   {"type":"state", ...}    full MuJoCo integration state once per second, for exact
//                                          restarts and later re-simulation checks
//   last line     {"type":"footer", ...}   totals
// Playback poses the scene from the recorded qpos (always exact); re-simulation is only a check.
import type { FarmLayoutParameters } from "../farm/farmLayout.ts";
import type { OperatorCommand, SimulationEvent } from "../sim/simulation.ts";

export const RECORDING_SCHEMA_VERSION = 0;

export interface RecordingHeader {
  type: "header";
  schema_version: number;
  session_id: string;
  started_at: string;
  simulator: string;
  world: { world_id: string; version: number; model: string } | null;
  task: { task_id: string; goal: string; layout_parameters: FarmLayoutParameters; tomato_count: number; ripe_count: number };
  scene_xml_sha256: string;
  control_rate_hz: number;
  physics_timestep_s: number;
  nq: number;
  operator: { device: string; user_agent: string };
}

export interface RecordingStep {
  type: "step";
  i: number;
  t: number;
  command: OperatorCommand;
  ctrl: number[];
  qpos: number[];
  attached: number[];
}

export type RecordingLine =
  | RecordingHeader
  | RecordingStep
  | { type: "event"; event: SimulationEvent }
  | { type: "state"; i: number; t: number; state: number[] }
  | { type: "footer"; steps: number; duration_s: number; harvested_ripe: number; harvested_unripe: number; dropped: number };

export interface Recording {
  header: RecordingHeader;
  steps: RecordingStep[];
  events: SimulationEvent[];
}

const round = (value: number, digits = 5) => Number(value.toFixed(digits));

export class SessionRecorder {
  readonly lines: RecordingLine[] = [];
  private eventCount = 0;
  private stepCount = 0;

  constructor(header: RecordingHeader) {
    this.lines.push(header);
  }

  get steps(): number {
    return this.stepCount;
  }

  addStep(step: Omit<RecordingStep, "type">, allEvents: SimulationEvent[], fullState: (() => number[]) | null): void {
    this.lines.push({
      type: "step",
      i: step.i,
      t: round(step.t, 4),
      command: {
        baseTarget: step.command.baseTarget.map((value) => round(value)) as [number, number, number],
        handTargetInCart: step.command.handTargetInCart.map((value) => round(value)) as [number, number, number],
        gripperYaw: round(step.command.gripperYaw),
        gripperPitch: round(step.command.gripperPitch),
        gripperOpen: step.command.gripperOpen,
      },
      ctrl: step.ctrl.map((value) => round(value)),
      qpos: step.qpos,
      attached: step.attached,
    });
    while (this.eventCount < allEvents.length) this.lines.push({ type: "event", event: allEvents[this.eventCount++]! });
    if (fullState) this.lines.push({ type: "state", i: step.i, t: round(step.t, 4), state: fullState() });
    this.stepCount += 1;
  }

  async toGzipBlob(footer: Extract<RecordingLine, { type: "footer" }>): Promise<Blob> {
    const text = [...this.lines, footer].map((line) => JSON.stringify(line)).join("\n") + "\n";
    const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
    return new Response(stream).blob();
  }
}

export async function parseRecording(file: Blob): Promise<Recording> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const isGzip = bytes[0] === 0x1f && bytes[1] === 0x8b;
  const text = isGzip ? await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text() : new TextDecoder().decode(bytes);
  let header: RecordingHeader | null = null;
  const steps: RecordingStep[] = [];
  const events: SimulationEvent[] = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const parsed = JSON.parse(line) as RecordingLine;
    if (parsed.type === "header") header = parsed;
    else if (parsed.type === "step") steps.push(parsed);
    else if (parsed.type === "event") events.push(parsed.event);
  }
  if (!header) throw new Error("Not a WeFarm recording: no header line");
  if (header.schema_version !== RECORDING_SCHEMA_VERSION) throw new Error(`Unsupported recording schema version ${header.schema_version}`);
  return { header, steps, events };
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
