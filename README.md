# WeFarm

**Play the harvest. Capture robot data.**

WeFarm connects a requester marketplace to the teammate's browser MuJoCo
tomato-harvesting simulator. A requester uploads a farm image and a data brief;
WeFarm stores both, uses OpenRouter to extract a constrained task spec when
configured, and publishes a playable game. The image-to-3D environment is an
**assumed teammate handoff** for this hackathon. This service does not generate
a new 3D farm from the uploaded image.

Players drive a one-arm Franka Panda cart through procedural tomato rows. The
game uploads its native `.jsonl.gz` recording to WeFarm. Structural checks,
server-side MuJoCo re-simulation, and an evidence-only OpenRouter QA decision
gate test-credit rewards and marketplace purchases. Crusoe is assigned to
AI Player VLM inference. Neo4j indexes task, episode, QA, and purchase
metadata; the original recording remains in file storage. See the
[architecture and limits](final_architecutre.md).

## Run locally

Requires Python 3.12+, Node.js, and `uv` or an equivalent virtual environment.
The game checks for a local World Labs package, then downloads the public
`crete-path/v2` greenhouse from S3 when needed. If that download fails, the
procedural work cell remains playable. The uploaded request image does not
create a new 3D world.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
npm ci --prefix web
```

Start the game in one terminal:

```bash
cd web
npm run dev -- --host 127.0.0.1 --port 5180
```

Start the WeFarm site in another terminal, from the repository root:

```bash
.venv/bin/python -m agriphilo.web --host 127.0.0.1 --port 8765 --demo
```

Open [Requester](http://127.0.0.1:8765/requester) to upload an image and data
request. The new `wf-*` listing appears in Marketplace. Open
[Player](http://127.0.0.1:8765/player), launch that listing, click **Record**,
harvest a ripe tomato into the basket, and click **Stop & submit**. The recording
and QA state appear under that listing and the player page. Only QA-approved
episodes can be purchased and downloaded through Data Miner.

Set `OPENROUTER_API_KEY` for requester interpretation and OpenRouter QA,
`CRUSOE_VLM_ENDPOINT`, `CRUSOE_VLM_MODEL`, and `CRUSOE_VLM_API_KEY` for the
Crusoe AI Player, and `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` for graph
indexing. `STRIPE_SECRET_KEY` enables Stripe **test-mode** Checkout; test
credits are not cash payouts. Without OpenRouter, the fixed task template is
used for requester interpretation, while submitted episodes cannot earn QA
approval from an unavailable model. Without a configured Crusoe VLM, AI Mode
is unavailable and human play remains available. Neo4j connection status is
separate from local recording storage.

For a Stripe billing demo, put an `sk_test_` key in the ignored `.env` as
`STRIPE_API_KEY` (or `STRIPE_SECRET_KEY`). On the requester site, open **Billing**,
choose a credit pack, complete Stripe Checkout with a test card, and return to
see the wallet balance. The server retrieves the Checkout Session and credits
the wallet only after verifying a paid test payment; the session ID prevents
double crediting on refresh. Set `WEFARM_PUBLIC_URL` to the site's HTTPS origin
when using a public deployment. This local demo confirms on browser return;
a production deployment also needs a Stripe webhook so payments are credited
if the browser never returns.

The OpenRouter QA code and Crusoe AI Player backend have local tests; the AI Mode
browser code typechecks and builds. One
synthetic OpenRouter QA request returned a valid evidence-based response, but
end-to-end game submission and Crusoe VLM play have not been verified. A
configured endpoint or key alone does not establish live inference. See the
[QA contract](openroture_QA.md) and [AI Player design](crusoe_player.md).

The older RoboCasa/HDF5 coffee-game code remains in the repository as a legacy
path and is not the WeFarm tomato-game data contract.

The teammate simulator controls, World Labs source, and asset credits are documented in [Simulator details](docs/simulator.md).
