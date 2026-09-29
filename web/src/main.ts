// WeFarm browser simulator: a photoreal tomato path (World Labs Marble splat) with a generated work
// cell (plants and tomatoes) and a harvest cart carrying a Franka Panda, all simulated live by
// MuJoCo. The operator drives the gripper with the keyboard and mouse; sessions are recorded and
// can be replayed for QA.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SparkRenderer } from "@sparkjsdev/spark";
import { DEFAULT_FARM_PARAMETERS, generateFarmLayout, type FarmLayoutParameters } from "./farm/farmLayout.ts";
import { CONTROL_RATE_HZ, TomatoHarvestSimulation } from "./sim/simulation.ts";
import { buildMujocoMeshes } from "./render/mujocoMeshes.ts";
import { buildTomatoPlants } from "./render/tomatoPlants.ts";
import { applyScaleChoice, downloadWithProgress, loadSplatWorld, loadWorldPackage, splatFileFor, worldPackageSources, type LoadedSplatWorld, type WorldPackage, type WorldPackageSource } from "./render/splatWorld.ts";
import { LoadingScreen, formatMegabytes } from "./render/loadingScreen.ts";
import { dressTomatoes } from "./render/tomatoFruit.ts";
import { dressCart } from "./render/cartAppearance.ts";
import { RECORDING_SCHEMA_VERSION, SessionRecorder, parseRecording, sha256Hex, type Recording } from "./recording/sessionRecording.ts";
import { KeyboardTeleop } from "./teleop/keyboardTeleop.ts";
import "./style.css";

const SIMULATOR_VERSION = "wefarm-web 0.2.0";
/**
 * Splat level swapped in after the first, light one has loaded. "full" (about 28 MB) ran at 78 fps
 * next to the physics on an Apple-silicon laptop in Chrome; `?splat=500k` suits weaker machines.
 */
const SHARP_SPLAT_LEVEL = "full";
const TASK_GOAL = "Drive along the tomato path and pick the ripe (red) tomatoes into the basket. Leave green ones on the plant.";
const params = new URLSearchParams(location.search);
const baseUrl = import.meta.env.BASE_URL;
const marketplaceGameId = params.get("game_id")?.trim() ?? "";
const marketplacePlayerId = params.get("player_id")?.trim() ?? "";
const submissionUrl = params.get("submission_url")?.trim() ?? "";
const aiApiBase = submissionUrl ? new URL(submissionUrl, location.href).origin : "http://127.0.0.1:8765";

type AiPhase = "checking" | "unavailable" | "ready" | "observing" | "thinking" | "acting" | "paused" | "stopped" | "completed" | "error";
type AiActionName = "move_arm" | "orient_wrist" | "move_base" | "gripper" | "wait" | "view" | "stop";
interface AiDecision {
  action: AiActionName;
  direction: string;
  duration_s: number;
  reason: string;
  model: string;
  latency_ms: number;
  frame_id: number;
}
const AI_DIRECTIONS: Record<AiActionName, readonly string[]> = {
  move_arm: ["forward", "backward", "left", "right", "up", "down"],
  orient_wrist: ["yaw_left", "yaw_right", "pitch_up", "pitch_down"],
  move_base: ["forward", "backward", "left", "right", "turn_left", "turn_right"],
  gripper: ["open", "close"],
  wait: [""],
  view: ["overview", "left", "right", "top"],
  stop: [""],
};
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

const statusElement = document.querySelector<HTMLDivElement>("#status")!;
const setStatus = (text: string) => (statusElement.textContent = text);

// Show any uncaught error on the page itself, so problems are visible without opening dev tools.
const showError = (message: string) => {
  const box = document.querySelector<HTMLPreElement>("#error-box")!;
  box.hidden = false;
  box.textContent += `${new Date().toLocaleTimeString()}  ${message}\n`;
};
window.addEventListener("error", (event) => showError(event.error?.stack ?? event.message));
window.addEventListener("unhandledrejection", (event) => showError(String((event.reason as Error)?.stack ?? event.reason)));

