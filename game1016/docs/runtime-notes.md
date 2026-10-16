# Native simulator runtime

This is a separate Python3.11 environment and independent source/asset copies inside `game1016/`. No editable package points at the old hackathon checkout. `scripts/setup.sh` pins all source revisions and invokes `scripts/install_assets.py`. It automatically chooses the macOS `requirements.lock` on Darwin and pinned direct dependencies in `requirements.txt` on Linux, then writes the exact resolved package versions to `logs/requirements-<OS>.resolved.txt`. The macOS lock includes PyObjC and must not be used on Linux.

## Ubuntu/Debian GPU pod setup

Use a prebuilt NVIDIA runtime image with Python-capable Ubuntu/Debian and expose HTTP port8786. No PyTorch, policy weights, CUDA toolkit compilation or Isaac Sim installation is needed; the GPU provides headless OpenGL rendering. At container creation, request `NVIDIA_DRIVER_CAPABILITIES=all` (or at least `compute,utility,graphics`) so the host NVIDIA EGL libraries are exposed. Installing `nvidia-driver` inside the pod is not the fix for absent driver mounts.

Transfer `game1016` source excluding `.venv`, `.cache`, `.tools`, `vendor`, `runs` and logs. Keep sources/assets on persistent workspace storage. On the pod:

```sh
cd /workspace/AI_conference_HACKDAY26_0929/game1016
bash scripts/cloud_setup.sh
MUJOCO_GL=egl PYOPENGL_PLATFORM=egl .venv/bin/python -m coffee_game.server --host 0.0.0.0 --port 8786
```

`cloud_setup.sh` installs apt prerequisites using root/sudo: Git/curl/CA certificates, C build tools and libudev/libevdev for input package builds, GLVND/EGL/OpenGL dispatch libraries, GLFW/OSMesa, and X11 runtime libraries required by the non-headless OpenCV wheel. EGL does not require a visible X server or Xvfb. It installs `uv` into this game's `.tools/bin` only if absent and puts downloaded Python/cache files inside the game folder. It then invokes the shared pinned setup and prints NVIDIA driver/GPU information.

`nvidia-smi` confirms driver visibility, not working rendering. If EGL initialization fails, check that `libEGL_nvidia.so.0` and its EGL vendor registration are mounted and that this container sees an EGL device; do not change to software rendering and report GPU success. `MUJOCO_EGL_DEVICE_ID` may select a numeric EGL device if necessary; do not assume multi-GPU CUDA indices and EGL indices are interchangeable. Rendering/imports must use the environment variables above before importing MuJoCo or PyOpenGL. The macOS `cgl` setting must not be copied to Linux.

After starting, verify the public proxy's `/api/status`, actual camera images, native movement, stop/submit and downloadable files. A running process or HTTP shell response alone is not deployment validation. The server binds one simulation process; additional uvicorn workers would create independent sessions and should not be enabled for this local game.

## Renderer lifecycle

Creation, rendering, stepping, task reset, replay, environment close, and final garbage collection of MuJoCo/GL context owners must stay on the creating simulation/event-loop thread. Moving a closure that still references a simulator to a file-writing worker can make its last reference disappear there and invoke a GL destructor on the wrong thread; this caused a local reset-time crash during development. The current server keeps finalization on its simulation thread. Avoid introducing worker-thread ownership of renderer/simulator objects during cloud optimization. Ensure the previous environment is closed before a new task is constructed; failed constructors also require owner-thread cleanup. Linux GPU scene reset and end-to-end validation must be measured on the actual pod.

## Source and dependency choices

- RLDX `cf67c31f7f168ebbca6d06e8c3c23914cb94435c` embeds RoboCasa0.2 Kitchen24. Native coffee task classes and their success checks are untouched.
- robosuite is independently copied/cloned at `5ce6643f3092639d08f7b0f90ed1c6a84f50552c` (1.5.2). RLDX itself installs unpinned robosuite master, so there is no single original robosuite SHA to reproduce; this is the local tested revision.
- MuJoCo3.2.6 and NumPy1.26.4 match embedded RoboCasa assertions. Its setup.py requests obsolete numba0.56.4; current robosuite requests MuJoCo>=3.3. These declared constraints are incompatible, so setup installs both editable sources with `--no-deps`, then the explicitly tested runtime requirements. `pip check` therefore reports upstream constraints and is not a successful runtime validation.
- No policy checkpoints, GPU inference dependencies or torch installation is needed for human play.

## Assets

Legacy Box links return404. Asset archive URLs, revisions and SHA256 values are in `source-manifest.json`. The older `Livfour/robocasa-assets` mirror contains the fixtures and mug object format required by Kitchen24. The newer official `robocasa/robocasa-assets` archive lacks legacy bounding sites and must not be mixed in. The simulator uses unmodified legacy objects and collision geometry. Exact historical RLDX training asset parity remains unverified because archive hashes are not specified by RLDX.

To keep setup small, `fetch_mugs.py` fetches ZIP central directory plus a contiguous ~13MB mug range, checks each member CRC, and records per-file SHA256. `install_assets.py` downloads full fixtures(~451MiB) and textures(~513MiB) with checksum verification, restores tracked XML/configs to the pinned RLDX revision, and writes `vendor/assets-ready.json`. Other object categories and non-coffee tasks are outside this installation's supported scope.

Initial development reused independent APFS copies of cached assets to avoid a large download, then replaced selected fixtures, textures and mugs with the pinned archives. All source-tracked XML/configs are restored from RLDX. Unused cache files can remain under ignored vendor directories; they are not the provenance of the selected coffee assets.

## Observation/action contract

`CoffeeSimulator.observe()` captures pre-action state53, left/right/wrist RGB256, native integration state, simulation timestamp and step. Native camera rendering is512 and flipped vertically, then resized with OpenCV INTER_AREA. `step()` builds named action channels through the original PandaOmron converter, fills the controller's native vector by actual named indexes, and independently converts to dataset ordering12. Controller scales, named slices and camera metadata are in `metadata()`.

Training collection uses object splitA with layout/style pairs0/0,3/3,5/5. Held-out evaluation uses the wrapper's B objects and pairs1/1,2/2,4/4,6/9,7/10. RLDX's gym helper silently hardcodes eval settings; this adapter uses native robosuite.make so the training split can actually be selected. Seed/layout/object provenance is recorded.

`native_state` is MuJoCo mjSTATE_INTEGRATION plus CoffeeMachine's Python latched turned_on flag and episode step. qpos occupies indexes1 through1+nq. State restoration restores physics and coffee latch, refreshes controller reference state, and resets controller goals. For step0, the original reset goals and gripper accumulator are reapplied; arbitrary mid-episode controller caches are not serialized. Frame state playback and action replay are different checks. Action replay must measure deviations and report them, especially for a mid-episode restore. All simulator/renderer methods must execute on the creating thread.

## Current validation

Native CoffeeSetupMug reset, three camera renders, state53, action12/native12, one step and metadata generation succeeded on local Apple Silicon with MuJoCo3.2.6. This validates execution, not human task completion or policy performance. The root game's replay/recording checks provide additional evidence separately.
