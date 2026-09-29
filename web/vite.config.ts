// Vite configuration for the WeFarm browser simulator.
// In development the server also exposes, read-only, /data/... from the data folder (world packages
// with splats). Without a local copy the page falls back to the public S3 copy (see render/splatWorld.ts).
import { createReadStream, existsSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(webRoot, "..");

function firstExistingDirectory(candidates: Array<string | undefined>): string | null {
  for (const candidate of candidates) if (candidate && existsSync(candidate) && statSync(candidate).isDirectory()) return candidate;
  return null;
}

/** Serve one folder read-only under a URL prefix, refusing any path that escapes the folder. */
function readOnlyFolder(urlPrefix: string, folder: string | null): Connect.NextHandleFunction {
  return (request, response, next) => {
    if (!request.url?.startsWith(`${urlPrefix}/`) || !folder) return next();
    const relativePath = decodeURIComponent(new URL(request.url, "http://localhost").pathname.slice(urlPrefix.length + 1));
    const absolutePath = path.resolve(folder, relativePath);
    if (!absolutePath.startsWith(folder + path.sep) || !existsSync(absolutePath) || !statSync(absolutePath).isFile()) {
      response.statusCode = 404;
      response.end("not found");
      return;
    }
    response.setHeader("Content-Length", String(statSync(absolutePath).size));
    response.setHeader("Cache-Control", "no-cache");
    createReadStream(absolutePath).pipe(response);
  };
}

function dataFolderPlugin(): Plugin {
  const dataFolder = firstExistingDirectory([process.env.WEFARM_DATA_DIR, "/data", path.join(repositoryRoot, "data")]);
  return {
    name: "wefarm-data-folder",
    configureServer: (server) => void server.middlewares.use(readOnlyFolder("/data", dataFolder)),
    configurePreviewServer: (server) => void server.middlewares.use(readOnlyFolder("/data", dataFolder)),
  };
}

export default defineConfig({
  plugins: [dataFolderPlugin()],
  // MuJoCo's WASM loader finds its .wasm next to its own module; pre-bundling would break that.
  optimizeDeps: { exclude: ["@mujoco/mujoco"] },
  server: { watch: process.env.VITE_USE_POLLING ? { usePolling: true, interval: 300 } : undefined },
  build: { target: "es2022", chunkSizeWarningLimit: 4000 },
});
