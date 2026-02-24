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
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
AAI_WSS_BASE = "wss://streaming.assemblyai.com/v3/ws"
AAI_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"
SAMPLE_RATE = 16000


async def create_temp_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            AAI_TOKEN_URL,
            headers={"Authorization": ASSEMBLYAI_API_KEY},
            params={"expires_in_seconds": 480},
        )
        resp.raise_for_status()
        return resp.json()["token"]


@app.get("/tokens")
async def get_tokens():
    tokens = await asyncio.gather(create_temp_token(), create_temp_token())
    return {
        "tokens": tokens,
        "sample_rate": SAMPLE_RATE,
        "wss_base": AAI_WSS_BASE,
        "deepgram_key": DEEPGRAM_API_KEY,
    }


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

    if not ASSEMBLYAI_API_KEY:
        print("Error: ASSEMBLYAI_API_KEY environment variable is required")
        raise SystemExit(1)

    if not DEEPGRAM_API_KEY:
        print("Error: DEEPGRAM_API_KEY environment variable is required")
        raise SystemExit(1)

    print(f"Starting comparison demo at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
