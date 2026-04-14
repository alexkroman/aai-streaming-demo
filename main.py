"""Config-driven Streaming STT Comparison Demo

Reads config.toml for box definitions and environment URLs.
Audio flows: browser -> backend WebSocket -> provider (streaming or batch).

Run:
  pip install -r requirements.txt
  cp .env.example .env  # fill in API keys
  python comparison.py
"""

import base64
import io
import json
import os
import tomllib
from typing import Any, Callable, Protocol
import wave
import asyncio
from urllib.parse import urlencode

from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
import websockets as ws_client

load_dotenv()

app = FastAPI()

with open("config.toml", "rb") as f:
    CONFIG = tomllib.load(f)

KEYS = {
    "assemblyai": os.environ.get("ASSEMBLYAI_API_KEY", ""),
    "assemblyai_staging": os.environ.get("ASSEMBLYAI_STAGING_API_KEY", ""),
    "deepgram": os.environ.get("DEEPGRAM_API_KEY", ""),
    "elevenlabs": os.environ.get("ELEVENLABS_API_KEY", ""),
}

ENVIRONMENTS = CONFIG.get("environments", {})
SAMPLE_RATE = CONFIG["sample_rate"]
BOXES = [{"name": name, **cfg} for name, cfg in CONFIG.get("boxes", {}).items()]


def get_key(provider: str, env_name: str) -> str:
    """Return the API key for a given provider/environment."""
    if provider == "deepgram":
        return KEYS["deepgram"]
    if provider == "elevenlabs":
        return KEYS["elevenlabs"]
    if env_name == "staging":
        return KEYS["assemblyai_staging"] or KEYS["assemblyai"]
    return KEYS["assemblyai"]


def get_active_boxes():
    active = []
    for box in BOXES:
        key = get_key(box["provider"], box.get("environment", "production"))
        if key:
            active.append(box)
    return active


