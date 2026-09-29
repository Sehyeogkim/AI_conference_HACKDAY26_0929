"""WeFarm web app: requester, marketplace, player, and simulator APIs."""
import argparse
import json
import os
import uuid
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .agents.payment import PRICING
from .credits import InsufficientCredits
from . import stripe_checkout
from .games import GameStore
from .pipeline import DELIVERY_FILES, OrderStore
from .sim.worker import default_worker
from .tomato_mujoco_marketplace import TOMATO_ID, TomatoMuJoCoMarketplace
from .wefarm_marketplace import DEFAULT_ROOT as WEFARM_DEFAULT_ROOT, MAX_RECORDING_BYTES, WeFarmMarketplace
from .crusoe import explain_qa
from .players.crusoe_vlm import CrusoeVLM, VLMError, MAX_REQUEST_BYTES, decode_request

STATIC = Path(__file__).parent / "static"
EXAMPLES = Path(__file__).parent.parent / "examples"
PAGES = {
    "/": ("login.html", "text/html"),
    "/requester": ("index.html", "text/html"),
    "/player": ("player.html", "text/html"),
    "/static/app.js": ("app.js", "application/javascript"),
    "/static/tomato-preview.jpg": ("tomato-preview.jpg", "image/jpeg"),
    "/static/example-tomato-farm.png": ("example-tomato-farm.png", "image/png"),
    "/static/demo/crete-farm-photo.jpg": ("demo/crete-farm-photo.jpg", "image/jpeg"),
    "/static/demo/crete-world-render.jpg": ("demo/crete-world-render.jpg", "image/jpeg"),
    "/static/demo/crete-world-panorama.jpg": ("demo/crete-world-panorama.jpg", "image/jpeg"),
    # A real, QA-passed recording from the prepared greenhouse, for demos before anyone has played.
    "/static/demo/sample-episode.jsonl.gz": ("demo/sample-episode.jsonl.gz", "application/gzip"),
}
PLAYER_ROUTE = re.compile(r"/api/players/([a-z0-9._-]{1,120})")
GAME_ROUTE = re.compile(r"/api/games/([0-9a-f]{12})(?:/(approve|close))?")
ORDER_ROUTE = re.compile(r"/api/orders/([0-9a-f-]{36})(?:/(answer|approve|purchase|close|reopen|retry|files/[\w.]+))?")
WEFARM_ROUTE = re.compile(r"/api/marketplace/(wf-[0-9a-f]{12})(?:/(purchase|mine|image|episodes/[a-zA-Z0-9._-]+/(?:file|replay)))?")
WEFARM_GAME_ROUTE = re.compile(r"/games/wefarm/(wf-[0-9a-f]{12})")
# Watch a recording: redirects to the simulator in replay mode, pointed at the stored file.
WATCH_EPISODE_ROUTE = re.compile(r"/watch/wefarm/(wf-[0-9a-f]{12})/([a-zA-Z0-9._-]+)")
WATCH_FREE_PLAY_ROUTE = re.compile(r"/watch/free-play/([0-9a-f-]{36})")
FREE_PLAY_FILE_ROUTE = re.compile(r"/api/wefarm/free-play/([0-9a-f-]{36})")
# The simulator runs on its own dev server; any loopback port may call the marketplace API.
FRIEND_ORIGIN = re.compile(r"http://(?:127\.0\.0\.1|localhost):\d{2,5}\Z")


