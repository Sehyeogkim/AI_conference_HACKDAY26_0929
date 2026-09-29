// Realistic materials for the harvest cart, drawn over MuJoCo's flat geom colours: a galvanised
// steel trolley (as used on greenhouse pipe rails), a dark arm post, rubber wheels, and a green
// plastic harvest crate. Only the drawing changes; the physics geoms are untouched.
import * as THREE from "three";
import type { MujocoMeshSet } from "./mujocoMeshes.ts";

const galvanisedSteel = () => new THREE.MeshStandardMaterial({ color: 0x9aa1a6, metalness: 0.75, roughness: 0.38 });
const darkSteel = () => new THREE.MeshStandardMaterial({ color: 0x3a3f44, metalness: 0.6, roughness: 0.45 });
const rubber = () => new THREE.MeshStandardMaterial({ color: 0x1b1b1b, metalness: 0, roughness: 0.9 });
const crateFloorPlastic = () => new THREE.MeshStandardMaterial({ color: 0x24472a, metalness: 0, roughness: 0.55 });
const cratePlastic = () => new THREE.MeshStandardMaterial({ color: 0x2f5f35, metalness: 0, roughness: 0.5 });

/** Geom name (exact, or prefix ending in "_") → material factory. */
const MATERIALS_BY_GEOM_NAME: Array<[string, () => THREE.Material]> = [
  ["cart_deck", galvanisedSteel],
  ["cart_frame", darkSteel],
  ["wheel_", rubber],
  ["arm0_post", darkSteel],
  ["basket_floor", crateFloorPlastic],
  ["basket_wall_", cratePlastic],
];

export function dressCart(meshes: MujocoMeshSet): void {
  for (const mesh of meshes.meshesByGeom.values()) {
    const entry = MATERIALS_BY_GEOM_NAME.find(([name]) => (name.endsWith("_") ? mesh.name.startsWith(name) : mesh.name === name));
    if (entry) mesh.material = entry[1]();
  }
}
