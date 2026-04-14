"""Config-driven Streaming STT Comparison Demo

Reads config.json to determine which transcription boxes to show,
each with its own provider, environment, and API parameters.

Run:
  pip install -r requirements.txt
  ASSEMBLYAI_API_KEY=your-key python comparison_demo.py
"""

import json
import os
import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()

with open("config.json") as f:
    CONFIG = json.load(f)

KEYS = {
    "assemblyai": os.environ.get("ASSEMBLYAI_API_KEY", ""),
    "assemblyai_staging": os.environ.get("ASSEMBLYAI_STAGING_API_KEY", ""),
    "deepgram": os.environ.get("DEEPGRAM_API_KEY", ""),
}

AAI_ENVS = {
    "production": {
        "wss": "wss://streaming.assemblyai.com/v3/ws",
        "token_url": "https://streaming.assemblyai.com/v3/token",
        "key": KEYS["assemblyai"],
    },
    "staging": {
        "wss": "wss://streaming.sandbox000.assemblyai-labs.com/v3/ws",
        "token_url": "https://streaming.sandbox000.assemblyai-labs.com/v3/token",
        "key": KEYS["assemblyai_staging"] or KEYS["assemblyai"],
    },
}


def get_active_boxes():
    """Return only boxes whose provider API key is available."""
    active = []
    for box in CONFIG["boxes"]:
        provider = box["provider"]
        env = box.get("environment", "production")
        if provider == "assemblyai":
            if AAI_ENVS[env]["key"]:
                active.append(box)
        elif provider == "deepgram":
            if KEYS["deepgram"]:
                active.append(box)
        else:
            active.append(box)
    return active


async def create_temp_token(token_url: str, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            token_url,
            headers={"Authorization": api_key},
            params={"expires_in_seconds": 480},
        )
        resp.raise_for_status()
        return resp.json()["token"]


@app.get("/config")
async def get_config():
    boxes = get_active_boxes()

    # Collect unique AAI environments that need tokens
    needed_envs = set()
    for box in boxes:
        if box["provider"] == "assemblyai":
            needed_envs.add(box.get("environment", "production"))

    # Fetch tokens in parallel
    env_list = sorted(needed_envs)
    tokens = await asyncio.gather(
        *(create_temp_token(AAI_ENVS[env]["token_url"], AAI_ENVS[env]["key"]) for env in env_list)
    )
    aai_tokens = dict(zip(env_list, tokens))

    return {
        "sample_rate": CONFIG["sample_rate"],
        "boxes": [
            {
                **box,
                "wss_url": AAI_ENVS[box.get("environment", "production")]["wss"],
                "token": aai_tokens[box.get("environment", "production")],
            }
            if box["provider"] == "assemblyai"
            else {
                **box,
                "api_key": KEYS[box["provider"]],
            }
            for box in boxes
        ],
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

    active = get_active_boxes()
    if not active:
        print("Error: No boxes can be activated. Set at least one provider API key.")
        raise SystemExit(1)

    print(f"Active boxes: {', '.join(b['name'] for b in active)}")
    skipped = [b["name"] for b in CONFIG["boxes"] if b not in active]
    if skipped:
        print(f"Skipped (missing API key): {', '.join(skipped)}")

    print(f"Starting demo at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
