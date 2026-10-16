# Native recording and learning export

`coffee_game.recording.EpisodeRecorder` writes all new output under the supplied
root. Each episode is a standalone LeRobot v2.1 dataset with local episode index
and task index zero, retaining the globally unique `episode_id` in provenance.
Do not concatenate episode Parquet files without reindexing their metadata.

```python
recorder = EpisodeRecorder(root, metadata, task_id, seed)
# Capture observation before step; append the action that was actually executed.
recorder.append(observation_before_step, executed_result, input_state, simulation_time)
submission = recorder.finalize("user_stop")
```

The observation contract is `state: float[53]`, `native_state: float[N]`, and
`images: {left, right, wrist}` with RGB uint8 `(256,256,3)` arrays. The result
contract is `action: float[12]`, `native_action: float[A]`, `reward`, `success`,
and optional `done`. Dataset action mode and gripper entries use binary 0/1;
native actions retain simulator encoding. Observation times advance by 0.05 s.

Metadata should include the generated `model_xml`, `env_info`, `ep_meta`, source
revisions, robot/controller/camera configuration and `native_state_kind`.
Initial and per-step pre-action native state vectors preserve a replay source;
only the simulator can establish whether these vectors are a complete state
for its version. The recorded result is associated with the same row's action.

```
<root>/<episode_id>/
  metadata.json
  submission.json
  raw/episode.hdf5
  replay/model.xml
  replay/environment.json
  lerobot/data/chunk-000/episode_000000.parquet
  lerobot/videos/chunk-000/observation.images.{left,right,wrist}_view/episode_000000.mp4
  lerobot/meta/{info,modality,stats,provenance}.json
  lerobot/meta/{episodes,tasks,episodes_stats}.jsonl
  qa/report.json
<root>/<episode_id>.zip
```

HDF5 top-level datasets: `state`, `action`, `native_state`, `native_action`,
`timestamp`, `wall_timestamp`, `reward`, `success`, `done`, `input_state`, and
`images/{left,right,wrist}_view`. HDF5 attributes include JSON `metadata`,
`env_info`, `ep_meta` and `observation_timing=pre-action`. Images are losslessly
compressed in HDF5 while three H.264 MP4s are streamed independently. At most
one timestep of RGB images is buffered by this module; numerical arrays are
loaded at finalization for Parquet and statistics.

Finalization closes all writers, exports, checks shapes/finite values/20Hz
timestamps/binary encodings/frame counts and video decoding, then atomically
renames the staging directory. Interrupted episodes remain in `.partial`
directories and are not listed as submitted. Repeated finalization is
idempotent. Missing camera video never silently passes QA.

`inspect_episode(path)` checks structural integrity. `set_replay_qa(path, report)`
can attach the result of **an actual simulator replay** and regenerate the ZIP.
Until that happens `action_replay.status=not_run` and `training_candidate=false`,
even when native task success was observed. Training performance is never
claimed by recording QA. Positive provenance eligibility additionally requires
`end_reason=task_success`, `mode=collection`, `split=train`, and observed native
success. Discarded, stopped, timed-out, shutdown, practice and evaluation
episodes therefore retain human validity 0 even if a success flag is present.
Missing provenance fields fail closed. Failure episodes are retained;
the final submitted frame is marked `next.done=true` even after a user stop,
whose reason is preserved separately.

Run structural validation:

```sh
.venv/bin/python scripts/check_dataset.py recordings/<episode_id>
```

State/action slices follow the public reference's
[modality metadata](https://huggingface.co/datasets/kimtaey/robocasa_mg_gr00t_300/blob/main/meta/modality.json).
Matching these fields does not prove an RLDX training loader has accepted a
dataset; run that loader as a separate compatibility check.

Numeric `stats.json` includes per-dimension `q01` and `q99` in addition to
LeRobot's min/max/mean/std/count: RLDX's percentile normalization requires these
fields. `episodes_stats.jsonl` retains the v2.1 per-episode statistics wrapper.
Video features provide v2.1 `info` plus the public reference's `video_info` alias.
