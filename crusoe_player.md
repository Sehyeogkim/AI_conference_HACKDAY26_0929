# WEFARM AI Player — Crusoe VLM

**Status: backend adapter and browser AI Mode implemented; live Crusoe loop unverified.** This is the AI Player design. The QA Agent remains an independent OpenRouter workflow defined in [`openroture_QA.md`](openroture_QA.md). Crusoe is assigned to **AI Player inference**, not QA.

## Product behavior

An AI Player is an independent player identity that can join the same tomato-harvest game as a human. On the Player page, a user opens a game and selects **AI Mode**. The page shows the live simulation, the AI Player's current observation and chosen action, progress through the harvest, and the final QA result. The active WEFARM browser game in `web` produces a native `.jsonl.gz` session recording; AI play must use that same format, identify the operator as AI, and receive the same structural, replay, task-success, and marketplace QA as human play. The demo must show a live action loop, not a prerecorded clip presented as live inference.

## Control loop

```text
Game camera frame + compact game status + task goal
  -> WEFARM AI Player controller
  -> VLM hosted on the Crusoe GPU instance
  -> structured next action
  -> validated short robot command or bounded skill
  -> simulator advances and records states/actions
  -> fresh frame and status; repeat until success, stop, or timeout
```

The VLM sees rendered frames, preferably the main view and wrist camera, plus only the game status needed to act. Its goal is to pick the tomato and place it in the harvest tray. It chooses one **bounded action at a time**: move base, position arm, orient wrist, open/close gripper, wait, request another view, or stop. The WEFARM controller validates the output, clamps motion and duration, and uses the existing simulator/controller to execute it. The VLM does not send unrestricted motor commands directly to MuJoCo. After every short action, it observes the new result and may recover from a mistake. Do not claim that a single prompt can control a robot continuously at the simulator's frame rate.

Example action schema for the first integration (exact skill names must match the selected game adapter):

```json
{
  "action": "move_arm",
  "direction": "forward",
  "duration_s": 0.4,
  "reason": "Move the gripper toward the visible tomato"
}
```

All actions need a strict allowlist, time limit, movement limit, and server-side validation. A failed or malformed VLM response pauses the AI Player; it must not leave movement active. Keep inference keys and model runtime on the server. Record frame IDs, model ID, action decisions, execution outcomes, and timestamps alongside the episode for a reviewable demo.

## Crusoe role

Run a compatible **vision-language model** on the Crusoe GPU instance and expose a private inference endpoint to the WEFARM AI Player controller. The browser never calls the GPU endpoint directly. Select the exact VLM and serving runtime only after checking the instance's GPU memory, model license, image input support, latency, and actual endpoint response. The existing SSH details do not by themselves prove that a VLM is installed or serving. If the model is too slow, use shorter observations and higher-level skills; report actual latency in the demo rather than claiming real-time control without measurement.

## Website flow

1. A player chooses **Tomato Harvest** from the marketplace and opens the game.
2. The game offers **Human Mode** and **AI Mode**. AI Mode is explicit; it never starts automatically.
3. Selecting AI Mode reserves control for the AI Player, starts the Crusoe VLM loop, and shows `Observing`, `Thinking`, `Acting`, or `Paused` with the selected action and elapsed time.
4. **Pause**, **Stop**, and **Take control** cancel queued actions and return control safely to the human. A single game session has only one active controller at a time.
5. On task completion, the AI Player submits its native `.jsonl.gz` recording. The independent QA Agent evaluates it; the UI displays the real result and any reason for rejection or review.

The active browser game runs MuJoCo in the browser and currently accepts keyboard and mouse input. Implement an AI controller adapter and exclusive control ownership before enabling the AI Mode button. Keep human input and AI commands on the same recording path so their episodes can be compared fairly. The older `sim/web` RoboCasa/HDF5 path is a legacy demo and is not the target for this feature.

## Demo acceptance checks

- A visible camera frame reaches the Crusoe-hosted VLM and a real model response is logged.
- The response drives an allowed robot action in the live game; a later frame shows its effect.
- The loop completes or stops cleanly, and the saved native recording identifies an AI operator and the model used.
- The episode enters the same QA pipeline as human play. A failed harvest is shown as failed, not relabeled as successful.
- If Crusoe inference is unavailable, AI Mode shows a clear unavailable state; the human game remains playable.

## Current versus planned

**Current:** the Crusoe VLM decision adapter, backend API, and browser AI Mode controller exist. With no Crusoe endpoint or model configured locally, AI Mode displays an unavailable state and human control remains available. The older scripted/LLM skill planner targets the legacy simulator and is not evidence of a live Crusoe VLM player. No real Crusoe response or complete frame-to-inference-to-action loop has been verified. **Next validation:** observe a live frame, model response, bounded action, and native recording together. Do not describe the AI Player as live before that verification.
