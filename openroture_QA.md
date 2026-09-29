# WEFARM QA Agent — OpenRouter decision

**Status: agreed implementation direction.** This document is the source of truth for new QA Agent work. The filename `openroture_QA.md` follows the requested spelling; the API provider is **OpenRouter**.

## Responsibility

WEFARM measures the quality of each recorded tomato-harvest episode. Its QA Agent calls an LLM through OpenRouter to interpret those measurements and produce an evidence-based decision and English explanation. OpenRouter provides model access; WEFARM owns the checks, thresholds, final approval, and marketplace policy. Do not send the native `.jsonl.gz` recording to a text model or ask it to invent observations.

## Quality contract

### Hard gates — failure means `reject`

1. **Data integrity:** required state, action, and timing streams exist; lengths agree; values are finite; timestamps and actions are valid.
2. **Reproducibility:** the recorded actions can be replayed in the simulator, within the configured state-divergence tolerance.
3. **Task completion:** replay verifies the tomato was picked and placed in the harvest tray. A recorded success flag alone is insufficient.
4. **Uniqueness:** an exact duplicate trajectory is rejected.

Never sell or reward an episode that fails a hard gate. A missing replay or an unavailable check is `review`, never an automatic pass.

### Quality signals — rank or flag episodes that pass the hard gates

- **Motion quality:** measured idle time and, when available, excessive oscillation or abrupt action changes.
- **Task accuracy:** measured drops and unintended collisions, when the simulator exposes those events. Expected grasp contact is not a failure.
- **Dataset value:** near-duplicate trajectories and coverage of different valid starting conditions, when those measurements exist.

The first demo may use the existing `idle_ratio` signal: below `0.20` is strong, `0.20–0.40` is acceptable, and `0.40` or above needs review. These are initial demo thresholds, not evidence that a trajectory will train a real robot well. Do not fabricate collision, smoothness, or near-duplicate metrics when they have not been implemented. Version thresholds and calibrate them against recorded examples before treating them as stable.

## Agent input and output

Send the OpenRouter model a compact JSON evidence summary: task and QA version, hard-gate results, replay measurements, quality signals, and any unavailable measurements. Require structured output with `decision` (`accept`, `review`, or `reject`), `reason`, `evidence_keys`, `model_id`, and `qa_version`. Keep all user-facing text in English.

The server validates the model response against the evidence. The model cannot override a failed hard gate. An absent, invalid, or unsupported model response becomes `review`; it cannot unlock a purchase. Only a hard-gate pass **and** a validated `accept` make an episode eligible for marketplace sale and player test credits. Store the rule results, model response, model ID, QA version, and final decision for audit.

## Implementation boundary

The current implementation runs structural checks and MuJoCo replay, builds a bounded evidence summary, and has an OpenRouter decision gate in code. The older Crusoe explanation adapter remains part of a separate legacy path. One synthetic live OpenRouter request returned a valid evidence-based QA response. **End-to-end game submission and approval have not been verified**, so describe the full workflow as under integration. Keep the hard gates while completing that validation.

Crusoe's assigned new role is AI Player VLM inference, defined in [`crusoe_player.md`](crusoe_player.md). Do not assign Crusoe to the new QA Agent. OpenRouter may also serve the orchestrator, but QA uses the contract in this document.
