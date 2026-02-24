"""Side-by-side Streaming STT Comparison Demo

Compares AssemblyAI u3-pro vs universal-streaming-english vs Deepgram in real time.

Run:
  pip install -r requirements.txt
  ASSEMBLYAI_API_KEY=your-key DEEPGRAM_API_KEY=your-key python comparison_demo.py
"""

import os
import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
import httpx

app = FastAPI()

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
AAI_WSS_BASE = "wss://streaming.assemblyai.com/v3/ws"
AAI_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"
SAMPLE_RATE = 16000
MODELS = ["u3-pro", "universal-streaming-english"]


async def create_temp_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        print(f"[server] Requesting token from {AAI_TOKEN_URL}...")
        try:
            resp = await client.get(
                AAI_TOKEN_URL,
                headers={"Authorization": ASSEMBLYAI_API_KEY},
                params={"expires_in_seconds": 480},
            )
            print(f"[server] Token response: status={resp.status_code}")
            if resp.status_code != 200:
                print(f"[server] Token request failed: body={resp.text}")
            resp.raise_for_status()
            token = resp.json()["token"]
            print(f"[server] Token obtained: {token[:15]}...")
            return token
        except Exception as e:
            print(f"[server] Token request error: {type(e).__name__}: {e}")
            raise


@app.get("/tokens")
async def get_tokens():
    print("[server] Creating 2 temporary tokens...")
    try:
        tokens = await asyncio.gather(create_temp_token(), create_temp_token())
        print(f"[server] Tokens created: {tokens[0][:20]}..., {tokens[1][:20]}...")
    except Exception as e:
        print(f"[server] Token creation FAILED: {e}")
        raise
    return {
        "tokens": tokens,
        "models": MODELS,
        "sample_rate": SAMPLE_RATE,
        "wss_base": AAI_WSS_BASE,
        "deepgram_key": DEEPGRAM_API_KEY,
    }


@app.get("/")
async def index():
    return FileResponse("index.html")


@app.get("/app.js")
async def app_js():
    return FileResponse("app.js", media_type="application/javascript")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Streaming STT Comparison Demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="localhost")
    args = parser.parse_args()

    if not ASSEMBLYAI_API_KEY:
        print("Error: ASSEMBLYAI_API_KEY environment variable is required")
        raise SystemExit(1)

    if not DEEPGRAM_API_KEY:
        print("Warning: DEEPGRAM_API_KEY not set — Deepgram pane will be disabled")

    print(f"[server] AAI key loaded: {ASSEMBLYAI_API_KEY[:8]}...{ASSEMBLYAI_API_KEY[-4:]}")
    if DEEPGRAM_API_KEY:
        print(f"[server] Deepgram key loaded: {DEEPGRAM_API_KEY[:8]}...{DEEPGRAM_API_KEY[-4:]}")
    print(f"Starting comparison demo at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
