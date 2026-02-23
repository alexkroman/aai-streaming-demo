"""Side-by-side Streaming STT Comparison Demo

Compares AssemblyAI u3-pro vs universal-streaming-english in real time.

Run:
  pip install -r requirements.txt
  ASSEMBLYAI_API_KEY=your-key python comparison_demo.py
"""

import os
import json
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import httpx

app = FastAPI()

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
AAI_WSS_BASE = "wss://streaming.assemblyai.com/v3/ws"
AAI_TOKEN_URL = "https://api.assemblyai.com/v2/realtime/token"
SAMPLE_RATE = 16000
MODELS = ["u3-pro", "universal-streaming-english"]


async def create_temp_token() -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            AAI_TOKEN_URL,
            headers={"Authorization": ASSEMBLYAI_API_KEY},
            json={"expires_in": 480},
        )
        resp.raise_for_status()
        return resp.json()["token"]


@app.get("/tokens")
async def get_tokens():
    tokens = await asyncio.gather(create_temp_token(), create_temp_token())
    return {
        "tokens": tokens,
        "models": MODELS,
        "sample_rate": SAMPLE_RATE,
        "wss_base": AAI_WSS_BASE,
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Streaming STT Model Comparison</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; }
  header { text-align: center; padding: 24px 16px 8px; }
  header h1 { font-size: 1.6rem; color: #fff; margin-bottom: 4px; }
  header p { font-size: 0.9rem; color: #888; }
  .controls { text-align: center; padding: 16px; }
  .controls button {
    padding: 12px 32px; font-size: 1rem; border: none; border-radius: 8px;
    cursor: pointer; font-weight: 600; transition: all 0.2s;
  }
  #startBtn { background: #3b82f6; color: #fff; }
  #startBtn:hover { background: #2563eb; }
  #startBtn.recording { background: #ef4444; }
  #startBtn.recording:hover { background: #dc2626; }
  #startBtn:disabled { background: #444; cursor: not-allowed; }
  .status { text-align: center; padding: 4px; font-size: 0.85rem; color: #888; }
  .columns {
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
    padding: 16px; max-width: 1400px; margin: 0 auto; height: calc(100vh - 200px);
  }
  .column {
    background: #1a1d27; border-radius: 12px; padding: 16px;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .column-header {
    display: flex; align-items: center; gap: 8px;
    padding-bottom: 12px; border-bottom: 1px solid #2a2d37; margin-bottom: 12px;
  }
  .model-badge {
    font-size: 0.75rem; font-weight: 700; padding: 4px 10px;
    border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .model-badge.u3-pro { background: #3b82f620; color: #60a5fa; border: 1px solid #3b82f640; }
  .model-badge.universal { background: #8b5cf620; color: #a78bfa; border: 1px solid #8b5cf640; }
  .column-header h2 { font-size: 1rem; font-weight: 600; }
  .transcript-area {
    flex: 1; overflow-y: auto; padding-right: 8px;
    scrollbar-width: thin; scrollbar-color: #333 transparent;
  }
  .turn {
    padding: 8px 12px; margin-bottom: 8px; border-radius: 8px;
    font-size: 0.95rem; line-height: 1.5; transition: all 0.2s;
  }
  .turn.partial { color: #888; background: #1f222e; }
  .turn.final { color: #e0e0e0; background: #252836; border-left: 3px solid #3b82f6; }
  .column:last-child .turn.final { border-left-color: #8b5cf6; }
  .turn.empty { color: #555; font-style: italic; }
  .latency-badge {
    font-size: 0.7rem; color: #666; float: right; margin-top: 2px;
  }
</style>
</head>
<body>

<header>
  <h1>Streaming STT Model Comparison</h1>
  <p>Compare AssemblyAI speech models in real time — same audio, side by side</p>
</header>

<div class="controls">
  <button id="startBtn">Start Recording</button>
</div>
<div class="status" id="status"></div>

<div class="columns">
  <div class="column" id="col-0">
    <div class="column-header">
      <span class="model-badge u3-pro">u3-pro</span>
      <h2>U3 Pro Streaming</h2>
    </div>
    <div class="transcript-area" id="transcript-0">
      <div class="turn empty">Waiting for audio...</div>
    </div>
  </div>
  <div class="column" id="col-1">
    <div class="column-header">
      <span class="model-badge universal">universal-streaming</span>
      <h2>Universal Streaming English</h2>
    </div>
    <div class="transcript-area" id="transcript-1">
      <div class="turn empty">Waiting for audio...</div>
    </div>
  </div>
</div>

<script>
const MODELS = ["u3-pro", "universal-streaming-english"];
const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");

let isRecording = false;
let sockets = [null, null];
let audioContext = null;
let workletNode = null;
let stream = null;

startBtn.addEventListener("click", async () => {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
});

async function startRecording() {
  startBtn.disabled = true;
  statusEl.textContent = "Connecting...";

  try {
    // Get temp token
    const tokenResp = await fetch("/tokens");
    const tokenData = await tokenResp.json();
    const tokens = tokenData.tokens;
    const sampleRate = tokenData.sample_rate;
    const wssBase = tokenData.wss_base;

    // Get microphone
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: sampleRate, channelCount: 1, echoCancellation: true, noiseSuppression: true }
    });

    audioContext = new AudioContext({ sampleRate: sampleRate });
    const source = audioContext.createMediaStreamSource(stream);

    // Load AudioWorklet for PCM extraction
    const workletCode = `
      class PCMProcessor extends AudioWorkletProcessor {
        process(inputs) {
          const input = inputs[0];
          if (input.length > 0) {
            const float32 = input[0];
            const int16 = new Int16Array(float32.length);
            for (let i = 0; i < float32.length; i++) {
              const s = Math.max(-1, Math.min(1, float32[i]));
              int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            this.port.postMessage(int16.buffer);
          }
          return true;
        }
      }
      registerProcessor('pcm-processor', PCMProcessor);
    `;
    const blob = new Blob([workletCode], { type: "application/javascript" });
    const workletUrl = URL.createObjectURL(blob);
    await audioContext.audioWorklet.addModule(workletUrl);
    workletNode = new AudioWorkletNode(audioContext, "pcm-processor");
    source.connect(workletNode);
    workletNode.connect(audioContext.destination);

    // Buffer audio and send every ~100ms
    let audioBuffers = [[], []];
    let sendIntervals = [];

    // Open WebSocket for each model
    for (let i = 0; i < MODELS.length; i++) {
      const url = `${wssBase}?sample_rate=${sampleRate}&speech_model=${MODELS[i]}&token=${tokens[i]}&format_turns=true`;
      const ws = new WebSocket(url);
      sockets[i] = ws;

      ws.onopen = () => {
        console.log(`Connected: ${MODELS[i]}`);
        if (sockets.every(s => s && s.readyState === WebSocket.OPEN)) {
          statusEl.textContent = "Recording — speak now";
          startBtn.textContent = "Stop Recording";
          startBtn.classList.add("recording");
          startBtn.disabled = false;
          isRecording = true;
        }
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(i, msg);
      };

      ws.onerror = (err) => {
        console.error(`WebSocket error (${MODELS[i]}):`, err);
        statusEl.textContent = `Error on ${MODELS[i]}`;
      };

      ws.onclose = () => {
        console.log(`Disconnected: ${MODELS[i]}`);
      };
    }

    // Send audio chunks to both sockets
    workletNode.port.onmessage = (event) => {
      const pcmBuffer = event.data;
      const base64 = arrayBufferToBase64(pcmBuffer);
      for (let i = 0; i < sockets.length; i++) {
        if (sockets[i] && sockets[i].readyState === WebSocket.OPEN) {
          sockets[i].send(JSON.stringify({ audio: base64 }));
        }
      }
    };

  } catch (err) {
    console.error("Error starting:", err);
    statusEl.textContent = "Error: " + err.message;
    startBtn.disabled = false;
  }
}

function stopRecording() {
  isRecording = false;
  startBtn.textContent = "Start Recording";
  startBtn.classList.remove("recording");
  statusEl.textContent = "Stopped";

  // Send terminate message and close sockets
  for (let i = 0; i < sockets.length; i++) {
    if (sockets[i] && sockets[i].readyState === WebSocket.OPEN) {
      sockets[i].send(JSON.stringify({ terminate_session: true }));
      sockets[i].close();
    }
    sockets[i] = null;
  }

  // Stop audio
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
}

// Track current partial turn per model
let currentTurnEl = [null, null];
let turnCounters = [0, 0];

function handleMessage(modelIdx, msg) {
  const area = document.getElementById(`transcript-${modelIdx}`);

  if (msg.type === "Begin") {
    // Clear the "waiting" placeholder
    area.innerHTML = "";
    return;
  }

  if (msg.type === "Turn") {
    const text = msg.transcript || msg.text || "";
    if (!text) return;

    const isEndOfTurn = msg.end_of_turn === true;
    const isFormatted = msg.turn_is_formatted === true;

    // Use formatted text once turn is complete and formatted
    if (isEndOfTurn || isFormatted) {
      // Finalize current turn
      if (currentTurnEl[modelIdx]) {
        currentTurnEl[modelIdx].className = "turn final";
        currentTurnEl[modelIdx].textContent = text;
      } else {
        const div = document.createElement("div");
        div.className = "turn final";
        div.textContent = text;
        area.appendChild(div);
      }
      currentTurnEl[modelIdx] = null;
      turnCounters[modelIdx]++;
      area.scrollTop = area.scrollHeight;
    } else {
      // Partial turn — update in place
      if (!currentTurnEl[modelIdx]) {
        const div = document.createElement("div");
        div.className = "turn partial";
        area.appendChild(div);
        currentTurnEl[modelIdx] = div;
      }
      currentTurnEl[modelIdx].textContent = text;
      area.scrollTop = area.scrollHeight;
    }
    return;
  }

  if (msg.type === "Termination") {
    console.log(`Session terminated: ${MODELS[modelIdx]}`);
    return;
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}
</script>
</body>
</html>
"""


@app.get("/")
async def index():
    return HTMLResponse(HTML_PAGE)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Streaming STT Comparison Demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="localhost")
    args = parser.parse_args()

    if not ASSEMBLYAI_API_KEY:
        print("Error: ASSEMBLYAI_API_KEY environment variable is required")
        raise SystemExit(1)

    print(f"Starting comparison demo at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