async function main(): Promise<void> {
  const loading = new LoadingScreen(document.querySelector<HTMLDivElement>("#loading-screen")!, [
    { id: "world", label: "Find the greenhouse scene", weight: 3 },
    { id: "download", label: "Download the photoreal greenhouse", weight: 55 },
    { id: "physics", label: "Load physics and the Franka Panda", weight: 30 },
    { id: "build", label: "Build the 3D scene", weight: 12 },
  ]);

  // ---------- World package: local copy first, then the public bucket ----------
  loading.start("world", "looking for a local copy…");
  let world: WorldPackage | null = null;
  let worldSource: WorldPackageSource | null = null;
  if (params.get("world") !== "none") {
    for (const source of worldPackageSources(params.get("world"))) {
      if (source.origin === "remote") loading.progress("world", null, "no local copy; trying the cloud…");
      try {
        world = applyScaleChoice(await loadWorldPackage(source.baseUrl), params.get("scale"));
        worldSource = source;
        break;
      } catch {
        /* try the next location */
      }
    }
  }
  const worldBaseUrl = worldSource?.baseUrl ?? "";
  // Show a light splat quickly, then swap in a sharper one in the background (unless ?splat= pins one).
  const splatLevel = params.get("splat") ?? (world?.files.splats["100k"] ? "100k" : "500k");
  const sharperSplatLevel = params.get("splat") ? null : SHARP_SPLAT_LEVEL;
  // Start the splat download now so it overlaps with loading physics.
  let splatDownload: Promise<Uint8Array | null> = Promise.resolve(null);
  if (world && worldSource) {
    loading.done("world", `${world.world_id} v${world.version} · ${worldSource.origin === "local" ? "local copy" : "from the cloud"}`);
    const splatUrl = `${worldBaseUrl}/${splatFileFor(world, splatLevel)}`;
    const downloadStarted = performance.now();
    const origin = worldSource.origin === "local" ? "local" : "cloud";
    loading.start("download", `starting (${origin})…`, 0);
    splatDownload = downloadWithProgress(splatUrl, (loadedBytes, totalBytes) => {
      const seconds = (performance.now() - downloadStarted) / 1000;
      const speed = seconds > 0.3 ? ` · ${formatMegabytes(loadedBytes / seconds)}/s` : "";
      loading.progress("download", totalBytes > 0 ? loadedBytes / totalBytes : null, `${formatMegabytes(loadedBytes)}${totalBytes > 0 ? ` / ${formatMegabytes(totalBytes)}` : ""} · ${origin}${speed}`);
    }).then(
      (bytes) => {
        loading.done("download", `${formatMegabytes(bytes.byteLength)} · ${origin} · ${((performance.now() - downloadStarted) / 1000).toFixed(1)} s`);
        return bytes;
      },
      (error: unknown) => {
        console.warn("Splat download failed; showing the work cell only", error);
        loading.fail("download", "download failed: plain ground instead");
        return null;
      },
    );
  } else {
    loading.skip("world", params.get("world") === "none" ? "turned off (?world=none)" : "not found: plain ground instead");
    loading.skip("download", "no photoreal scene");
  }

  // ---------- Task layout and simulation ----------
  const layoutParameters: FarmLayoutParameters = {
    ...DEFAULT_FARM_PARAMETERS,
    seed: Number(params.get("seed") ?? DEFAULT_FARM_PARAMETERS.seed),
    pathWidthM: Math.min(1.5, Math.max(1.0, world?.path?.width_m ?? DEFAULT_FARM_PARAMETERS.pathWidthM)),
  };
  const layout = generateFarmLayout(layoutParameters);
  setStatus("Loading physics and the Franka Panda model…");
  loading.start("physics", "MuJoCo (WebAssembly) and robot model files…");
  const pandaBase = `${baseUrl}models/franka_emika_panda`;
  let pandaFilesTotal = 0;
  let pandaFilesLoaded = 0;
  const simulation = await TomatoHarvestSimulation.create(layout, {
    readPandaFile: async (relativePath) => {
      const bytes = new Uint8Array(await (await fetch(`${pandaBase}/${relativePath}`)).arrayBuffer());
      pandaFilesLoaded += 1;
      if (pandaFilesTotal > 0) loading.progress("physics", (0.9 * pandaFilesLoaded) / pandaFilesTotal, `robot files ${pandaFilesLoaded} / ${pandaFilesTotal}`);
      return bytes;
    },
    pandaAssetList: async () => {
      const names: string[] = await (await fetch(`${pandaBase}/asset-list.json`)).json();
      pandaFilesTotal = names.length + 1;
      return names;
    },
  });
  loading.done("physics", `${layout.tomatoes.length} tomatoes · ${simulation.model.nq} joint positions`);
  const sceneHash = await sha256Hex(simulation.sceneXml);

  // ---------- Renderer, scene, cameras ----------
  const container = document.querySelector<HTMLDivElement>("#viewport")!;
  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xcfd8dc);
  const spark = new SparkRenderer({ renderer });
  scene.add(spark);
  // World content is z-up (MuJoCo, ROS, USD); three.js is y-up. One rotated root converts.
  const worldRoot = new THREE.Group();
  worldRoot.rotation.x = -Math.PI / 2;
  scene.add(worldRoot);

  const hemisphereLight = new THREE.HemisphereLight(0xfffaf0, 0x5a4a38, 1.5);
  scene.add(hemisphereLight);
  const sun = new THREE.DirectionalLight(0xfff1dc, 2.2);
  sun.position.set(2, 6, 3);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 20 });
  scene.add(sun, sun.target);

  const meshes = buildMujocoMeshes(simulation.mujoco, simulation.model);
  worldRoot.add(meshes.root);
  const plants = buildTomatoPlants(layout);
  worldRoot.add(plants.root);
  const fruit = dressTomatoes(meshes, layout);
  dressCart(meshes);

  // The photoreal scene cannot receive shadows, so an invisible ground that shows only shadows
  // grounds the cart, arm, and plants on the photographed path.
  const shadowCatcher = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), new THREE.ShadowMaterial({ opacity: 0.38, depthWrite: false }));
  shadowCatcher.position.set(20, 0, 0.003);
  shadowCatcher.receiveShadow = true;
  shadowCatcher.renderOrder = 10;
  shadowCatcher.visible = false;
  worldRoot.add(shadowCatcher);

  const targetMarker = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.004, 8, 24), new THREE.MeshBasicMaterial({ color: 0x39ff88 }));
  worldRoot.add(targetMarker);

  const orbitCamera = new THREE.PerspectiveCamera(60, 1, 0.02, 200);
  const wristCamera = new THREE.PerspectiveCamera(75, 1, 0.01, 100);
  const headCamera = new THREE.PerspectiveCamera(70, 1, 0.02, 200);
  const cameraModes = ["orbit", "head", "wrist"] as const;
  /** Small picture-in-picture view from the wrist camera (key M), shown unless the main view is the wrist. */
  let wristInsetVisible = true;
  const wristInsetLabel = document.querySelector<HTMLDivElement>("#wrist-inset-label")!;
  let cameraMode: (typeof cameraModes)[number] = "orbit";
  const controls = new OrbitControls(orbitCamera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.495;
  // Start behind and above the cart, looking down the path (+x world = -z three).
  const toScene = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);
  orbitCamera.position.copy(toScene(-1.4, -0.9, 1.9));
  controls.target.copy(toScene(1.2, 0, 0.9));

  let splatWorld: LoadedSplatWorld | null = null;
  loading.start("build", "waiting for the download…");
  const splatFileBytes = await splatDownload;
  if (world && splatFileBytes) {
    setStatus("Building the photoreal greenhouse…");
    loading.progress("build", null, "decoding the splat…");
    try {
      splatWorld = await loadSplatWorld({ baseUrl: worldBaseUrl, world, splatLevel, parent: worldRoot, layout, splatFileBytes });
      meshes.setGroundVisible(false);
      shadowCatcher.visible = true;
      // Default: the photo's own plants, whole; the pickable trusses hang in front of them.
      splatWorld.plantEraser.visible = false;
      plants.setGeneratedFoliageVisible(false);
      scene.background = new THREE.Color(0xe8ecef);
      // Light the robot, cart, and fruit with the world's own panorama, so they take on the
      // greenhouse's colours (sky-lit roof above, green rows around, sandy ground below).
      if (world.files.lighting_pano) {
        new THREE.TextureLoader().loadAsync(`${worldBaseUrl}/${world.files.lighting_pano}`).then(
          (panorama) => {
            panorama.mapping = THREE.EquirectangularReflectionMapping;
            panorama.colorSpace = THREE.SRGBColorSpace;
            scene.environment = panorama;
            scene.environmentIntensity = 0.9;
            // The panorama's centre looks down the path (+x world, which is -z in three.js).
            scene.environmentRotation.set(0, Math.PI / 2, 0);
            hemisphereLight.intensity = 0.5;
          },
          (error: unknown) => console.warn("Lighting panorama failed to load; keeping the default lights", error),
        );
      }
      loading.done("build", "photoreal scene placed");
    } catch (error) {
      console.warn("World package failed to load; showing the work cell only", error);
      loading.fail("build", `could not build the scene (${(error as Error).message}): plain ground instead`);
    }
  } else {
    loading.done("build", "work cell on plain ground");
  }

  const resize = () => {
    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    renderer.setSize(width, height, false);
    for (const camera of [orbitCamera, wristCamera, headCamera]) {
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
  };
  new ResizeObserver(resize).observe(container);
  resize();

  // ---------- HUD ----------
  const hud = {
    harvested: document.querySelector<HTMLSpanElement>("#hud-harvested")!,
    unripe: document.querySelector<HTMLSpanElement>("#hud-unripe")!,
    dropped: document.querySelector<HTMLSpanElement>("#hud-dropped")!,
    time: document.querySelector<HTMLSpanElement>("#hud-time")!,
    camera: document.querySelector<HTMLSpanElement>("#hud-camera")!,
    gripper: document.querySelector<HTMLSpanElement>("#hud-gripper")!,
    record: document.querySelector<HTMLButtonElement>("#button-record")!,
    reset: document.querySelector<HTMLButtonElement>("#button-reset")!,
    load: document.querySelector<HTMLInputElement>("#input-recording")!,
    scenery: document.querySelector<HTMLButtonElement>("#button-scenery")!,
    cameraButton: document.querySelector<HTMLButtonElement>("#button-camera")!,
    playPanel: document.querySelector<HTMLDivElement>("#play-panel")!,
    replayPanel: document.querySelector<HTMLDivElement>("#replay-panel")!,
    replaySlider: document.querySelector<HTMLInputElement>("#replay-slider")!,
    replayPlay: document.querySelector<HTMLButtonElement>("#replay-play")!,
    replaySpeed: document.querySelector<HTMLSelectElement>("#replay-speed")!,
    replayInfo: document.querySelector<HTMLDivElement>("#replay-info")!,
    replayEvents: document.querySelector<HTMLOListElement>("#replay-events")!,
    replayExit: document.querySelector<HTMLButtonElement>("#replay-exit")!,
    qaPass: document.querySelector<HTMLButtonElement>("#qa-pass")!,
    qaFail: document.querySelector<HTMLButtonElement>("#qa-fail")!,
    qaNotes: document.querySelector<HTMLTextAreaElement>("#qa-notes")!,
    worldCredit: document.querySelector<HTMLDivElement>("#world-credit")!,
  };
  const aiUi = {
    state: document.querySelector<HTMLDivElement>("#ai-state")!,
    message: document.querySelector<HTMLParagraphElement>("#ai-message")!,
    observation: document.querySelector<HTMLImageElement>("#ai-observation")!,
    model: document.querySelector<HTMLElement>("#ai-model")!,
    action: document.querySelector<HTMLElement>("#ai-action")!,
    reason: document.querySelector<HTMLElement>("#ai-reason")!,
    decisions: document.querySelector<HTMLElement>("#ai-decisions")!,
    latency: document.querySelector<HTMLElement>("#ai-latency")!,
    start: document.querySelector<HTMLButtonElement>("#ai-start")!,
    pause: document.querySelector<HTMLButtonElement>("#ai-pause")!,
    resume: document.querySelector<HTMLButtonElement>("#ai-resume")!,
    stop: document.querySelector<HTMLButtonElement>("#ai-stop")!,
    take: document.querySelector<HTMLButtonElement>("#ai-take")!,
  };
  const marketplaceContext = document.querySelector<HTMLDivElement>("#marketplace-context")!;
  aiUi.stop.textContent = submissionUrl && marketplaceGameId && marketplacePlayerId ? "Stop & submit" : "Stop & save";
  if (marketplaceGameId && marketplacePlayerId) {
    marketplaceContext.hidden = false;
    marketplaceContext.textContent = `Marketplace game: ${marketplaceGameId} · Player: ${marketplacePlayerId}`;
  }
  const ripeCount = layout.tomatoes.filter((tomato) => tomato.ripeness === "ripe").length;
  if (world?.input) hud.worldCredit.innerHTML = `Scene: World Labs Marble (${world.model}) from <a href="${world.input.page_url}" target="_blank" rel="noopener">a photo</a> by ${world.input.author}, ${world.input.license}. Robot: MuJoCo Menagerie Franka Panda (Apache-2.0).`;
  else hud.worldCredit.textContent = "No world package loaded: work cell only. Robot: MuJoCo Menagerie Franka Panda (Apache-2.0).";

  const tally = (events: TomatoHarvestSimulation["events"]) => {
    let harvestedRipe = 0;
    let harvestedUnripe = 0;
    let dropped = 0;
    for (const event of events) {
      if (event.kind === "harvested") event.ripeness === "ripe" ? (harvestedRipe += 1) : (harvestedUnripe += 1);
      if (event.kind === "dropped") dropped += 1;
    }
    return { harvestedRipe, harvestedUnripe, dropped };
  };
  const updateHud = (events: TomatoHarvestSimulation["events"], time: number, gripperOpen: boolean) => {
    const totals = tally(events);
    hud.harvested.textContent = `${totals.harvestedRipe} / ${ripeCount}`;
    hud.unripe.textContent = String(totals.harvestedUnripe);
    hud.dropped.textContent = String(totals.dropped);
    hud.time.textContent = `${time.toFixed(1)} s`;
    hud.gripper.textContent = gripperOpen ? "open" : "closed";
    hud.camera.textContent = cameraMode;
  };

  // ---------- Modes: play (live physics) and replay (QA) ----------
  let mode: "play" | "replay" = "play";
  let aiMode: "human" | "running" | "paused" | "finishing" = "human";
  let aiPhase: AiPhase = "checking";
  let aiAvailable = false;
  let aiMessage = "Checking the Crusoe VLM connection…";
  let aiModel = "";
  let aiDecisionCount = 0;
  let aiFrameId = 0;
  let aiGeneration = 0;
  let aiAbort: AbortController | null = null;
  let aiAction: AiDecision | null = null;
  let aiActionRemaining = 0;
  let aiNextObservationAt = 0;
  let aiSessionStartedAt = 0;
  let aiLastLatencyMs: number | null = null;
  let recorder: SessionRecorder | null = null;
  let savingRecording = false;
  let replay: { recording: Recording; frame: number; playing: boolean; accumulator: number } | null = null;
  const teleop = new KeyboardTeleop(simulation, layout);
  // Debug handle for the browser console (inspect the simulation, script a pick).
  Object.assign(window, { wefarm: { simulation, layout, teleop, orbitCamera, orbitControls: controls, splatWorld } });

  const newRecordingHeader = (device = "keyboard+mouse", model = "") => ({
    type: "header" as const,
    schema_version: RECORDING_SCHEMA_VERSION,
    session_id: crypto.randomUUID(),
    started_at: new Date().toISOString(),
    simulator: SIMULATOR_VERSION,
    world: world ? { world_id: world.world_id, version: world.version, model: world.model } : null,
    task: { task_id: "tomato-path-harvest", goal: TASK_GOAL, layout_parameters: layout.parameters, tomato_count: layout.tomatoes.length, ripe_count: ripeCount },
    scene_xml_sha256: sceneHash,
    control_rate_hz: CONTROL_RATE_HZ,
    physics_timestep_s: simulation.model.opt.timestep,
    nq: simulation.model.nq,
    operator: { device, user_agent: navigator.userAgent, ...(model ? { model } : {}) },
  });

  const updateAiUi = () => {
    const labels: Record<AiPhase, string> = {
      checking: "Checking availability…", unavailable: "Unavailable", ready: "Ready", observing: "Observing",
      thinking: "Thinking", acting: "Acting", paused: "Paused", stopped: "Stopped", completed: "Completed", error: "Unavailable",
    };
    aiUi.state.textContent = labels[aiPhase];
    aiUi.message.textContent = aiMessage;
    aiUi.model.textContent = aiModel || "—";
    aiUi.action.textContent = aiAction ? `${aiAction.action}${aiAction.direction ? ` · ${aiAction.direction}` : ""}` : "—";
    aiUi.reason.textContent = aiAction?.reason || "—";
    aiUi.decisions.textContent = String(aiDecisionCount);
    aiUi.latency.textContent = aiLastLatencyMs === null ? "—" : `${aiLastLatencyMs} ms`;
    const active = aiMode === "running" || aiMode === "paused";
    aiUi.start.disabled = !aiAvailable || aiMode !== "human" || mode !== "play" || Boolean(recorder) || savingRecording;
    aiUi.pause.disabled = aiMode !== "running";
    aiUi.resume.disabled = aiMode !== "paused";
    aiUi.stop.disabled = !active;
    aiUi.take.disabled = !active;
    hud.record.disabled = active || aiMode === "finishing" || savingRecording;
    hud.reset.disabled = active || aiMode === "finishing";
    hud.load.disabled = active || aiMode === "finishing";
  };

  const setAiPhase = (phase: AiPhase, message: string) => {
    aiPhase = phase;
    aiMessage = message;
    updateAiUi();
  };

  const cancelAiDecision = () => {
    aiGeneration += 1;
    aiAbort?.abort();
    aiAbort = null;
    if (aiAction) recorder?.addAiActionResult({ type: "ai_action_result", frame_id: aiAction.frame_id,
      step: simulation.controlStep, completed_at: new Date().toISOString(), outcome: "cancelled" });
    aiAction = null;
    aiActionRemaining = 0;
  };

  const refreshAiConfig = async () => {
    try {
      const response = await fetch(`${aiApiBase}/api/ai-player/config`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const config = await response.json() as { available?: boolean; model?: string | null; reason?: string | null };
      aiAvailable = config.available === true;
      aiModel = config.model ?? "";
      setAiPhase(aiAvailable ? "ready" : "unavailable", aiAvailable
        ? "Crusoe VLM is configured. Start AI Mode to test live inference."
        : config.reason || "Crusoe VLM is not configured. Human mode remains available.");
    } catch {
      aiAvailable = false;
      setAiPhase("unavailable", "AI Player API is unreachable. Human mode remains available.");
    }
  };

  const downloadBlob = (blob: Blob, filename: string) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
  };

  const stopRecording = async () => {
    if (!recorder || savingRecording) return;
    const finished = recorder;
    recorder = null;
    savingRecording = true;
    hud.record.disabled = true;
    hud.record.textContent = "Saving…";
    hud.record.classList.remove("recording");
    try {
      const totals = tally(simulation.events);
      const header = finished.lines[0] as ReturnType<typeof newRecordingHeader>;
      const aiRecording = header.operator.device === "crusoe-vlm";
      const blob = await finished.toGzipBlob({ type: "footer", steps: finished.steps, duration_s: Number((finished.steps / CONTROL_RATE_HZ).toFixed(2)), harvested_ripe: totals.harvestedRipe, harvested_unripe: totals.harvestedUnripe, dropped: totals.dropped });
      const filename = `wefarm-session-${header.started_at.replace(/[:.]/g, "-")}.jsonl.gz`;
      if (submissionUrl && marketplaceGameId && marketplacePlayerId) {
        setStatus(`Uploading ${finished.steps} recorded steps to the marketplace…`);
        try {
          const endpoint = new URL(submissionUrl, location.href);
          if (endpoint.protocol !== "http:" && endpoint.protocol !== "https:") throw new Error("Unsupported upload URL");
          const response = await fetch(endpoint, {
            method: "POST",
            headers: {
              "Content-Type": "application/gzip",
              "X-WeFarm-Game-Id": marketplaceGameId,
              "X-WeFarm-Player-Id": marketplacePlayerId,
              "X-WeFarm-Session-Id": header.session_id,
            },
            body: blob,
          });
          if (!response.ok) throw new Error(`Server returned HTTP ${response.status}`);
          const result = await response.json() as { episode_id?: string; qa?: string; reasons?: string[] };
          const qaText = result.qa ? ` QA: ${result.qa}${result.reasons?.length ? ` (${result.reasons.join("; ")})` : ""}.` : " QA result unavailable.";
          setStatus(`Recording saved to the marketplace: ${finished.steps} steps (${(blob.size / 1024).toFixed(0)} KB).${qaText}`);
          if (aiRecording) setAiPhase("completed", `Episode ${result.episode_id ?? "saved"}.${qaText}`);
        } catch (error) {
          downloadBlob(blob, filename);
          setStatus(`Marketplace upload failed (${(error as Error).message}). A local recording was downloaded for recovery.`);
          if (aiRecording) setAiPhase("error", `Marketplace upload failed. A local recording was downloaded for recovery.`);
        }
      } else {
        downloadBlob(blob, filename);
        setStatus(`Saved recording: ${finished.steps} steps (${(blob.size / 1024).toFixed(0)} KB). Load it with "Replay" to review.`);
        if (aiRecording) setAiPhase("completed", "AI recording saved locally. No marketplace QA was run.");
      }
    } catch (error) {
      setStatus(`Could not save recording: ${(error as Error).message}`);
      setAiPhase("error", `Could not save recording: ${(error as Error).message}`);
    } finally {
      savingRecording = false;
      hud.record.textContent = "● Record";
      updateAiUi();
    }
  };

  hud.record.addEventListener("click", async () => {
    if (mode !== "play" || savingRecording || aiMode !== "human") return;
    if (recorder) return void (await stopRecording());
    simulation.reset();
    recorder = new SessionRecorder(newRecordingHeader());
    hud.record.textContent = submissionUrl ? "■ Stop & submit" : "■ Stop & save";
    hud.record.classList.add("recording");
    setStatus("Recording from a fresh start. Pick the ripe tomatoes!");
    updateAiUi();
  });
  hud.reset.addEventListener("click", () => {
    if (mode !== "play" || aiMode !== "human") return;
    recorder = null;
    hud.record.textContent = "● Record";
    hud.record.classList.remove("recording");
    simulation.reset();
    updateAiUi();
  });
  const cycleCamera = () => {
    cameraMode = cameraModes[(cameraModes.indexOf(cameraMode) + 1) % cameraModes.length]!;
    controls.enabled = cameraMode === "orbit";
  };
  hud.cameraButton.addEventListener("click", cycleCamera);
  const toggleScenery = () => {
    if (!splatWorld) return;
    const useGeneratedPlants = !splatWorld.plantEraser.visible;
    splatWorld.plantEraser.visible = useGeneratedPlants;
    plants.setGeneratedFoliageVisible(useGeneratedPlants);
    hud.scenery.textContent = useGeneratedPlants ? "Photo plants (P)" : "Generated plants (P)";
  };
  hud.scenery.addEventListener("click", toggleScenery);
  hud.scenery.disabled = !splatWorld;
  hud.scenery.textContent = "Generated plants (P)";
  window.addEventListener("keydown", (event) => {
    if ((event.target as HTMLElement).tagName === "TEXTAREA") return;
    if (event.code === "KeyV") cycleCamera();
    if (event.code === "KeyP") toggleScenery();
    if (event.code === "KeyM") wristInsetVisible = !wristInsetVisible;
  });

  // Replay (QA) mode.
  const showReplayFrame = (frame: number) => {
    if (!replay) return;
    const steps = replay.recording.steps;
    replay.frame = Math.max(0, Math.min(steps.length - 1, frame));
    const step = steps[replay.frame]!;
    simulation.applySnapshot(step.qpos, step.attached);
    step.attached.forEach((attached, index) => plants.setFruitStemVisible(index, attached === 1));
    hud.replaySlider.value = String(replay.frame);
    const eventsSoFar = replay.recording.events.filter((event) => event.time <= step.t + 1e-6);
    updateHud(eventsSoFar, step.t, step.command.gripperOpen);
    const target = TomatoHarvestSimulation.cartToWorld(step.qpos.slice(0, 3), step.command.handTargetInCart);
    targetMarker.position.set(target[0], target[1], target[2]);
  };
  hud.load.addEventListener("change", async () => {
    if (aiMode !== "human") { hud.load.value = ""; return; }
    const file = hud.load.files?.[0];
    if (!file) return;
    try {
      const recording = await parseRecording(file);
      if (recording.header.scene_xml_sha256 !== sceneHash) {
        setStatus("This recording was made with a different scene (seed or layout). Reload with ?seed=" + recording.header.task.layout_parameters.seed);
        return;
      }
      recorder = null;
      mode = "replay";
      updateAiUi();
      replay = { recording, frame: 0, playing: true, accumulator: 0 };
      hud.playPanel.hidden = true;
      hud.replayPanel.hidden = false;
      hud.replaySlider.max = String(recording.steps.length - 1);
      hud.replayInfo.textContent = `Session ${recording.header.session_id.slice(0, 8)} · ${recording.header.started_at} · ${recording.steps.length} steps · ${(recording.steps.length / recording.header.control_rate_hz).toFixed(1)} s`;
      hud.replayEvents.innerHTML = "";
      for (const event of recording.events) {
        const item = document.createElement("li");
        const label = event.kind === "harvested" ? `harvested tomato ${event.tomato} (${event.ripeness})` : event.kind === "detach" ? `tomato ${event.tomato} came free (${event.forceN} N)` : `tomato ${event.tomato} dropped`;
        item.innerHTML = `<button type="button">${event.time.toFixed(2)} s</button> ${label}`;
        item.querySelector("button")!.addEventListener("click", () => showReplayFrame(Math.round(event.time * recording.header.control_rate_hz) - 1));
        hud.replayEvents.appendChild(item);
      }
      showReplayFrame(0);
      setStatus("Replay: scrub the timeline, click an event to jump, then mark pass or fail.");
    } catch (error) {
      setStatus(`Could not load the recording: ${(error as Error).message}`);
    } finally {
      hud.load.value = "";
    }
  });
  hud.replaySlider.addEventListener("input", () => {
    if (replay) replay.playing = false;
    showReplayFrame(Number(hud.replaySlider.value));
  });
  hud.replayPlay.addEventListener("click", () => {
    if (!replay) return;
    if (replay.frame >= replay.recording.steps.length - 1) replay.frame = 0;
    replay.playing = !replay.playing;
  });
  hud.replayExit.addEventListener("click", () => {
    mode = "play";
    replay = null;
    hud.playPanel.hidden = false;
    hud.replayPanel.hidden = true;
    simulation.reset();
    updateAiUi();
    setStatus("Live simulation.");
  });
  const saveReview = (verdict: "pass" | "fail") => {
    if (!replay) return;
    const review = {
      type: "qa_review",
      schema_version: 0,
      session_id: replay.recording.header.session_id,
      verdict,
      notes: hud.qaNotes.value,
      reviewed_at: new Date().toISOString(),
      marked_at_step: replay.frame,
    };
    downloadBlob(new Blob([JSON.stringify(review, null, 2)], { type: "application/json" }), `wefarm-review-${review.session_id.slice(0, 8)}-${verdict}.json`);
    setStatus(`QA verdict "${verdict}" saved.`);
  };
  hud.qaPass.addEventListener("click", () => saveReview("pass"));
  hud.qaFail.addEventListener("click", () => saveReview("fail"));

  // Click a tomato: the hand glides to just above it with the gripper open.
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let pointerDown: { x: number; y: number } | null = null;
  renderer.domElement.addEventListener("pointerdown", (event) => (pointerDown = { x: event.clientX, y: event.clientY }));
  // Hover: light up the tomato under the mouse, so it is clear what a click will reach for.
  let hoverPending = false;
  renderer.domElement.addEventListener("pointermove", (event) => {
    if (hoverPending) return;
    hoverPending = true;
    requestAnimationFrame(() => {
      hoverPending = false;
      if (mode !== "play" || cameraMode === "wrist") return fruit.setHighlighted(null);
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, activeCamera());
      const tomatoMeshes = [...meshes.meshesByGeom.values()].filter((mesh) => mesh.name.startsWith("tomato_"));
      const hit = raycaster.intersectObjects(tomatoMeshes, false)[0];
      fruit.setHighlighted(hit ? Number(hit.object.name.split("_")[1]) : null);
      renderer.domElement.style.cursor = hit ? "pointer" : "";
    });
  });
  renderer.domElement.addEventListener("pointerup", (event) => {
    if (!pointerDown || mode !== "play" || aiMode !== "human" || cameraMode === "wrist") return;
    const moved = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y);
    pointerDown = null;
    if (moved > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, activeCamera());
    const tomatoMeshes = [...meshes.meshesByGeom.values()].filter((mesh) => mesh.name.startsWith("tomato_"));
    const hit = raycaster.intersectObjects(tomatoMeshes, false)[0];
    if (!hit) return;
    const index = Number(hit.object.name.split("_")[1]);
    teleop.approachTomato(index);
    setStatus(`Moving above tomato ${index} (${layout.tomatoes[index]!.ripeness}). Lower with F, close with Space, lift with R.`);
  });

  // ---------- Frame loop ----------
  const activeCamera = () => (cameraMode === "wrist" ? wristCamera : cameraMode === "head" ? headCamera : orbitCamera);
  const handBodyId = simulation.mujoco.mj_name2id(simulation.model, simulation.mujoco.mjtObj.mjOBJ_BODY.value, "arm0/hand");
  const zUpToScene = new THREE.Matrix4().makeRotationX(-Math.PI / 2);
  const placeCameras = () => {
    const data = simulation.data;
    // Wrist camera: on the hand, looking along the gripper (hand +z), slightly behind the fingers.
    const p = handBodyId * 3;
    const r = handBodyId * 9;
    const x = data.xmat;
    const handMatrix = new THREE.Matrix4().set(x[r], x[r + 1], x[r + 2], data.xpos[p], x[r + 3], x[r + 4], x[r + 5], data.xpos[p + 1], x[r + 6], x[r + 7], x[r + 8], data.xpos[p + 2], 0, 0, 0, 1);
    // Camera looks down its -z; point it along hand +z with hand +x as "up", offset along hand -x.
    const cameraInHand = new THREE.Matrix4().makeRotationX(Math.PI).premultiply(new THREE.Matrix4().makeTranslation(-0.07, 0, 0.02));
    wristCamera.matrixAutoUpdate = false;
    wristCamera.matrix.copy(zUpToScene).multiply(handMatrix).multiply(cameraInHand);
    wristCamera.matrixWorldNeedsUpdate = true;
    // Head camera: on a mast at the cart's rear right corner, high enough to look past the arm
    // at the trusses ahead and on both sides.
    const pose = simulation.cartPose();
    headCamera.position.copy(toScene(...TomatoHarvestSimulation.cartToWorld(pose, [-0.5, -0.3, 1.95])));
    headCamera.lookAt(toScene(...TomatoHarvestSimulation.cartToWorld(pose, [0.9, 0.05, 0.85])));
  };

  const finishAi = async (message: string) => {
    if (aiMode !== "running" && aiMode !== "paused") return;
    cancelAiDecision();
    aiMode = "finishing";
    setAiPhase("stopped", message);
    if (recorder) await stopRecording();
    aiMode = "human";
    teleop.setHumanEnabled(true);
    updateAiUi();
  };

  aiUi.start.addEventListener("click", () => {
    if (!aiAvailable || aiMode !== "human" || mode !== "play" || recorder || savingRecording) return;
    simulation.reset();
    teleop.setHumanEnabled(false);
    cameraMode = "head";
    controls.enabled = false;
    aiMode = "running";
    aiDecisionCount = 0;
    aiFrameId = 0;
    aiLastLatencyMs = null;
    aiUi.observation.hidden = true;
    aiSessionStartedAt = performance.now();
    aiNextObservationAt = aiSessionStartedAt + 200;
    recorder = new SessionRecorder(newRecordingHeader("crusoe-vlm", aiModel));
    hud.record.textContent = "● AI recording";
    hud.record.classList.add("recording");
    setAiPhase("observing", "Capturing a live game frame for the Crusoe VLM.");
  });
  aiUi.pause.addEventListener("click", () => {
    if (aiMode !== "running") return;
    cancelAiDecision();
    aiMode = "paused";
    setAiPhase("paused", "AI actions are paused. Resume, stop, or take control.");
  });
  aiUi.resume.addEventListener("click", () => {
    if (aiMode !== "paused") return;
    aiMode = "running";
    aiNextObservationAt = performance.now();
    setAiPhase("observing", "Capturing a fresh game frame.");
  });
  aiUi.stop.addEventListener("click", () => { void finishAi("AI Player stopped. Saving the native recording."); });
  aiUi.take.addEventListener("click", () => { void finishAi("Returning control to the human player. Saving the AI recording."); });

  const observationCanvas = document.createElement("canvas");
  observationCanvas.width = 640;
  observationCanvas.height = 360;
  const observationContext = observationCanvas.getContext("2d")!;
  const requestAiDecision = async () => {
    if (aiMode !== "running" || aiAbort || aiAction) return;
    const frameId = ++aiFrameId;
    const generation = aiGeneration;
    const observedAt = new Date().toISOString();
    const controller = new AbortController();
    aiAbort = controller;
    setAiPhase("thinking", `Crusoe VLM is evaluating live frame ${frameId}.`);
    try {
      observationContext.drawImage(renderer.domElement, 0, 0, observationCanvas.width, observationCanvas.height);
      const frameDataUrl = observationCanvas.toDataURL("image/jpeg", 0.65);
      const frame_jpeg_base64 = frameDataUrl.split(",", 2)[1];
      if (!frame_jpeg_base64) throw new Error("Could not capture the live camera frame.");
      aiUi.observation.src = frameDataUrl;
      aiUi.observation.hidden = false;
      const totals = tally(simulation.events);
      const response = await fetch(`${aiApiBase}/api/ai-player/decide`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ frame_id: frameId, frame_jpeg_base64, status: {
          step: simulation.controlStep, harvested: totals.harvestedRipe, dropped: totals.dropped,
          gripper_open: simulation.command.gripperOpen, task: TASK_GOAL,
        } }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({})) as { error?: string };
        throw new Error(failure.error || `AI Player API returned HTTP ${response.status}`);
      }
      const decision = await response.json() as AiDecision;
      if (generation !== aiGeneration || aiMode !== "running") return;
      if (!Object.prototype.hasOwnProperty.call(AI_DIRECTIONS, decision.action)
          || !AI_DIRECTIONS[decision.action]?.includes(decision.direction)
          || !Number.isFinite(decision.duration_s) || decision.duration_s <= 0 || decision.duration_s > 0.5
          || decision.frame_id !== frameId || typeof decision.reason !== "string") {
        throw new Error("Crusoe returned an unsupported action. AI control was paused.");
      }
      aiDecisionCount += 1;
      aiLastLatencyMs = decision.latency_ms;
      aiModel = decision.model;
      recorder?.addAiDecision({ type: "ai_decision", frame_id: frameId, step: simulation.controlStep,
        observed_at: observedAt, model: decision.model, action: decision.action, direction: decision.direction,
        duration_s: decision.duration_s, reason: decision.reason, latency_ms: decision.latency_ms });
      if (decision.action === "stop") {
        void finishAi("Crusoe VLM requested stop. Saving the episode for QA.");
        return;
      }
      if (decision.action === "view") {
        cameraMode = decision.direction === "overview" ? "head" : "orbit";
        controls.enabled = cameraMode === "orbit";
        if (cameraMode === "orbit") {
          const target = controls.target;
          orbitCamera.position.copy(target).add(decision.direction === "top" ? new THREE.Vector3(0, 2.4, 0.01)
            : decision.direction === "left" ? new THREE.Vector3(-1.2, 1.4, 0)
              : new THREE.Vector3(1.2, 1.4, 0));
          controls.update();
        }
      }
      aiAction = decision;
      aiActionRemaining = decision.duration_s;
      setAiPhase("acting", `Applying ${decision.action}${decision.direction ? ` ${decision.direction}` : ""} for up to ${decision.duration_s.toFixed(2)} s.`);
    } catch (error) {
      if (controller.signal.aborted || generation !== aiGeneration) return;
      aiMode = "paused";
      aiAction = null;
      aiActionRemaining = 0;
      setAiPhase("paused", `${(error as Error).message} Resume to retry, or take control.`);
    } finally {
      if (aiAbort === controller) aiAbort = null;
    }
  };

  void refreshAiConfig();
  updateAiUi();

  let lastTime = performance.now();
  let accumulator = 0;
  let lastCartPose = simulation.cartPose();
  const controlPeriod = 1 / CONTROL_RATE_HZ;
  setStatus(splatWorld ? "Ready. Click a red tomato, or move the arm with W A S D R F." : "Ready (no photoreal world found).");
  loading.finish();

  // Sharpen the scene in the background once everything else is running.
  if (splatWorld && world && sharperSplatLevel && world.files.splats[sharperSplatLevel] && sharperSplatLevel !== splatLevel) {
    const sharperFile = splatFileFor(world, sharperSplatLevel);
    const readyStatus = statusElement.textContent ?? "";
    downloadWithProgress(`${worldBaseUrl}/${sharperFile}`, (loadedBytes, totalBytes) => {
      // Only annotate the ready message; never overwrite a newer status (e.g. a click hint).
      const current = statusElement.textContent ?? "";
      if (totalBytes > 0 && loadedBytes < totalBytes && current.startsWith(readyStatus)) setStatus(`${readyStatus} (sharpening the scene… ${Math.round((100 * loadedBytes) / totalBytes)}%)`);
    })
      .then((bytes) => splatWorld!.upgradeSplat(bytes, sharperFile.split("/").pop()!))
      .then(() => {
        if (statusElement.textContent?.includes("sharpening")) setStatus(readyStatus);
      })
      .catch((error: unknown) => console.warn("Sharper splat failed to load; keeping the light one", error));
  }

  renderer.setAnimationLoop(() => {
    const now = performance.now();
    const elapsed = Math.min(0.1, (now - lastTime) / 1000);
    lastTime = now;
    if (mode === "play") {
      accumulator += elapsed;
      let ticks = 0;
      while (accumulator >= controlPeriod && ticks < 4) {
        if (aiMode === "running" && aiAction) {
          teleop.applyAiAction(aiAction.action, aiAction.direction, controlPeriod);
          aiActionRemaining -= controlPeriod;
          if (aiActionRemaining <= 0) {
            recorder?.addAiActionResult({ type: "ai_action_result", frame_id: aiAction.frame_id,
              step: simulation.controlStep, completed_at: new Date().toISOString(), outcome: "completed" });
            aiAction = null;
            aiActionRemaining = 0;
            aiNextObservationAt = now + 150;
            setAiPhase("observing", "Checking the result in a fresh camera frame.");
          }
        } else if (aiMode === "human") teleop.applyHeldKeys(controlPeriod);
        simulation.controlTick();
        if (recorder) {
          const snapshot = simulation.snapshot();
          recorder.addStep(
            { i: simulation.controlStep - 1, t: simulation.time, command: { ...simulation.command, baseTarget: [...simulation.command.baseTarget], handTargetInCart: [...simulation.command.handTargetInCart] }, ctrl: simulation.actuatorTargets(), qpos: snapshot.qpos, attached: snapshot.attached },
            simulation.events,
            simulation.controlStep % CONTROL_RATE_HZ === 0 ? () => simulation.fullState() : null,
          );
        }
        accumulator -= controlPeriod;
        ticks += 1;
      }
      if (ticks === 4) accumulator = 0;
      layout.tomatoes.forEach((tomato) => plants.setFruitStemVisible(tomato.index, simulation.tomatoStatus(tomato.index) === "attached"));
      const target = simulation.handTargetWorld();
      targetMarker.position.set(target[0], target[1], target[2]);
      updateHud(simulation.events, simulation.time, simulation.command.gripperOpen);
      if (aiMode === "running" && tally(simulation.events).harvestedRipe >= 1) {
        void finishAi("A ripe tomato was harvested. Submitting the AI recording for QA.");
      } else if (aiMode === "running" && (now - aiSessionStartedAt > 120_000 || aiDecisionCount >= 100)) {
        void finishAi("AI Player reached its safety limit. Saving the episode for QA.");
      }
    } else if (replay?.playing) {
      replay.accumulator += elapsed * Number(hud.replaySpeed.value);
      const advance = Math.floor(replay.accumulator * replay.recording.header.control_rate_hz);
      if (advance > 0) {
        replay.accumulator -= advance / replay.recording.header.control_rate_hz;
        showReplayFrame(replay.frame + advance);
        if (replay.frame >= replay.recording.steps.length - 1) replay.playing = false;
      }
    }
    hud.replayPlay.textContent = replay?.playing ? "❚❚ Pause" : "▶ Play";
    meshes.update(simulation.data);
    // The orbit camera follows the cart as it drives.
    const cartPose = simulation.cartPose();
    const cartX = cartPose[0];
    const shift = toScene(cartPose[0] - lastCartPose[0], cartPose[1] - lastCartPose[1], 0);
    orbitCamera.position.add(shift);
    controls.target.add(shift);
    lastCartPose = cartPose;
    sun.position.copy(toScene(cartX + 2, -3, 6));
    sun.target.position.copy(toScene(cartX + 0.5, 0, 0.5));
    placeCameras();
    if (controls.enabled) controls.update();
    renderer.render(scene, activeCamera());
    if ((wristInsetVisible || aiMode === "running" || aiMode === "paused") && cameraMode !== "wrist") {
      // Bottom-right inset, above the credit line; the wrist camera keeps the main view's aspect.
      const size = renderer.getSize(new THREE.Vector2());
      const insetWidth = Math.round(Math.min(360, size.x * 0.28));
      const insetHeight = Math.round(insetWidth * (size.y / size.x));
      const insetX = size.x - insetWidth - 16;
      const insetY = 34;
      renderer.setScissorTest(true);
      renderer.setViewport(insetX, insetY, insetWidth, insetHeight);
      renderer.setScissor(insetX, insetY, insetWidth, insetHeight);
      // The hand-target ring sits right in front of the wrist camera; leave it out of this view.
      targetMarker.visible = false;
      renderer.render(scene, wristCamera);
      targetMarker.visible = true;
      renderer.setScissorTest(false);
      renderer.setViewport(0, 0, size.x, size.y);
      Object.assign(wristInsetLabel.style, { display: "block", right: "16px", bottom: `${insetY + insetHeight - 22}px`, width: `${insetWidth}px` });
    } else {
      wristInsetLabel.style.display = "none";
    }
    if (aiMode === "running" && !aiAbort && !aiAction && now >= aiNextObservationAt) void requestAiDecision();
  });
}

main().catch((error) => {
  console.error(error);
  document.querySelector<HTMLDivElement>("#loading-screen")!.hidden = true;
  showError((error as Error).stack ?? String(error));
  setStatus(`Failed to start: ${(error as Error).message}`);
});