def make_wav(pcm_data: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Streaming handlers — proxy audio to provider WebSocket, relay transcripts
# ---------------------------------------------------------------------------

class StreamingAAI:
    def __init__(self, box, index, send_fn):
        self.box = box
        self.index = index
        self.send = send_fn
        self.ws = None
        self._task = None

    async def connect(self):
        env = self.box.get("environment", "production")
        env_cfg = ENVIRONMENTS[env]
        key = get_key("assemblyai", env)

        print(f"[AAI box={self.index}] Fetching token from {env_cfg['token_url']}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                env_cfg["token_url"],
                headers={"Authorization": key},
                params={"expires_in_seconds": 480},
            )
            resp.raise_for_status()
            token = resp.json()["token"]

        params = {"sample_rate": str(SAMPLE_RATE), "token": token}
        for k, v in self.box.get("params", {}).items():
            params[k] = str(v)

        url = f"{env_cfg['wss']}?{urlencode(params)}"
        print(f"[AAI box={self.index}] Connecting to {url[:100]}...")
        self.ws = await ws_client.connect(url)
        print(f"[AAI box={self.index}] Connected")
        self._task = asyncio.create_task(self._recv())

    async def _recv(self):
        if not self.ws:
            return
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                print(f"[AAI box={self.index}] recv: {str(raw)[:200]}")
                if msg.get("type") == "SpeechStarted":
                    await self.send({"box": self.index, "type": "speech_started"})
                elif msg.get("type") == "Turn":
                    text = msg.get("transcript") or msg.get("text") or ""
                    if text:
                        await self.send({
                            "box": self.index,
                            "type": "final" if msg.get("end_of_turn") else "partial",
                            "text": text,
                            "turn_order": msg.get("turn_order"),
                        })
        except (ws_client.exceptions.ConnectionClosed, asyncio.CancelledError) as e:
            print(f"[AAI box={self.index}] recv ended: {e}")

    async def send_audio(self, data: bytes):
        if not self.ws:
            return
        try:
            await self.ws.send(data)
        except ws_client.exceptions.ConnectionClosed:
            pass

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class StreamingDG:
    def __init__(self, box, index, send_fn):
        self.box = box
        self.index = index
        self.send = send_fn
        self.ws = None
        self._task = None

    async def connect(self):
        env = self.box.get("environment", "deepgram")
        env_cfg = ENVIRONMENTS[env]
        key = get_key("deepgram", env)

        params = {"sample_rate": str(SAMPLE_RATE)}
        for k, v in self.box.get("params", {}).items():
            params[k] = str(v)

        url = f"{env_cfg['wss']}?{urlencode(params)}"
        self.ws = await ws_client.connect(
            url,
            additional_headers={"Authorization": f"Token {key}"},
        )
        self._task = asyncio.create_task(self._recv())

    async def _recv(self):
        if not self.ws:
            return
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "Results":
                    alts = msg.get("channel", {}).get("alternatives", [])
                    text = alts[0].get("transcript", "") if alts else ""
                    if text:
                        await self.send({
                            "box": self.index,
                            "type": "final" if msg.get("is_final") else "partial",
                            "text": text,
                        })
        except (ws_client.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    async def send_audio(self, data: bytes):
        if not self.ws:
            return
        try:
            await self.ws.send(data)
        except ws_client.exceptions.ConnectionClosed:
            pass

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class StreamingEL:
    def __init__(self, box, index, send_fn):
        self.box = box
        self.index = index
        self.send = send_fn
        self.ws = None
        self._task = None

    async def connect(self):
        env = self.box.get("environment", "elevenlabs")
        env_cfg = ENVIRONMENTS[env]
        key = get_key("elevenlabs", env)

        params = {}
        for k, v in self.box.get("params", {}).items():
            params[k] = str(v)

        url = f"{env_cfg['wss']}?{urlencode(params)}"
        self.ws = await ws_client.connect(
            url,
            additional_headers={"xi-api-key": key},
        )
        self._task = asyncio.create_task(self._recv())

    async def _recv(self):
        if not self.ws:
            return
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                msg_type = msg.get("message_type", "")
                if msg_type == "partial_transcript":
                    text = msg.get("text", "")
                    if text:
                        await self.send({
                            "box": self.index, "type": "partial", "text": text,
                        })
                elif msg_type in ("committed_transcript", "committed_transcript_with_timestamps"):
                    text = msg.get("text", "")
                    if text:
                        await self.send({
                            "box": self.index, "type": "final", "text": text,
                        })
        except (ws_client.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    async def send_audio(self, data: bytes):
        if not self.ws:
            return
        try:
            await self.ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(data).decode("ascii"),
            }))
        except ws_client.exceptions.ConnectionClosed:
            pass

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Batch handlers — collect audio, transcribe via REST API on stop
# ---------------------------------------------------------------------------

class BatchAAI:
    def __init__(self, box, index, send_fn):
        self.box = box
        self.index = index
        self.send = send_fn
        self.chunks: list[bytes] = []

    async def connect(self):
        await self.send({"box": self.index, "type": "info", "text": "Recording... will transcribe on stop."})

    async def send_audio(self, data: bytes):
        self.chunks.append(data)

    async def close(self):
        if not self.chunks:
            return
        await self.send({"box": self.index, "type": "info", "text": "Transcribing..."})

        wav_data = make_wav(b"".join(self.chunks))
        env = self.box.get("environment", "production")
        env_cfg = ENVIRONMENTS[env]
        key = get_key("assemblyai", env)
        headers = {"Authorization": key}

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                upload_resp = await client.post(
                    f"{env_cfg['api']}/v2/upload",
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    content=wav_data,
                )
                upload_resp.raise_for_status()
                upload_url = upload_resp.json()["upload_url"]

                body = {"audio_url": upload_url}
                for k, v in self.box.get("params", {}).items():
                    body[k] = v

                create_resp = await client.post(
                    f"{env_cfg['api']}/v2/transcript",
                    headers=headers,
                    json=body,
                )
                create_resp.raise_for_status()
                transcript_id = create_resp.json()["id"]

                while True:
                    poll_resp = await client.get(
                        f"{env_cfg['api']}/v2/transcript/{transcript_id}",
                        headers=headers,
                    )
                    poll_resp.raise_for_status()
                    result = poll_resp.json()
                    if result["status"] == "completed":
                        await self.send({
                            "box": self.index, "type": "final",
                            "text": result.get("text") or "(no speech detected)",
                        })
                        return
                    if result["status"] == "error":
                        await self.send({
                            "box": self.index, "type": "error",
                            "text": f"Error: {result.get('error', 'unknown')}",
                        })
                        return
                    await asyncio.sleep(1)
        except Exception as e:
            await self.send({"box": self.index, "type": "error", "text": str(e)})


