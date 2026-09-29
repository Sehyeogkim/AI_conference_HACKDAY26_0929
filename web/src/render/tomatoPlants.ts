// Visual tomato plants for the work cell: stems, support strings, instanced leaves, truss stalks,
// and a short fruit stem per tomato that disappears when the tomato comes free. Plants are drawn
// only (no physics); the tomatoes themselves are MuJoCo bodies drawn by mujocoMeshes.ts.
import * as THREE from "three";
import type { FarmLayout } from "../farm/farmLayout.ts";
import { createSeededRandom, randomBetween } from "../farm/seededRandom.ts";

export interface TomatoPlantVisuals {
  root: THREE.Group;
  setFruitStemVisible(tomatoIndex: number, visible: boolean): void;
  /**
   * Generated stems, strings, and leaves are shown only when the photo's own plants are erased;
   * the truss and fruit stalks always show, coming out of whichever plants are visible.
   */
  setGeneratedFoliageVisible(visible: boolean): void;
}

function leafGeometry(): THREE.BufferGeometry {
  const shape = new THREE.Shape();
  shape.moveTo(0, 0);
  shape.bezierCurveTo(0.035, 0.03, 0.045, 0.1, 0, 0.16);
  shape.bezierCurveTo(-0.045, 0.1, -0.035, 0.03, 0, 0);
  const geometry = new THREE.ShapeGeometry(shape, 6);
  // Bend the leaf a little so it is not a flat card.
  const position = geometry.getAttribute("position");
  for (let index = 0; index < position.count; index += 1) {
    const x = position.getX(index);
    const y = position.getY(index);
    position.setZ(index, -Math.abs(x) * 0.6 - y * y * 0.8);
  }
  geometry.computeVertexNormals();
  return geometry;
}

export function buildTomatoPlants(layout: FarmLayout): TomatoPlantVisuals {
  const root = new THREE.Group();
  root.name = "tomato-plants";
  const random = createSeededRandom(layout.parameters.seed * 7919 + 17);
  const stemMaterial = new THREE.MeshStandardMaterial({ color: 0x4f7a2a, roughness: 0.8 });
  const stalkMaterial = new THREE.MeshStandardMaterial({ color: 0x55682f, roughness: 0.85 });
  const stringMaterial = new THREE.MeshStandardMaterial({ color: 0xd9d2b8, roughness: 1 });
  const leafMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.75, side: THREE.DoubleSide });

  const foliage = new THREE.Group();
  foliage.name = "generated-foliage";
  root.add(foliage);
  const leafTransforms: THREE.Matrix4[] = [];
  const leafColours: THREE.Color[] = [];
  const dummy = new THREE.Object3D();

  for (const plant of layout.plants) {
    const [baseX, baseY] = plant.basePosition;
    const towardPath = plant.side === "left" ? -1 : 1;
    // Stem: a gently curving tube from the ground to the top.
    const points: THREE.Vector3[] = [];
    for (let step = 0; step <= 8; step += 1) {
      const height = (plant.heightM * step) / 8;
      points.push(new THREE.Vector3(baseX + Math.sin(plant.swayPhase + step * 0.7) * 0.03, baseY + Math.cos(plant.swayPhase + step * 0.5) * 0.025, height));
    }
    const stem = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 24, 0.011, 6, false), stemMaterial);
    stem.castShadow = true;
    foliage.add(stem);
    const twine = new THREE.Mesh(new THREE.CylinderGeometry(0.002, 0.002, 2.6, 4).rotateX(Math.PI / 2), stringMaterial);
    twine.position.set(baseX, baseY, 1.3 + 0.9);
    foliage.add(twine);

    // Leaves: compound-leaf clusters along the stem, mostly facing away from the path so the
    // trusses stay reachable.
    const leafCount = Math.round(plant.heightM * 34);
    for (let leafIndex = 0; leafIndex < leafCount; leafIndex += 1) {
      const height = randomBetween(random, 0.15, plant.heightM);
      const point = points[Math.min(8, Math.round((height / plant.heightM) * 8))]!;
      const outward = randomBetween(random, -Math.PI * 0.95, Math.PI * 0.95);
      const facing = Math.atan2(-towardPath, 0) + outward * 0.9;
      const reach = randomBetween(random, 0.02, 0.2);
      dummy.position.set(point.x + Math.cos(facing) * reach * 0.8, point.y + Math.sin(facing) * reach, height);
      dummy.rotation.set(randomBetween(random, 0.6, 1.4), 0, facing - Math.PI / 2, "ZXY");
      dummy.rotateZ(randomBetween(random, -0.5, 0.5));
      const scale = randomBetween(random, 0.8, 1.5);
      dummy.scale.set(scale, scale, scale);
      dummy.updateMatrix();
      leafTransforms.push(dummy.matrix.clone());
      leafColours.push(new THREE.Color().setHSL(randomBetween(random, 0.22, 0.3), randomBetween(random, 0.45, 0.65), randomBetween(random, 0.2, 0.34)));
    }

    // Truss stalks from the stem to each truss anchor.
    for (const anchor of plant.trussAnchors) {
      const stemPoint = new THREE.Vector3(baseX, baseY, anchor[2] + 0.06);
      const anchorPoint = new THREE.Vector3(...anchor);
      root.add(cylinderBetween(stemPoint, anchorPoint, 0.0035, stalkMaterial));
    }
  }

  const leaves = new THREE.InstancedMesh(leafGeometry(), leafMaterial, leafTransforms.length);
  leafTransforms.forEach((matrix, index) => {
    leaves.setMatrixAt(index, matrix);
    leaves.setColorAt(index, leafColours[index]!);
  });
  leaves.castShadow = true;
  leaves.receiveShadow = true;
  foliage.add(leaves);

  const fruitStems = layout.tomatoes.map((tomato) => {
    const top = new THREE.Vector3(...tomato.stemAnchor);
    const fruitTop = new THREE.Vector3(tomato.position[0], tomato.position[1], tomato.position[2] + tomato.radiusM);
    const stalk = cylinderBetween(top, fruitTop, 0.0018, stalkMaterial);
    root.add(stalk);
    return stalk;
  });

  return {
    root,
    setFruitStemVisible: (tomatoIndex, visible) => {
      const stalk = fruitStems[tomatoIndex];
      if (stalk) stalk.visible = visible;
    },
    setGeneratedFoliageVisible: (visible) => {
      foliage.visible = visible;
    },
  };
}

function cylinderBetween(start: THREE.Vector3, end: THREE.Vector3, radius: number, material: THREE.Material): THREE.Mesh {
  const direction = new THREE.Vector3().subVectors(end, start);
  const length = direction.length();
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 5), material);
  mesh.position.copy(start).addScaledVector(direction, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
  return mesh;
}
