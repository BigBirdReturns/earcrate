from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from array import array
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
from threading import Thread

import pytest

from scripts.ace_step_v15_adapter import AdapterError, _splice_repaint_pcm, execute
from earcrate.generative_floor.cli import _load_json_argument


class _AceStepHandler(BaseHTTPRequestHandler):
    audio = b"RIFFtest-WAVE"
    release_payload: dict[str, object] | None = None
    query_payload: dict[str, object] | None = None
    release_body: bytes | None = None
    release_content_type: str | None = None

    def log_message(self, *_args: object) -> None:
        return

    def _send_json(self, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type") or ""
        if content_type.startswith("multipart/form-data"):
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
            )
            payload: dict[str, object] = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                value: object = part.get_payload(decode=True) if filename else part.get_content()
                if name in payload:
                    existing = payload[name]
                    payload[name] = existing + [value] if isinstance(existing, list) else [existing, value]
                else:
                    payload[name] = value
            type(self).release_body = body
            type(self).release_content_type = content_type
        else:
            payload = json.loads(body.decode("utf-8"))
        if self.path == "/release_task":
            type(self).release_payload = payload
            assert str(payload["seed"]) == "1729"
            assert payload["use_random_seed"] in {False, "false"}
            self._send_json({"data": {"task_id": "task-1"}, "code": 200, "error": None})
            return
        assert self.path == "/query_result"
        type(self).query_payload = payload
        result = json.dumps([{"file": "/v1/audio?path=opaque", "status": 1}])
        self._send_json(
            {"data": [{"task_id": "task-1", "status": 1, "result": result}], "code": 200, "error": None}
        )

    def do_GET(self) -> None:
        assert self.path.startswith("/v1/audio?")
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.audio)))
        self.end_headers()
        self.wfile.write(self.audio)


def test_adapter_materializes_async_audio(tmp_path: Path) -> None:
    _AceStepHandler.release_payload = None
    _AceStepHandler.query_payload = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AceStepHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request_path = tmp_path / "request.json"
        request_path.write_text(
            json.dumps(
                {
                    "seed": 1729,
                    "task_mode": "text_to_music",
                    "prompt": {"caption": "dry rock rhythm section", "audio_duration": 10},
                    "output_contract": {"duration_seconds": 10},
                }
            ),
            encoding="utf-8",
        )
        target = execute(
            request_path=request_path,
            output_directory=tmp_path,
            seed=1729,
            base_url=f"http://127.0.0.1:{server.server_port}",
            source_audio=None,
            timeout_seconds=10,
            poll_seconds=0.01,
        )
        assert target.read_bytes() == _AceStepHandler.audio
        assert _AceStepHandler.release_payload is not None
        assert _AceStepHandler.release_payload["task_type"] == "text2music"
        assert _AceStepHandler.query_payload == {"task_id_list": ["task-1"]}
        retained = json.loads((tmp_path / "provider-request.private.json").read_text(encoding="utf-8"))
        assert retained == _AceStepHandler.release_payload
        assert (tmp_path / "provider-submit-response.private.json").is_file()
        assert (tmp_path / "provider-terminal-response.private.json").is_file()
    finally:
        server.shutdown()
        server.server_close()


def test_adapter_refuses_non_loopback_service(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"seed": 1, "task_mode": "text_to_music", "prompt": {}}),
        encoding="utf-8",
    )
    with pytest.raises(AdapterError, match="loopback"):
        execute(
            request_path=request_path,
            output_directory=tmp_path,
            seed=1,
            base_url="https://example.com",
            source_audio=None,
            timeout_seconds=1,
            poll_seconds=0.01,
        )


def test_vocal_to_bgm_emits_exact_complete_contract(tmp_path: Path) -> None:
    _AceStepHandler.release_payload = None
    _AceStepHandler.query_payload = None
    _AceStepHandler.release_body = None
    _AceStepHandler.release_content_type = None
    source = tmp_path / "source-vocal.wav"
    source.write_bytes(b"RIFF-source-WAVE")
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "seed": 1729,
                "task_mode": "vocal_to_bgm",
                "prompt": {
                    "caption": "dry modern live rock backing band",
                    "lyrics": "[instrumental]",
                    "model": "acestep-v15-base",
                    "thinking": True,
                    "use_cot_caption": False,
                    "use_cot_language": False,
                    "lm_model_path": "acestep-5Hz-lm-1.7B",
                    "instruction": "Complete the input lead vocal with drums, bass, guitar, and organ:",
                    "track_classes": ["drums", "bass", "guitar", "keyboard"],
                    "audio_duration": 24.0,
                    "bpm": 114,
                    "key_scale": "C minor",
                    "time_signature": "4",
                },
                "output_contract": {"duration_seconds": 24.0},
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AceStepHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        target = execute(
            request_path=request_path,
            output_directory=tmp_path,
            seed=1729,
            base_url=f"http://127.0.0.1:{server.server_port}",
            source_audio=source,
            timeout_seconds=10,
            poll_seconds=0.01,
        )
        assert target.read_bytes() == _AceStepHandler.audio
        payload = _AceStepHandler.release_payload
        assert payload is not None
        assert payload["task_type"] == "complete"
        assert payload["model"] == "acestep-v15-base"
        assert payload["thinking"] == "true"
        assert payload["lm_model_path"] == "acestep-5Hz-lm-1.7B"
        assert payload["track_classes"] == ["drums", "bass", "guitar", "keyboard"]
        assert payload["instruction"].startswith("Complete the input lead vocal")
        assert payload["bpm"] == "114"
        assert payload["key_scale"] == "C minor"
        assert payload["time_signature"] == "4"
        assert payload["audio_duration"] == "24.0"
        assert payload["seed"] == "1729"
        assert payload["src_audio"] == source.read_bytes()
        retained = json.loads((tmp_path / "provider-request.private.json").read_text(encoding="utf-8"))
        assert retained["fields"]["task_type"] == "complete"
        assert retained["source_upload"]["sha256"] == __import__("hashlib").sha256(source.read_bytes()).hexdigest()
        wire = (tmp_path / "provider-request.private.multipart").read_bytes()
        assert retained["wire_body_sha256"] == __import__("hashlib").sha256(wire).hexdigest()
        assert wire == _AceStepHandler.release_body
    finally:
        server.shutdown()
        server.server_close()


def test_cli_json_argument_accepts_file_path(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.json"
    conditioning_path = tmp_path / "conditioning.json"
    prompt_path.write_text('{"caption":"modern live rock"}', encoding="utf-8")
    conditioning_path.write_text('[{"source_id":"fixture"}]', encoding="utf-8")
    assert _load_json_argument(str(prompt_path)) == {"caption": "modern live rock"}
    assert _load_json_argument(str(conditioning_path)) == [{"source_id": "fixture"}]
    assert _load_json_argument('[{"source_id":"fixture"}]') == [{"source_id": "fixture"}]


def test_repaint_splice_preserves_every_sample_outside_mask() -> None:
    source = array("f", [0.25] * 40)
    provider = array("f", [0.75] * 40)
    result = array("f")
    result.frombytes(
        _splice_repaint_pcm(
            source.tobytes(),
            provider.tobytes(),
            start_frame=4,
            end_frame=8,
            channels=2,
            crossfade_frames=1,
        )
    )
    assert result[: 4 * 2] == source[: 4 * 2]
    assert result[8 * 2 :] == source[8 * 2 :]
    assert result[5 * 2 : 7 * 2] == provider[5 * 2 : 7 * 2]
