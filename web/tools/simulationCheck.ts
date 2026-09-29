// Quick headless check of the simulation core in Node: loads the scene, reaches for a ripe tomato,
// grips it, pulls it free, carries it over the basket, drops it, and reports timing.
// Run: node --experimental-strip-types tools/simulationCheck.ts
import { readFile } from "node:fs/promises";
import path from "node:path";
import { generateFarmLayout } from "../src/farm/farmLayout.ts";
import { TomatoHarvestSimulation } from "../src/sim/simulation.ts";

const pandaFolder = path.resolve(import.meta.dirname, "../public/models/franka_emika_panda");
const layout = generateFarmLayout();
const simulation = await TomatoHarvestSimulation.create(layout, {
  readPandaFile: async (relativePath) => new Uint8Array(await readFile(path.join(pandaFolder, relativePath))),
  pandaAssetList: async () => JSON.parse(await readFile(path.join(pandaFolder, "asset-list.json"), "utf8")),
});
console.log("nq", simulation.model.nq, "nv", simulation.model.nv, "tomatoes", layout.tomatoes.length, "physics steps per control", simulation.physicsStepsPerControl);

const tomato = layout.tomatoes.find((candidate) => candidate.ripeness === "ripe" && candidate.side === "left" && candidate.position[0] > 1.0)!;
console.log("target tomato", tomato.index, tomato.position.map((v) => v.toFixed(3)));
const station = layout.cart.stationsX.reduce((best, x) => (Math.abs(x + 0.2 - tomato.position[0]) < Math.abs(best + 0.2 - tomato.position[0]) ? x : best));

const started = performance.now();
let ticks = 0;
const runFor = (seconds: number, label: string) => {
  for (let index = 0; index < seconds * 50; index += 1) {
    simulation.controlTick();
    ticks += 1;
  }
  const hand = simulation.gripperCentreWorld();
  const target = simulation.handTargetWorld();
  const error = Math.hypot(hand[0] - target[0], hand[1] - target[1], hand[2] - target[2]);
  console.log(label.padEnd(16), "t", simulation.time.toFixed(2), "cart", simulation.cartX().toFixed(3), "hand", hand.map((v) => v.toFixed(3)).join(","), "err", error.toFixed(3), "tomato", simulation.tomatoStatus(tomato.index));
};
const setHandWorld = (x: number, y: number, z: number) => (simulation.handGoalInCart = [x - simulation.command.baseTarget[0], y, z]);

runFor(1, "settle");
simulation.command.baseTarget = [station, 0, 0];
runFor(2, "drive");
setHandWorld(tomato.position[0], tomato.position[1] - 0.08, tomato.position[2] + 0.12);
simulation.command.gripperYaw = 0;
runFor(3, "above-front");
setHandWorld(tomato.position[0] + Number(process.env.GRASP_DX ?? 0), tomato.position[1] + Number(process.env.GRASP_DY ?? 0), tomato.position[2] + Number(process.env.GRASP_DZ ?? 0));
runFor(2, "lower");
simulation.command.gripperOpen = false;
runFor(1, "close");
setHandWorld(tomato.position[0], tomato.position[1] - 0.1, tomato.position[2] + 0.25);
runFor(2, "pull");
const basket = layout.cart.basket;
simulation.handGoalInCart = [basket.centre[0], 0, basket.centre[2] + 0.3];
runFor(2.5, "over basket");
simulation.command.gripperOpen = true;
runFor(1.5, "release");
const elapsed = performance.now() - started;
console.log("events", JSON.stringify(simulation.events.filter((event) => event.tomato === tomato.index)));
console.log("other detaches", JSON.stringify(simulation.events.filter((event) => event.kind === "detach" && event.tomato !== tomato.index).map((e) => [e.tomato, e.time.toFixed(2), (e as { forceN: number }).forceN])));
console.log(`ms per control tick ${(elapsed / ticks).toFixed(2)} (budget 20 for real time)`);
