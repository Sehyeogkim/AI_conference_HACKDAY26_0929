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
import { applyScaleChoice, loadSplatWorld, loadWorldPackage, type LoadedSplatWorld, type WorldPackage } from "./render/splatWorld.ts";
import { RECORDING_SCHEMA_VERSION, SessionRecorder, parseRecording, sha256Hex, type Recording } from "./recording/sessionRecording.ts";
import { KeyboardTeleop } from "./teleop/keyboardTeleop.ts";
import "./style.css";

const SIMULATOR_VERSION = "wefarm-web 0.1.0";
const TASK_GOAL = "Drive along the tomato path and pick the ripe (red) tomatoes into the basket. Leave green ones on the plant.";
const params = new URLSearchParams(location.search);
const baseUrl = import.meta.env.BASE_URL;

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
  // ---------- Task layout and simulation ----------
  const worldCandidates = params.get("world") ? [params.get("world")!] : [`${baseUrl}worlds/crete-path/v2`, `${baseUrl}worlds/crete-path/v1`, "/data/worlds/crete-path/v2", "/data/worlds/crete-path/v1"];
  let world: WorldPackage | null = null;
  let worldBaseUrl = "";
  if (params.get("world") !== "none") {
    for (const candidate of worldCandidates) {
      try {
        world = applyScaleChoice(await loadWorldPackage(candidate), params.get("scale"));
        worldBaseUrl = candidate;
        break;
      } catch {
        /* try the next location */
      }
    }
  }
  const layoutParameters: FarmLayoutParameters = {
    ...DEFAULT_FARM_PARAMETERS,
    seed: Number(params.get("seed") ?? DEFAULT_FARM_PARAMETERS.seed),
    pathWidthM: Math.min(1.5, Math.max(1.0, world?.path?.width_m ?? DEFAULT_FARM_PARAMETERS.pathWidthM)),
  };
  const layout = generateFarmLayout(layoutParameters);
  setStatus("Loading physics and the Franka Panda model…");
  const pandaBase = `${baseUrl}models/franka_emika_panda`;
  const simulation = await TomatoHarvestSimulation.create(layout, {
    readPandaFile: async (relativePath) => new Uint8Array(await (await fetch(`${pandaBase}/${relativePath}`)).arrayBuffer()),
    pandaAssetList: async () => (await fetch(`${pandaBase}/asset-list.json`)).json(),
  });
  const sceneHash = await sha256Hex(simulation.sceneXml);

  // ---------- Renderer, scene, cameras ----------
  const container = document.querySelector<HTMLDivElement>("#viewport")!;
  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
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

  scene.add(new THREE.HemisphereLight(0xfffaf0, 0x5a4a38, 1.5));
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

  const targetMarker = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.004, 8, 24), new THREE.MeshBasicMaterial({ color: 0x39ff88 }));
  worldRoot.add(targetMarker);

  const orbitCamera = new THREE.PerspectiveCamera(60, 1, 0.02, 200);
  const wristCamera = new THREE.PerspectiveCamera(75, 1, 0.01, 100);
  const headCamera = new THREE.PerspectiveCamera(70, 1, 0.02, 200);
  const cameraModes = ["orbit", "head", "wrist"] as const;
  let cameraMode: (typeof cameraModes)[number] = "orbit";
  const controls = new OrbitControls(orbitCamera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.495;
  // Start behind and above the cart, looking down the path (+x world = -z three).
  const toScene = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);
  orbitCamera.position.copy(toScene(-1.4, -0.9, 1.9));
  controls.target.copy(toScene(1.2, 0, 0.9));

  let splatWorld: LoadedSplatWorld | null = null;
  if (world) {
    setStatus("Loading the photoreal greenhouse…");
    try {
      splatWorld = await loadSplatWorld({
        baseUrl: worldBaseUrl,
        world,
        splatLevel: params.get("splat") ?? "500k",
        parent: worldRoot,
        layout,
        onProgress: (fraction) => setStatus(`Loading the photoreal greenhouse… ${Math.round(fraction * 100)}%`),
      });
      meshes.setGroundVisible(false);
      scene.background = new THREE.Color(0xe8ecef);
    } catch (error) {
      console.warn("World package failed to load; showing the work cell only", error);
    }
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
  let recorder: SessionRecorder | null = null;
  let replay: { recording: Recording; frame: number; playing: boolean; accumulator: number } | null = null;
  const teleop = new KeyboardTeleop(simulation, layout);

  const newRecordingHeader = () => ({
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
    operator: { device: "keyboard+mouse", user_agent: navigator.userAgent },
  });

  const downloadBlob = (blob: Blob, filename: string) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
  };

  const stopRecording = async () => {
    if (!recorder) return;
    const finished = recorder;
    recorder = null;
    hud.record.textContent = "● Record";
    hud.record.classList.remove("recording");
    const totals = tally(simulation.events);
    const header = finished.lines[0] as ReturnType<typeof newRecordingHeader>;
    const blob = await finished.toGzipBlob({ type: "footer", steps: finished.steps, duration_s: Number((finished.steps / CONTROL_RATE_HZ).toFixed(2)), harvested_ripe: totals.harvestedRipe, harvested_unripe: totals.harvestedUnripe, dropped: totals.dropped });
    downloadBlob(blob, `wefarm-session-${header.started_at.replace(/[:.]/g, "-")}.jsonl.gz`);
    setStatus(`Saved recording: ${finished.steps} steps (${(blob.size / 1024).toFixed(0)} KB). Load it with "Replay" to review.`);
  };

  hud.record.addEventListener("click", async () => {
    if (mode !== "play") return;
    if (recorder) return void (await stopRecording());
    simulation.reset();
    recorder = new SessionRecorder(newRecordingHeader());
    hud.record.textContent = "■ Stop & save";
    hud.record.classList.add("recording");
    setStatus("Recording from a fresh start. Pick the ripe tomatoes!");
  });
  hud.reset.addEventListener("click", () => {
    if (mode !== "play") return;
    recorder = null;
    hud.record.textContent = "● Record";
    hud.record.classList.remove("recording");
    simulation.reset();
  });
  const cycleCamera = () => {
    cameraMode = cameraModes[(cameraModes.indexOf(cameraMode) + 1) % cameraModes.length]!;
    controls.enabled = cameraMode === "orbit";
  };
  hud.cameraButton.addEventListener("click", cycleCamera);
  const toggleScenery = () => {
    if (!splatWorld) return;
    splatWorld.plantEraser.visible = !splatWorld.plantEraser.visible;
    hud.scenery.textContent = splatWorld.plantEraser.visible ? "Show photo plants" : "Hide photo plants";
  };
  hud.scenery.addEventListener("click", toggleScenery);
  hud.scenery.disabled = !splatWorld;
  window.addEventListener("keydown", (event) => {
    if ((event.target as HTMLElement).tagName === "TEXTAREA") return;
    if (event.code === "KeyC") cycleCamera();
    if (event.code === "KeyP") toggleScenery();
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
    const target = step.command.handTargetInCart;
    targetMarker.position.set(target[0] + step.qpos[0]!, target[1], target[2]);
  };
  hud.load.addEventListener("change", async () => {
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
  renderer.domElement.addEventListener("pointerup", (event) => {
    if (!pointerDown || mode !== "play" || cameraMode === "wrist") return;
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
    // Head camera: above the cart's rear, looking forward and down at the work area.
    const cartX = simulation.cartX();
    headCamera.position.copy(toScene(cartX - 0.55, 0, 1.75));
    headCamera.lookAt(toScene(cartX + 0.6, 0, 0.75));
  };

  let lastTime = performance.now();
  let accumulator = 0;
  let lastCartX = simulation.cartX();
  const controlPeriod = 1 / CONTROL_RATE_HZ;
  setStatus(splatWorld ? "Ready. Click a red tomato, or drive the hand with W A S D R F." : "Ready (no photoreal world found).");

  renderer.setAnimationLoop(() => {
    const now = performance.now();
    const elapsed = Math.min(0.1, (now - lastTime) / 1000);
    lastTime = now;
    if (mode === "play") {
      accumulator += elapsed;
      let ticks = 0;
      while (accumulator >= controlPeriod && ticks < 4) {
        teleop.applyHeldKeys(controlPeriod);
        simulation.controlTick();
        if (recorder) {
          const snapshot = simulation.snapshot();
          recorder.addStep(
            { i: simulation.controlStep - 1, t: simulation.time, command: { ...simulation.command, handTargetInCart: [...simulation.command.handTargetInCart] }, ctrl: simulation.actuatorTargets(), qpos: snapshot.qpos, attached: snapshot.attached },
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
    const cartX = simulation.cartX();
    const shift = toScene(cartX - lastCartX, 0, 0);
    orbitCamera.position.add(shift);
    controls.target.add(shift);
    lastCartX = cartX;
    sun.position.copy(toScene(cartX + 2, -3, 6));
    sun.target.position.copy(toScene(cartX + 0.5, 0, 0.5));
    placeCameras();
    if (controls.enabled) controls.update();
    renderer.render(scene, activeCamera());
  });
}

main().catch((error) => {
  console.error(error);
  showError((error as Error).stack ?? String(error));
  setStatus(`Failed to start: ${(error as Error).message}`);
});