class Handler(BaseHTTPRequestHandler):
    store: OrderStore
    games: GameStore
    tomato: TomatoMuJoCoMarketplace
    wefarm: WeFarmMarketplace
    demo = False

    def log_message(self, fmt, *args):
        if self.command != "GET" or not self.path.startswith("/api/"):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and (FRIEND_ORIGIN.fullmatch(origin) or origin == os.environ.get("WEFARM_ALLOWED_ORIGIN")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WeFarm-Game-Id, X-WeFarm-Player-Id, X-WeFarm-Session-Id")

    def do_OPTIONS(self):
        if urlsplit(self.path).path in {"/api/wefarm/episodes", "/api/wefarm/free-play", "/api/ai-player/decide"}:
            return self._send(204, b"", "text/plain")
        return self._json(404, {"error": "not found"})

    def _json(self, status: int, body) -> None:
        self._send(status, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _file(self, path: Path, ctype: str, download: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self._cors()
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _order(self):
        """(order, action) for /api/orders/<id>[/action]; order is False for an unknown id."""
        m = ORDER_ROUTE.fullmatch(self.path.split("?")[0])
        if not m:
            return None, None
        try:
            return self.store.get(m.group(1)), m.group(2)
        except KeyError:
            return False, None

    def _simulator_url(self, params: dict) -> str:
        game_url = os.environ.get("WEFARM_GAME_URL", "http://127.0.0.1:5180/")
        return game_url + ("&" if "?" in game_url else "?") + urlencode(params)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        host = self.headers.get("Host", f"127.0.0.1:{self.server.server_port}")
        watch = WATCH_EPISODE_ROUTE.fullmatch(path)
        if watch:
            game_id, episode_id = watch.groups()
            if self.wefarm.replay_path(game_id, episode_id) is None:
                return self._json(404, {"error": "unknown episode"})
            return self._redirect(self._simulator_url(
                {"replay": f"http://{host}/api/marketplace/{game_id}/episodes/{episode_id}/replay", "replay_label": episode_id}))
        if path == "/watch/sample":
            return self._redirect(self._simulator_url(
                {"replay": f"http://{host}/static/demo/sample-episode.jsonl.gz", "replay_label": "sample"}))
        watch = WATCH_FREE_PLAY_ROUTE.fullmatch(path)
        if watch:
            if self.wefarm.free_play_path(watch.group(1)) is None:
                return self._json(404, {"error": "unknown recording"})
            return self._redirect(self._simulator_url(
                {"replay": f"http://{host}/api/wefarm/free-play/{watch.group(1)}", "replay_label": "free play"}))
        if path == "/api/wefarm/free-play":
            return self._json(200, {"items": self.wefarm.free_play_sessions()})
        free_play = FREE_PLAY_FILE_ROUTE.fullmatch(path)
        if free_play:
            fp = self.wefarm.free_play_path(free_play.group(1))
            if fp is None:
                return self._json(404, {"error": "unknown recording"})
            return self._file(fp, "application/gzip", download=False)
        game_route = WEFARM_GAME_ROUTE.fullmatch(path)
        if game_route:
            game_id = game_route.group(1)
            try:
                self.wefarm.summary(game_id)
            except KeyError:
                return self._json(404, {"error": "unknown game"})
            original = parse_qs(parsed.query)
            game_url = os.environ.get("WEFARM_GAME_URL", "http://127.0.0.1:5180/")
            host = self.headers.get("Host", f"127.0.0.1:{self.server.server_port}")
            params = {"game_id": game_id, "submission_url": f"http://{host}/api/wefarm/episodes"}
            if "player_id" in original:
                params["player_id"] = original["player_id"][0]
            target = game_url + ("&" if "?" in game_url else "?") + urlencode(params)
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        if path == "/games/tomato":
            self.send_response(302)
            self.send_header("Location", "/requester")
            self.end_headers()
            return
        if path in PAGES:
            name, ctype = PAGES[path]
            return self._send(200, (STATIC / name).read_bytes(), f"{ctype}; charset=utf-8")
        if path == "/api/config":
            return self._json(200, {"demo": self.demo, "worker": self.store.worker.name, "pricing_note": PRICING["note"],
                                    "stripe": stripe_checkout.enabled(), "tomato_game": True,
                                    "qa_provider": "OpenRouter", "product": "WeFarm"})
        if path == "/api/ai-player/config":
            vlm = CrusoeVLM()
            return self._json(200, {"available": vlm.configured, "model": vlm.model or None,
                                    "reason": None if vlm.configured else "Crusoe VLM endpoint and model are not configured."})
        if path == "/api/wefarm/requests":
            return self._json(200, {"items": [self.wefarm.summary(r["game_id"]) for r in self.wefarm.requests]})
        if path == "/api/examples":
            return self._json(200, {p.stem: p.read_text() for p in sorted(EXAMPLES.glob("cafe*_order.txt"))})
        if path == "/api/orders":
            return self._json(200, {"items": [o.summary() for o in self.store.all()]})
        if path == "/api/marketplace":
            return self._json(200, {"items": [*self.wefarm.listings(), *self.store.marketplace()]})
        friend_market = WEFARM_ROUTE.fullmatch(path)
        if friend_market:
            game_id, action = friend_market.groups()
            try:
                if action is None:
                    return self._json(200, self.wefarm.summary(game_id))
                if action == "image":
                    image = self.wefarm.image(game_id)
                    if image is None:
                        return self._json(404, {"error": "no image"})
                    return self._file(image[0], image[1], download=False)
                if action.endswith("/replay"):
                    # Watching is a preview: any stored episode can be replayed; downloads stay purchase-gated.
                    fp = self.wefarm.replay_path(game_id, action.split("/")[1])
                    if fp is None:
                        return self._json(404, {"error": "unknown episode"})
                    return self._file(fp, "application/gzip", download=False)
                if action.startswith("episodes/"):
                    episode_id = action.split("/")[1]
                    fp = self.wefarm.file_path(game_id, episode_id)
                    if fp is None:
                        return self._json(403, {"error": "episode is missing, unpurchased, or not QA approved"})
                    return self._file(fp, "application/gzip", download=True)
                if action == "mine":
                    filters = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    for key in ("min_harvested", "min_quality", "seed"):
                        if key in filters:
                            filters[key] = int(filters[key])
                    if "max_duration_s" in filters:
                        filters["max_duration_s"] = float(filters["max_duration_s"])
                    return self._json(200, {"items": self.wefarm.mine(game_id, filters)})
            except (KeyError, ValueError) as err:
                return self._json(400, {"error": str(err)})
        if path == f"/api/marketplace/{TOMATO_ID}":
            return self._json(200, self.tomato.summary())
        file_match = re.fullmatch(rf"/api/marketplace/{TOMATO_ID}/episodes/([a-zA-Z0-9._-]+)/file", path)
        if file_match:
            fp = self.tomato.file_path(file_match.group(1))
            if fp is None:
                return self._json(403, {"error": "episode is missing, unpurchased, or not QA approved"})
            return self._file(fp, "application/x-hdf5", download=True)
        if path == f"/api/marketplace/{TOMATO_ID}/mine":
            request = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            for key in ("min_harvested", "min_quality", "seed"):
                if key in request:
                    try:
                        request[key] = int(request[key])
                    except ValueError:
                        return self._json(400, {"error": f"{key} must be an integer"})
            if "max_duration_s" in request:
                try:
                    request["max_duration_s"] = float(request["max_duration_s"])
                except ValueError:
                    return self._json(400, {"error": "max_duration_s must be a number"})
            try:
                return self._json(200, {"items": self.tomato.mine(request)})
            except ValueError as err:
                return self._json(400, {"error": str(err)})
        m = PLAYER_ROUTE.fullmatch(path)
        if m:
            result = self.store.player(m.group(1))
            result["episodes"] = sorted([*result["episodes"], *self.wefarm.player_episodes(m.group(1))],
                                        key=lambda e: -e["submitted_at"])
            return self._json(200, result)
        if path == "/api/wallet":
            return self._json(200, self.games.wallet())
        if path == "/api/games":
            return self._json(200, {"items": [g.summary() for g in self.games.all()],
                                    "game_server_url": os.environ.get("AGRIPHILO_GAME_SERVER_URL", "")})
        m = GAME_ROUTE.fullmatch(path)
        if m and not m.group(2):
            try:
                return self._json(200, self.games.get(m.group(1)).to_dict())
            except KeyError:
                return self._json(404, {"error": "unknown game"})
        order, action = self._order()
        if order is False:
            return self._json(404, {"error": "unknown order"})
        if order and not action:
            return self._json(200, order.to_dict())
        if order and action.startswith("files/"):
            name = action.split("/", 1)[1]
            fp = order.file_path(name)
            if not fp or not fp.exists():
                return self._json(403 if name in DELIVERY_FILES else 404, {"error": "file not available"})
            ctype = "image/png" if name.endswith(".png") else DELIVERY_FILES.get(name, "application/octet-stream")
            return self._file(fp, ctype, download=not name.endswith(".png"))
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"error": "invalid Content-Length"})
        if path == "/api/ai-player/decide":
            origin = self.headers.get("Origin", "")
            if origin and not (FRIEND_ORIGIN.fullmatch(origin) or origin == os.environ.get("WEFARM_ALLOWED_ORIGIN")):
                return self._json(403, {"error": "This game origin is not allowed."})
            if not 1 <= length <= MAX_REQUEST_BYTES:
                return self._json(413, {"error": "AI observation exceeds the size limit."})
            vlm = CrusoeVLM()
            if not vlm.configured:
                return self._json(503, {"error": "Crusoe VLM endpoint and model are not configured."})
            try:
                observation = json.loads(self.rfile.read(length))
                frame, status, frame_id = decode_request(observation)
                started = time.perf_counter()
                decision = vlm.decide(frame, status, frame_id)
            except (ValueError, VLMError) as exc:
                return self._json(422, {"error": str(exc)})
            return self._json(200, {"action": decision.action, "direction": decision.direction,
                                    "duration_s": decision.duration_s, "reason": decision.reason,
                                    "model": vlm.model, "latency_ms": round((time.perf_counter() - started) * 1000),
                                    "frame_id": frame_id})
        if path == "/api/wefarm/free-play":
            if not 1 <= length <= MAX_RECORDING_BYTES:
                return self._json(413, {"error": "recording must be between 1 byte and 20 MB"})
            try:
                record = self.wefarm.save_free_play(self.headers.get("X-WeFarm-Session-Id", ""), self.rfile.read(length))
            except ValueError as err:
                return self._json(400, {"error": str(err)})
            return self._json(201, {**record, "watch_url": f"http://{self.headers.get('Host', '127.0.0.1:8765')}/watch/free-play/{record['session_id']}"})
        if path == "/api/wefarm/episodes":
            if not 1 <= length <= MAX_RECORDING_BYTES:
                return self._json(413, {"error": "recording must be between 1 byte and 20 MB"})
            try:
                record = self.wefarm.submit_episode(self.headers.get("X-WeFarm-Game-Id", ""),
                                                    self.headers.get("X-WeFarm-Player-Id", ""),
                                                    self.headers.get("X-WeFarm-Session-Id", ""), self.rfile.read(length))
            except (ValueError, KeyError) as err:
                return self._json(400, {"error": str(err)})
            return self._json(201, {"episode_id": record["episode_id"], "qa": record["qa"],
                                    "reasons": record["reasons"], "harvested_count": record["harvested_count"]})
        if length > 9_000_000:
            return self._json(413, {"error": "request exceeds the 9 MB limit"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON body"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "expected a JSON object"})
        if path == "/api/wefarm/requests":
            try:
                result = self.wefarm.create_request(body.get("requester_id", "demo-requester"), body.get("request", ""),
                                                    body.get("image_name", "farm image"), body.get("image_data") or body.get("image_data_url", ""))
            except ValueError as err:
                return self._json(400, {"error": str(err)})
            return self._json(201, result)
        friend_market = WEFARM_ROUTE.fullmatch(path)
        if friend_market:
            game_id, action = friend_market.groups()
            try:
                if action == "purchase":
                    return self._json(200, self.wefarm.purchase(game_id, body.get("count")))
                if action == "mine":
                    return self._json(200, {"items": self.wefarm.mine(game_id, body.get("request", {}))})
            except (ValueError, KeyError, InsufficientCredits) as err:
                return self._json(409, {"error": str(err)})
        if self.path == f"/api/marketplace/{TOMATO_ID}/episodes":
            return self._json(410, {"error": "Submit the episode from the MuJoCo game server."})
        if self.path == f"/api/marketplace/{TOMATO_ID}/purchase":
            try:
                purchased = self.tomato.purchase(body.get("count"))
            except (ValueError, InsufficientCredits) as err:
                return self._json(409, {"error": str(err)})
            return self._json(200, purchased)
        if self.path == f"/api/marketplace/{TOMATO_ID}/mine":
            try:
                return self._json(200, {"items": self.tomato.mine(body.get("request", {}))})
            except ValueError as err:
                return self._json(400, {"error": str(err)})
        if self.path == "/api/wallet/checkout":
            try:
                credits = float(body.get("credits", 0))
                if not 1 <= credits <= 100000:
                    raise ValueError("credits must be between 1 and 100000")
                origin = f"http://{self.headers.get('Host', 'localhost:8000')}"
                s = stripe_checkout.create_topup_session(
                    self.games.wallet()["customer_id"], credits,
                    success_url=f"{origin}/requester?topup=success&session_id={{CHECKOUT_SESSION_ID}}",
                    cancel_url=f"{origin}/requester?topup=cancel")
            except (TypeError, ValueError) as err:
                return self._json(400, {"error": str(err)})
            except RuntimeError as err:
                return self._json(502, {"error": str(err)})
            return self._json(200, {"url": s["url"]})
        if self.path == "/api/wallet/confirm":
            try:
                s = stripe_checkout.retrieve_session(str(body.get("session_id", "")))
                meta = s.get("metadata") or {}
                customer = self.games.wallet()["customer_id"]
                if meta.get("kind") != "credit_topup" or meta.get("customer") != customer:
                    raise ValueError("not a credit top-up for this wallet")
                if s.get("payment_status") != "paid":
                    raise ValueError(f"Stripe reports payment_status={s.get('payment_status')}")
                credits = float(meta["credits"])
                if s.get("amount_total") != int(round(credits * 100)):
                    raise ValueError("paid amount does not match the credits")
                # keyed by the Checkout Session id, so a reload of the return page never credits twice
                e = self.games.ledger.topup(customer, credits, f"stripe:{s['id']}")
            except ValueError as err:
                return self._json(409, {"error": str(err)})
            except RuntimeError as err:
                return self._json(502, {"error": str(err)})
            return self._json(200, {"entry": e, "wallet": self.games.wallet()})
        if self.path == "/api/wallet/topup":
            try:
                amount = float(body.get("amount", 0))
                e = self.games.ledger.topup(self.games.wallet()["customer_id"], amount,
                                            body.get("idempotency_key") or f"test_evt_{uuid.uuid4().hex}")
            except (TypeError, ValueError) as err:
                return self._json(400, {"error": str(err)})
            return self._json(200, {"entry": e, "wallet": self.games.wallet()})
        if self.path == "/api/games":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "order text is empty"})
            return self._json(201, self.games.create(text).to_dict())
        m = GAME_ROUTE.fullmatch(self.path)
        if m and m.group(2):
            try:
                g = self.games.get(m.group(1))
            except KeyError:
                return self._json(404, {"error": "unknown game"})
            try:
                g.approve() if m.group(2) == "approve" else g.close()
            except (RuntimeError, InsufficientCredits) as err:
                return self._json(409, {"error": str(err)})
            return self._json(200, g.to_dict())
        if self.path == "/api/orders":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "order text is empty"})
            try:
                order = self.store.create(text, bool(body.get("auto")))
            except Exception as e:
                return self._json(502, {"error": f"could not start the Customer Agent: {e}"})
            return self._json(201, order.to_dict())
        order, action = self._order()
        if order is False:
            return self._json(404, {"error": "unknown order"})
        if not order or action not in ("answer", "approve", "purchase", "close", "reopen", "retry"):
            return self._json(404, {"error": "not found"})
        try:
            if action == "answer":
                order.answer(body.get("text", ""))
            elif action == "approve":
                order.approve()
            elif action == "retry":
                order.retry()
            elif action == "purchase":
                order.purchase(int(body.get("count", 0)))
            elif action == "reopen":
                order.reopen_listing()
            else:
                order.close_listing()
        except (RuntimeError, ValueError) as e:  # InsufficientCredits is a RuntimeError
            return self._json(409, {"error": str(e)})
        except Exception as e:
            return self._json(502, {"error": str(e)})
        self._json(200, order.to_dict())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--demo", action="store_true", help="scripted agents; no API key or credits")
    args = p.parse_args()
    from . import pipeline
    from .demo import DemoBrainbase
    # Keep the original order/Stripe flow; the marketplace scans MuJoCo episodes.
    pipeline.ROOT = Path("out/demo_orders")
    bb = DemoBrainbase()
    Handler.games = GameStore(bb)
    Handler.store = OrderStore(bb, default_worker(), ledger=Handler.games.ledger)
    Handler.tomato = TomatoMuJoCoMarketplace(Handler.games.ledger, explainer=lambda v:
        explain_qa(v, "pass" if v.get("pass") else "fail", v.get("reasons", [])))
    Handler.wefarm = WeFarmMarketplace(Handler.games.ledger, root=Path(os.environ.get("WEFARM_DATA_ROOT", str(WEFARM_DEFAULT_ROOT))))
    Handler.demo = True
    # Demo requester wallet starts with test credits (idempotent key: added once per ledger).
    Handler.games.ledger.topup(Handler.games.wallet()["customer_id"], 1000, "demo-starting-credits")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"WeFarm on http://localhost:{args.port} (teammate MuJoCo game, JSONL recordings, QA gated)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