class BatchDG:
    def __init__(self, box, index, send_fn):
        self.box = box
        self.index = index
        self.send = send_fn
        self.chunks: list[bytes] = []

    async def connect(self):
        await self.send({"box": self.index, "type": "info", "text": "Recording... will transcribe on stop."})

    async def send_audio(self, data: bytes):
        self.chunks.append(data)

    async def close(self):
        if not self.chunks:
            return
        await self.send({"box": self.index, "type": "info", "text": "Transcribing..."})

        wav_data = make_wav(b"".join(self.chunks))
        env = self.box.get("environment", "deepgram")
        env_cfg = ENVIRONMENTS[env]
        key = get_key("deepgram", env)

        params = {}
        for k, v in self.box.get("params", {}).items():
            if k == "interim_results":
                continue
            params[k] = str(v)

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{env_cfg['api']}/listen?{urlencode(params)}",
                    headers={
                        "Authorization": f"Token {key}",
                        "Content-Type": "audio/wav",
                    },
                    content=wav_data,
                )
                resp.raise_for_status()
                result = resp.json()
                text = result["results"]["channels"][0]["alternatives"][0]["transcript"]
                await self.send({
                    "box": self.index, "type": "final",
                    "text": text or "(no speech detected)",
                })
        except Exception as e:
            await self.send({"box": self.index, "type": "error", "text": str(e)})


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


class Handler(Protocol):
    def __init__(self, box: dict[str, Any], index: int, send_fn: Callable[..., Any]) -> None: ...
    async def connect(self) -> None: ...
    async def send_audio(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


HANDLERS: dict[tuple[str, str], type[Handler]] = {
    ("assemblyai", "streaming"): StreamingAAI,
    ("assemblyai", "batch"): BatchAAI,
    ("deepgram", "streaming"): StreamingDG,
    ("deepgram", "batch"): BatchDG,
    ("elevenlabs", "streaming"): StreamingEL,
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/config")
async def get_config():
    boxes = get_active_boxes()
    return {
        "sample_rate": SAMPLE_RATE,
        "boxes": [
            {
                "name": b["name"],
                "color": b.get("color", "#3b82f6"),
                "provider": b["provider"],
                "mode": b.get("mode", "streaming"),
            }
            for b in boxes
        ],
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    boxes = get_active_boxes()

    async def send_msg(obj):
        try:
            await websocket.send_text(json.dumps(obj))
        except Exception:
            pass

    handlers = []
    for i, box in enumerate(boxes):
        mode = box.get("mode", "streaming")
        cls = HANDLERS.get((box["provider"], mode))
        if cls:
            handlers.append(cls(box, i, send_msg))

    try:
        for h in handlers:
            await h.connect()
    except Exception as e:
        print(f"[WS] connect error: {e}")
        import traceback; traceback.print_exc()
        await send_msg({"type": "error", "text": str(e)})
        await websocket.close()
        return

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.receive":
                if "bytes" in msg:
                    await asyncio.gather(*(h.send_audio(msg["bytes"]) for h in handlers))
                elif "text" in msg:
                    data = json.loads(msg["text"])
                    if data.get("type") == "stop":
                        break
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass

    # Close all handlers — batch ones transcribe here
    await asyncio.gather(*(h.close() for h in handlers), return_exceptions=True)

    try:
        await websocket.send_text(json.dumps({"type": "done"}))
        await websocket.close()
    except Exception:
        pass


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Streaming STT Comparison Demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="localhost")
    args = parser.parse_args()

    active = get_active_boxes()
    if not active:
        print("Error: No boxes can be activated. Set at least one provider API key.")
        raise SystemExit(1)

    print(f"Active boxes: {', '.join(b['name'] for b in active)}")
    skipped = [b["name"] for b in BOXES if b not in active]
    if skipped:
        print(f"Skipped (missing API key): {', '.join(skipped)}")

    print(f"Starting demo at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
