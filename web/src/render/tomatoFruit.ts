// Gives the pickable tomatoes a realistic look: a slightly flattened, gently lobed fruit with a
// glossy skin, a per-fruit colour drawn from the ripeness stage, and a green calyx (the star of
// sepals) on top. Only the drawing changes; the physics shape stays MuJoCo's sphere of the same
// radius. Also provides a hover highlight for the tomato under the mouse.
import * as THREE from "three";
import type { FarmLayout, Ripeness } from "../farm/farmLayout.ts";
import { createSeededRandom, randomBetween } from "../farm/seededRandom.ts";
import type { MujocoMeshSet } from "./mujocoMeshes.ts";

/** Hue, saturation, lightness ranges per ripeness stage, tuned to vine-ripened cocktail tomatoes. */
const SKIN_COLOUR_RANGES: Record<Ripeness, { hue: [number, number]; saturation: [number, number]; lightness: [number, number] }> = {
  ripe: { hue: [0.0, 0.018], saturation: [0.82, 0.92], lightness: [0.36, 0.44] },
  turning: { hue: [0.045, 0.085], saturation: [0.85, 0.95], lightness: [0.46, 0.52] },
  green: { hue: [0.2, 0.25], saturation: [0.5, 0.62], lightness: [0.38, 0.46] },
};

export interface TomatoFruitVisuals {
  /** Highlight one tomato (by layout index), or none with null. */
  setHighlighted(tomatoIndex: number | null): void;
}

/** A unit fruit: slightly flattened top to bottom, with shallow lobes around the equator. */
function fruitGeometry(radius: number, lobeCount: number, lobeDepth: number): THREE.BufferGeometry {
  const geometry = new THREE.SphereGeometry(1, 36, 24);
  const position = geometry.getAttribute("position");
  const vertex = new THREE.Vector3();
  for (let index = 0; index < position.count; index += 1) {
    vertex.fromBufferAttribute(position, index);
    // Sphere geometry is y-up; the fruit's top (stem end) is +z in the body frame.
    const up = vertex.y;
    const around = Math.atan2(vertex.z, vertex.x);
    const equatorWeight = 1 - up * up;
    const lobes = 1 + lobeDepth * Math.cos(lobeCount * around) * equatorWeight;
    // A shallow dimple where the stem attaches.
    const dimple = up > 0.8 ? 1 - (up - 0.8) * 0.35 : 1;
    const flatten = 0.88;
    // (x, y, z) → (x, -z, y) is a rotation (keeps the triangles facing outwards).
    position.setXYZ(index, vertex.x * lobes * radius, -vertex.z * lobes * radius, up * flatten * dimple * radius);
  }
  geometry.computeVertexNormals();
  return geometry;
}

/** The calyx: five or six narrow sepals radiating from the stem end, curling down over the fruit. */
function calyxGeometry(radius: number, sepalCount: number, twist: number): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];
  for (let sepal = 0; sepal < sepalCount; sepal += 1) {
    const shape = new THREE.Shape();
    const length = radius * 0.95;
    const width = radius * 0.16;
    shape.moveTo(0, -width);
    shape.quadraticCurveTo(length * 0.6, -width * 0.9, length, 0);
    shape.quadraticCurveTo(length * 0.6, width * 0.9, 0, width);
    const piece = new THREE.ShapeGeometry(shape, 4);
    // Bend each sepal down over the curved top of the fruit.
    const position = piece.getAttribute("position");
    for (let index = 0; index < position.count; index += 1) {
      const along = position.getX(index) / length;
      position.setZ(index, -along * along * radius * 0.42);
    }
    piece.rotateZ((sepal / sepalCount) * Math.PI * 2 + twist);
    parts.push(piece);
  }
  const stub = new THREE.CylinderGeometry(radius * 0.09, radius * 0.12, radius * 0.35, 6).rotateX(Math.PI / 2).translate(0, 0, radius * 0.17);
  parts.push(stub);
  const merged = mergeGeometries(parts);
  merged.translate(0, 0, radius * 0.86);
  merged.computeVertexNormals();
  return merged;
}

function mergeGeometries(geometries: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = [];
  for (const geometry of geometries) {
    const source = geometry.index ? geometry.toNonIndexed() : geometry;
    positions.push(...(source.getAttribute("position").array as Float32Array));
  }
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return merged;
}

export function dressTomatoes(meshes: MujocoMeshSet, layout: FarmLayout): TomatoFruitVisuals {
  const random = createSeededRandom(layout.parameters.seed * 104729 + 3);
  const calyxMaterial = new THREE.MeshStandardMaterial({ color: 0x3d6b1f, roughness: 0.7, side: THREE.DoubleSide });
  const fruitMaterials: THREE.MeshPhysicalMaterial[] = [];
  const meshByTomato = new Map<number, THREE.Mesh>();
  for (const mesh of meshes.meshesByGeom.values()) {
    const match = /^tomato_(\d+)_geom$/.exec(mesh.name);
    if (!match) continue;
    const tomato = layout.tomatoes[Number(match[1])];
    if (!tomato) continue;
    const range = SKIN_COLOUR_RANGES[tomato.ripeness];
    const colour = new THREE.Color().setHSL(randomBetween(random, ...range.hue), randomBetween(random, ...range.saturation), randomBetween(random, ...range.lightness));
    const material = new THREE.MeshPhysicalMaterial({ color: colour, roughness: 0.38, clearcoat: 0.9, clearcoatRoughness: 0.12, emissive: 0x000000 });
    fruitMaterials[tomato.index] = material;
    mesh.geometry = fruitGeometry(tomato.radiusM, 5 + Math.floor(random() * 2), randomBetween(random, 0.025, 0.05));
    mesh.material = material;
    const calyx = new THREE.Mesh(calyxGeometry(tomato.radiusM, 5 + Math.floor(random() * 2), random() * Math.PI), calyxMaterial);
    calyx.castShadow = true;
    mesh.add(calyx);
    meshByTomato.set(tomato.index, mesh);
  }

  let highlighted: number | null = null;
  return {
    setHighlighted: (tomatoIndex) => {
      if (tomatoIndex === highlighted) return;
      if (highlighted !== null) fruitMaterials[highlighted]?.emissive.setHex(0x000000);
      highlighted = tomatoIndex;
      if (tomatoIndex !== null) fruitMaterials[tomatoIndex]?.emissive.setHex(0x3a3a18);
    },
  };
}
