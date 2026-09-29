# Two-camera AI Player and marketplace merge

The browser AI Player now sends separate 640×360 head and wrist JPEGs to the Crusoe VLM. The wrist capture uses the simulator's mesh-only render path, preserving the splat flicker guard. The server requires both images and forwards them as two labeled image parts.

The marketplace retains the World Labs demo and replay paths while adding episode QA lifecycle, listing detail, and Neo4j graph views. Cached OpenRouter demo answers remain `pending_review` and cannot approve or sell an episode; live OpenRouter approval still requires all hard gates.

Local checks before push: Python tests, TypeScript check, Vite build, and headless MuJoCo harvest smoke test passed. One live Crusoe call produced a bounded `move_base forward` action from the two images; a complete autonomous harvest was not verified.
