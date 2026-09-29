import base64
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from agriphilo.players.crusoe_vlm import CrusoeVLM, VLMError, decode_request, validate_decision


FRAME = b"\xff\xd8\xff" + b"frame pixels" * 4 + b"\xff\xd9"


def test_request_accepts_bounded_jpeg_and_filters_status():
    frame, status, frame_id = decode_request({
        "frame_jpeg_base64": base64.b64encode(FRAME).decode(),
        "frame_id": 5,
        "status": {"task": "tomato harvest", "step": 10, "harvested": 1,
                   "gripper_open": True, "ignore_me": "injected instruction"},
    })
    assert frame == FRAME
    assert frame_id == 5
    assert status == {"task": "tomato harvest", "step": 10, "harvested": 1, "gripper_open": True}


@pytest.mark.parametrize("raw", [
    {"action": "run_code", "duration_s": 0.1},
    {"action": "move_arm", "direction": "teleport", "duration_s": 0.1},
    {"action": "move_base", "direction": "forward", "duration_s": 20},
    {"action": "gripper", "direction": "destroy", "duration_s": 0.1},
    {"action": "view", "direction": "hidden", "duration_s": 0.1},
])
def test_rejects_unsafe_actions(raw):
    with pytest.raises(VLMError):
        validate_decision(raw)


def test_model_sees_image_and_returns_only_bounded_action(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = __import__("json").loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"action":"move_arm","direction":"forward","duration_s":0.3,"reason":"tomato ahead"}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    vlm = CrusoeVLM(endpoint="https://private.example/v1/chat/completions", model="vision-model",
                    api_key="secret", client=client)
    choice = vlm.decide(FRAME, {"step": 0}, 2)
    assert choice.key == "w"
    assert choice.duration_s == 0.3
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_bad_model_output_fails_closed():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"action":"move_base","direction":"forward","duration_s":999}'}}]
    })))
    vlm = CrusoeVLM(endpoint="https://private.example/v1/chat/completions", model="vision-model", client=client)
    with pytest.raises(VLMError):
        vlm.decide(FRAME, {}, 1)


def test_browser_api_uses_server_side_model_and_cors(monkeypatch):
    from agriphilo.players.crusoe_vlm import Decision
    from agriphilo.web import Handler

    monkeypatch.setenv("CRUSOE_VLM_ENDPOINT", "https://private.example/v1/chat/completions")
    monkeypatch.setenv("CRUSOE_VLM_MODEL", "vision-model")
    monkeypatch.setattr(CrusoeVLM, "decide", lambda self, frame, status, frame_id:
                        Decision("move_arm", "forward", 0.2, "visible tomato"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client() as client:
            config = client.get(base + "/api/ai-player/config")
            assert config.json() == {"available": True, "model": "vision-model", "reason": None}
            result = client.post(base + "/api/ai-player/decide", headers={"Origin": "http://127.0.0.1:5180"},
                                 json={"frame_jpeg_base64": base64.b64encode(FRAME).decode(),
                                       "frame_id": 9, "status": {"step": 3}})
            assert result.status_code == 200
            assert result.json()["action"] == "move_arm"
            assert result.json()["frame_id"] == 9
            assert result.headers["access-control-allow-origin"] == "http://127.0.0.1:5180"
            denied = client.post(base + "/api/ai-player/decide", headers={"Origin": "https://other.example"},
                                 json={"frame_jpeg_base64": base64.b64encode(FRAME).decode(), "frame_id": 9})
            assert denied.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
