// Draws every visible MuJoCo geom as a three.js mesh and poses it from the simulation each frame.
// The robot's visual meshes come straight from the compiled MuJoCo model, so what is drawn is
// exactly what is simulated. Collision-only geoms (group 3) are hidden.
import * as THREE from "three";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MjModel = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MjData = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MujocoModule = any;

const HIDDEN_GEOM_GROUPS = new Set([3]);

export interface MujocoMeshSet {
  root: THREE.Group;
  /** geom id → mesh (only visible geoms). */
  meshesByGeom: Map<number, THREE.Mesh>;
  update(data: MjData): void;
  setGroundVisible(visible: boolean): void;
}

export function buildMujocoMeshes(mujoco: MujocoModule, model: MjModel): MujocoMeshSet {
  const root = new THREE.Group();
  root.name = "mujoco-geoms";
  const meshesByGeom = new Map<number, THREE.Mesh>();
  const meshGeometryCache = new Map<number, THREE.BufferGeometry>();
  const geomType = mujoco.mjtGeom;
  let groundMesh: THREE.Mesh | null = null;

  for (let geomId = 0; geomId < model.ngeom; geomId += 1) {
    if (HIDDEN_GEOM_GROUPS.has(model.geom_group[geomId])) continue;
    const type = model.geom_type[geomId];
    const size = [model.geom_size[geomId * 3], model.geom_size[geomId * 3 + 1], model.geom_size[geomId * 3 + 2]] as [number, number, number];
    let geometry: THREE.BufferGeometry;
    if (type === geomType.mjGEOM_PLANE.value) geometry = new THREE.PlaneGeometry(60, 60);
    else if (type === geomType.mjGEOM_SPHERE.value) geometry = new THREE.SphereGeometry(size[0], 24, 16);
    else if (type === geomType.mjGEOM_BOX.value) geometry = new THREE.BoxGeometry(size[0] * 2, size[1] * 2, size[2] * 2);
    else if (type === geomType.mjGEOM_CYLINDER.value) geometry = new THREE.CylinderGeometry(size[0], size[0], size[1] * 2, 24).rotateX(Math.PI / 2);
    else if (type === geomType.mjGEOM_CAPSULE.value) geometry = new THREE.CapsuleGeometry(size[0], size[1] * 2, 8, 16).rotateX(Math.PI / 2);
    else if (type === geomType.mjGEOM_MESH.value) {
      const meshId = model.geom_dataid[geomId];
      let cached = meshGeometryCache.get(meshId);
      if (!cached) {
        cached = meshGeometry(model, meshId);
        meshGeometryCache.set(meshId, cached);
      }
      geometry = cached;
    } else continue;

    const materialId = model.geom_matid[geomId];
    const rgbaSource = materialId >= 0 ? model.mat_rgba : model.geom_rgba;
    const rgbaIndex = (materialId >= 0 ? materialId : geomId) * 4;
    const colour = new THREE.Color(rgbaSource[rgbaIndex], rgbaSource[rgbaIndex + 1], rgbaSource[rgbaIndex + 2]);
    const name: string = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM.value, geomId) ?? "";
    const isTomato = name.startsWith("tomato_");
    const material = new THREE.MeshStandardMaterial({
      color: colour,
      roughness: isTomato ? 0.32 : type === geomType.mjGEOM_PLANE.value ? 1 : 0.55,
      metalness: 0.05,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = name || `geom_${geomId}`;
    mesh.matrixAutoUpdate = false;
    mesh.castShadow = type !== geomType.mjGEOM_PLANE.value;
    mesh.receiveShadow = true;
    mesh.userData.geomId = geomId;
    if (type === geomType.mjGEOM_PLANE.value) groundMesh = mesh;
    root.add(mesh);
    meshesByGeom.set(geomId, mesh);
  }

  const update = (data: MjData) => {
    const positions = data.geom_xpos;
    const rotations = data.geom_xmat;
    for (const [geomId, mesh] of meshesByGeom) {
      const p = geomId * 3;
      const r = geomId * 9;
      mesh.matrix.set(
        rotations[r], rotations[r + 1], rotations[r + 2], positions[p],
        rotations[r + 3], rotations[r + 4], rotations[r + 5], positions[p + 1],
        rotations[r + 6], rotations[r + 7], rotations[r + 8], positions[p + 2],
        0, 0, 0, 1,
      );
      mesh.matrixWorldNeedsUpdate = true;
    }
  };

  return {
    root,
    meshesByGeom,
    update,
    setGroundVisible: (visible) => {
      if (groundMesh) groundMesh.visible = visible;
    },
  };
}

function meshGeometry(model: MjModel, meshId: number): THREE.BufferGeometry {
  const vertexStart = model.mesh_vertadr[meshId];
  const vertexCount = model.mesh_vertnum[meshId];
  const faceStart = model.mesh_faceadr[meshId];
  const faceCount = model.mesh_facenum[meshId];
  const positions = new Float32Array(model.mesh_vert.subarray(vertexStart * 3, (vertexStart + vertexCount) * 3));
  const indices = new Uint32Array(model.mesh_face.subarray(faceStart * 3, (faceStart + faceCount) * 3));
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.computeVertexNormals();
  return geometry;
}
