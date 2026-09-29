// The tomato-path work cell: where the plants, trusses, and tomatoes are, and the harvest cart's
// dimensions. Everything is generated from a few parameters and a seed, so the same task always
// produces the same farm, in the browser and in any later Python re-simulation.
//
// World frame: metres, z up, right-handed. Origin on the ground at the start of the path;
// +x runs forward along the path, +y points to the left row, -y to the right row.
import { createSeededRandom, randomBetween } from "./seededRandom.ts";

export type Ripeness = "ripe" | "turning" | "green";

export interface TomatoSpec {
  index: number;
  name: string;
  position: [number, number, number];
  radiusM: number;
  ripeness: Ripeness;
  rgba: [number, number, number, number];
  side: "left" | "right";
  /** Where the fruit's stem joins its truss (for drawing the stem). */
  stemAnchor: [number, number, number];
}

export interface PlantSpec {
  side: "left" | "right";
  basePosition: [number, number, number];
  heightM: number;
  swayPhase: number;
  trussAnchors: Array<[number, number, number]>;
}

export interface CartSpec {
  lengthM: number;
  widthM: number;
  deckHeightM: number;
  deckThicknessM: number;
  wheelRadiusM: number;
  /** Arm base positions relative to the cart origin (the cart origin is on the ground, centred). */
  armMounts: Array<{ name: string; position: [number, number, number] }>;
  basket: { centre: [number, number, number]; innerLengthM: number; innerWidthM: number; heightM: number; wallM: number };
  /** Cart stops (x of the cart origin) along the path. */
  stationsX: number[];
}

export interface FarmLayoutParameters {
  seed: number;
  pathWidthM: number;
  rowStartX: number;
  rowLengthM: number;
  plantSpacingM: number;
  tomatoesPerTruss: [number, number];
  ripeFraction: number;
  turningFraction: number;
  armCount: 1;
}

export interface FarmLayout {
  parameters: FarmLayoutParameters;
  plants: PlantSpec[];
  tomatoes: TomatoSpec[];
  cart: CartSpec;
}

export const DEFAULT_FARM_PARAMETERS: FarmLayoutParameters = {
  seed: 7,
  pathWidthM: 1.2,
  rowStartX: 0.6,
  rowLengthM: 5.4,
  plantSpacingM: 0.6,
  tomatoesPerTruss: [3, 4],
  ripeFraction: 0.6,
  turningFraction: 0.2,
  armCount: 1,
};

const RIPENESS_COLOURS: Record<Ripeness, [number, number, number, number]> = {
  ripe: [0.78, 0.07, 0.04, 1],
  turning: [0.93, 0.45, 0.08, 1],
  green: [0.42, 0.62, 0.18, 1],
};

export function generateFarmLayout(parameters: FarmLayoutParameters = DEFAULT_FARM_PARAMETERS): FarmLayout {
  const random = createSeededRandom(parameters.seed);
  const plants: PlantSpec[] = [];
  const tomatoes: TomatoSpec[] = [];
  const rowOffsetY = parameters.pathWidthM / 2 + 0.2; // plant stems sit 0.2 m back from the path edge

  for (const side of ["left", "right"] as const) {
    const sign = side === "left" ? 1 : -1;
    const plantCount = Math.floor(parameters.rowLengthM / parameters.plantSpacingM) + 1;
    for (let plantIndex = 0; plantIndex < plantCount; plantIndex += 1) {
      const x = parameters.rowStartX + plantIndex * parameters.plantSpacingM + randomBetween(random, -0.05, 0.05);
      const y = sign * (rowOffsetY + randomBetween(random, -0.03, 0.03));
      const plant: PlantSpec = {
        side,
        basePosition: [x, y, 0],
        heightM: randomBetween(random, 1.7, 2.1),
        swayPhase: random() * Math.PI * 2,
        trussAnchors: [],
      };
      // One truss per plant, hanging towards the path at a reachable height.
      const trussHeight = randomBetween(random, 0.8, 1.2);
      const trussAnchor: [number, number, number] = [x + randomBetween(random, -0.08, 0.08), sign * (rowOffsetY - 0.1), trussHeight];
      plant.trussAnchors.push(trussAnchor);
      const count = Math.round(randomBetween(random, parameters.tomatoesPerTruss[0], parameters.tomatoesPerTruss[1]));
      for (let fruitIndex = 0; fruitIndex < count; fruitIndex += 1) {
        const radiusM = randomBetween(random, 0.017, 0.021);
        // Fruit hang in a short chain down and along the truss, spaced so they never touch.
        const position: [number, number, number] = [
          trussAnchor[0] + (fruitIndex - (count - 1) / 2) * 0.055,
          sign * (rowOffsetY - 0.14 - randomBetween(random, 0, 0.03)),
          trussAnchor[2] - 0.06 - (fruitIndex % 2) * 0.03,
        ];
        const roll = random();
        const ripeness: Ripeness = roll < parameters.ripeFraction ? "ripe" : roll < parameters.ripeFraction + parameters.turningFraction ? "turning" : "green";
        const index = tomatoes.length;
        tomatoes.push({
          index,
          name: `tomato_${index}`,
          position,
          radiusM,
          ripeness,
          rgba: RIPENESS_COLOURS[ripeness],
          side,
          stemAnchor: [position[0], trussAnchor[1], trussAnchor[2]],
        });
      }
      plants.push(plant);
    }
  }

  const cartWidthM = Math.min(0.62, parameters.pathWidthM - 0.3);
  const cart: CartSpec = {
    lengthM: 1.0,
    widthM: cartWidthM,
    deckHeightM: 0.42,
    deckThicknessM: 0.06,
    wheelRadiusM: 0.14,
    armMounts: [{ name: "arm0", position: [0.2, 0, 0.72] }],
    basket: { centre: [-0.25, 0, 0.45], innerLengthM: 0.34, innerWidthM: cartWidthM - 0.1, heightM: 0.14, wallM: 0.012 },
    stationsX: [],
  };
  for (let x = parameters.rowStartX - 0.2; x <= parameters.rowStartX + parameters.rowLengthM - 0.2; x += 0.6) cart.stationsX.push(Number(x.toFixed(3)));

  return { parameters, plants, tomatoes, cart };
}
