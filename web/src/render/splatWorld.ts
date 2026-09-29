// Loads a world package (World Labs Marble scene) as the photoreal backdrop: the Gaussian splat,
// placed by the package's raw-to-world matrix (Marble raw OpenCV frame → z-up world metres), and
// a SplatEdit that erases the splat's own plants where the generated work-cell plants stand, so no
// tomato appears twice.
import * as THREE from "three";
import { SplatEdit, SplatEditRgbaBlendMode, SplatEditSdf, SplatEditSdfType, SplatMesh } from "@sparkjsdev/spark";
import type { FarmLayout } from "../farm/farmLayout.ts";

export interface WorldPackage {
  world_id: string;
  version: number;
  model: string;
  raw_to_world: number[][];
  path?: { width_m?: number; usable_length_m?: number };
  scale?: { meters_per_raw_unit?: number; method?: string };
  input?: { page_url?: string; author?: string; license?: string };
  files: { splats: Record<string, string>; collider?: string; pano?: string };
  /** Present when Marble's own scale disagreed with the camera-height estimate. */
  scale_alternative_camera_height?: { meters_per_raw_unit: number; raw_to_world: number[][]; path_width_m: number; path_usable_length_m: number };
}

/**
 * Pick the scale to use. The 2026-09-29 standard world's Marble scale implies a 2.18 m camera
 * height (36% off a hand-held 1.6 m); the camera-height alternative agrees with the draft world,
 * so it is the default. `?scale=marble` uses Marble's own scale instead.
 */
export function applyScaleChoice(world: WorldPackage, choice: string | null): WorldPackage {
  const alternative = world.scale_alternative_camera_height;
  if (!alternative || choice === "marble") return world;
  return { ...world, raw_to_world: alternative.raw_to_world, path: { ...world.path, width_m: alternative.path_width_m, usable_length_m: alternative.path_usable_length_m } };
}

export interface LoadedSplatWorld {
  world: WorldPackage;
  splat: SplatMesh;
  plantEraser: SplatEdit;
}

export async function loadWorldPackage(baseUrl: string): Promise<WorldPackage> {
  const response = await fetch(`${baseUrl}/world.json`);
  if (!response.ok) throw new Error(`World package not found at ${baseUrl} (${response.status})`);
  return (await response.json()) as WorldPackage;
}

/**
 * Public, read-only copies of the world packages (S3 bucket, `worlds/` folder only; CC BY-SA
 * attribution in each package's ATTRIBUTION.txt). Used when no local copy exists, so a fresh
 * checkout shows the photoreal scene without downloading anything by hand.
 */
export const REMOTE_WORLD_PACKAGES_URL = "https://wefarm-aiconf-2026-assets.s3.us-west-2.amazonaws.com/worlds";

export interface WorldPackageSource {
  baseUrl: string;
  /** "local" = served from this machine's data folder; "remote" = downloaded from the public bucket. */
  origin: "local" | "remote";
}

/**
 * Where to look for a world package, in order. `?world=<id>/<version>` (e.g. `crete-path/v2`)
 * picks a package by name; `?world=<url>` loads that exact folder; no option means the default
 * world. Local copies come first; the public bucket is the fallback.
 */
export function worldPackageSources(worldOption: string | null): WorldPackageSource[] {
  if (worldOption && /^(https?:)?\/\//.test(worldOption)) return [{ baseUrl: worldOption.replace(/\/$/, ""), origin: "remote" }];
  if (worldOption && worldOption.startsWith("/")) return [{ baseUrl: worldOption.replace(/\/$/, ""), origin: "local" }];
  const packageIds = worldOption ? [worldOption] : ["crete-path/v2"];
  return [
    ...packageIds.map((id) => ({ baseUrl: `/data/worlds/${id}`, origin: "local" as const })),
    ...packageIds.map((id) => ({ baseUrl: `${REMOTE_WORLD_PACKAGES_URL}/${id}`, origin: "remote" as const })),
  ];
}

export function splatFileFor(world: WorldPackage, splatLevel: string): string {
  const splatFile = world.files.splats[splatLevel] ?? Object.values(world.files.splats)[0];
  if (!splatFile) throw new Error("World package lists no splat files");
  return splatFile;
}

/** Download a file while reporting bytes received (total is 0 when the server does not say). */
export async function downloadWithProgress(url: string, onProgress: (loadedBytes: number, totalBytes: number) => void): Promise<Uint8Array> {
  const response = await fetch(url);
  if (!response.ok || !response.body) throw new Error(`Download failed: ${url} (${response.status})`);
  const totalBytes = Number(response.headers.get("Content-Length") ?? 0);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loadedBytes = 0;
  onProgress(0, totalBytes);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loadedBytes += value.byteLength;
    onProgress(loadedBytes, totalBytes);
  }
  const bytes = new Uint8Array(loadedBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

export async function loadSplatWorld(options: {
  baseUrl: string;
  world: WorldPackage;
  splatLevel: string;
  parent: THREE.Object3D;
  layout: FarmLayout;
  /** Already-downloaded splat file (from `downloadWithProgress`); fetched from `baseUrl` when absent. */
  splatFileBytes?: Uint8Array;
  onProgress?: (fraction: number) => void;
}): Promise<LoadedSplatWorld> {
  const { world, parent, layout } = options;
  const splatFile = splatFileFor(world, options.splatLevel);
  const splat = options.splatFileBytes
    ? new SplatMesh({ fileBytes: options.splatFileBytes, fileName: splatFile.split("/").pop() })
    : new SplatMesh({
        url: `${options.baseUrl}/${splatFile}`,
        onProgress: (event: ProgressEvent) => {
          if (event.lengthComputable && event.total > 0) options.onProgress?.(event.loaded / event.total);
        },
      });
  const m = world.raw_to_world;
  const matrix = new THREE.Matrix4().set(
    m[0]![0]!, m[0]![1]!, m[0]![2]!, m[0]![3]!,
    m[1]![0]!, m[1]![1]!, m[1]![2]!, m[1]![3]!,
    m[2]![0]!, m[2]![1]!, m[2]![2]!, m[2]![3]!,
    0, 0, 0, 1,
  );
  matrix.decompose(splat.position, splat.quaternion, splat.scale);
  parent.add(splat);
  await splat.initialized;

  // Erase the splat's plants alongside the work cell: one box per row, from just outside the path
  // edge to behind the generated plants, over the length of the generated rows.
  const plantEraser = new SplatEdit({ rgbaBlendMode: SplatEditRgbaBlendMode.MULTIPLY, softEdge: 0.08 });
  const { rowStartX, rowLengthM, pathWidthM } = layout.parameters;
  const lengthM = rowLengthM + 0.9;
  const centreX = rowStartX + rowLengthM / 2;
  for (const sign of [1, -1]) {
    const box = new SplatEditSdf({ type: SplatEditSdfType.BOX, opacity: 0 });
    const innerY = pathWidthM / 2 - 0.05;
    const outerY = pathWidthM / 2 + 0.75;
    box.position.set(centreX, sign * (innerY + outerY) / 2, 1.2);
    box.scale.set(lengthM / 2, (outerY - innerY) / 2, 1.15);
    plantEraser.addSdf(box);
  }
  parent.add(plantEraser);
  return { world, splat, plantEraser };
}
