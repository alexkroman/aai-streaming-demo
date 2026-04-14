const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");
const columnsEl = document.getElementById("columns");

let isRecording = false;
let sockets = [];
let audioContext = null;
let workletNode = null;
let stream = null;
let boxConfig = null;
const aaiCurrentTurn = {};

startBtn.addEventListener("click", async () => {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
});

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function buildColumns(boxes) {
  columnsEl.replaceChildren();
  columnsEl.style.gridTemplateColumns = boxes.map(() => "1fr").join(" ");
  for (let i = 0; i < boxes.length; i++) {
    const box = boxes[i];
    const color = box.color || "#3b82f6";

    const col = document.createElement("div");
    col.className = "column";
    col.id = `col-${i}`;

    const header = document.createElement("div");
    header.className = "column-header";

    const badge = document.createElement("span");
    badge.className = "model-badge";
    badge.style.background = hexToRgba(color, 0.12);
    badge.style.color = color;
    badge.style.border = `1px solid ${hexToRgba(color, 0.25)}`;
    badge.textContent = box.provider;

    const title = document.createElement("h2");
    title.textContent = box.name;

    header.appendChild(badge);
    header.appendChild(title);

    const area = document.createElement("div");
    area.className = "transcript-area";
    area.id = `transcript-${i}`;
    const waiting = document.createElement("div");
    waiting.className = "turn empty";
    waiting.textContent = "Waiting for audio...";
    area.appendChild(waiting);

    col.appendChild(header);
    col.appendChild(area);
    columnsEl.appendChild(col);
  }
}

function updatePartial(areaId, text) {
  const area = document.getElementById(areaId);
  let el = area.querySelector(".partial");
  if (!el) {
    el = document.createElement("div");
    el.className = "turn partial";
    area.appendChild(el);
  }
  el.textContent = text;
  area.scrollTop = area.scrollHeight;
}

function finalizePartial(areaId) {
  const area = document.getElementById(areaId);
  const el = area.querySelector(".partial");
  if (el) el.className = "turn final";
  area.scrollTop = area.scrollHeight;
}

function clearArea(areaId) {
  document.getElementById(areaId).replaceChildren();
}

function connectSocket(url, protocols, { areaId, onMessage }) {
  return new Promise((resolve, reject) => {
    const ws = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error(`WebSocket failed for ${areaId}`));
    ws.onmessage = (event) => onMessage(JSON.parse(event.data));
    sockets.push(ws);
  });
}

function parseAAIMessage(areaId, msg) {
  console.log(`[AAI ${areaId}]`, JSON.stringify(msg).slice(0, 300));
  if (msg.type === "SpeechStarted") {
    const area = document.getElementById(areaId);
    const el = document.createElement("div");
    el.className = "turn speech-started";
    el.textContent = "Speech detected";
    area.appendChild(el);
    area.scrollTop = area.scrollHeight;
    return;
  }
  if (msg.type === "Turn") {
    const text = msg.transcript || msg.text || "";
    if (!text) return;
    const turnOrder = msg.turn_order;
    if (aaiCurrentTurn[areaId] !== undefined && turnOrder !== aaiCurrentTurn[areaId]) {
      finalizePartial(areaId);
    }
    aaiCurrentTurn[areaId] = turnOrder;
    updatePartial(areaId, text);
    if (msg.end_of_turn) finalizePartial(areaId);
  }
}

function parseDGMessage(areaId, msg) {
  console.log(`[DG ${areaId}]`, JSON.stringify(msg).slice(0, 300));
  if (msg.type === "Results") {
    const transcript = msg.channel?.alternatives?.[0]?.transcript;
    if (!transcript) return;
    updatePartial(areaId, transcript);
    if (msg.is_final) finalizePartial(areaId);
  }
}

function buildWSUrl(base, params) {
  const qs = new URLSearchParams(params).toString();
  return `${base}?${qs}`;
}

function connectBox(box, index, sampleRate) {
  const areaId = `transcript-${index}`;
  const provider = box.provider;

  if (provider === "assemblyai") {
    const params = { sample_rate: sampleRate, ...box.params, token: box.token };
    const url = buildWSUrl(box.wss_url, params);
    return connectSocket(url, null, {
      areaId,
      onMessage: (msg) => parseAAIMessage(areaId, msg),
    });
  }

  if (provider === "deepgram") {
    const params = {
      encoding: "linear16",
      sample_rate: sampleRate,
      channels: 1,
      interim_results: "true",
      ...box.params,
    };
    const url = buildWSUrl("wss://api.deepgram.com/v1/listen", params);
    return connectSocket(url, ["token", box.api_key], {
      areaId,
      onMessage: (msg) => parseDGMessage(areaId, msg),
    });
  }

  return Promise.reject(new Error(`Unknown provider: ${provider}`));
}

async function startRecording() {
  startBtn.disabled = true;
  statusEl.textContent = "Connecting...";

  try {
    const resp = await fetch("/config");
    boxConfig = await resp.json();
    const { sample_rate: sampleRate, boxes } = boxConfig;

    buildColumns(boxes);

    stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate, channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    audioContext = new AudioContext({ sampleRate });
    const source = audioContext.createMediaStreamSource(stream);
    await audioContext.audioWorklet.addModule("/static/pcm-processor.js");
    workletNode = new AudioWorkletNode(audioContext, "pcm-processor");
    source.connect(workletNode);

    await Promise.all(boxes.map((box, i) => connectBox(box, i, sampleRate)));

    workletNode.port.onmessage = (event) => {
      for (const ws of sockets) {
        if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
      }
    };

    for (let i = 0; i < boxes.length; i++) {
      clearArea(`transcript-${i}`);
      delete aaiCurrentTurn[`transcript-${i}`];
    }

    statusEl.textContent = "Recording — speak now";
    startBtn.textContent = "Stop Recording";
    startBtn.classList.add("recording");
    startBtn.disabled = false;
    isRecording = true;
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
    startBtn.disabled = false;
  }
}

function stopRecording() {
  isRecording = false;
  startBtn.textContent = "Start Recording";
  startBtn.classList.remove("recording");
  statusEl.textContent = "Stopped";

  if (boxConfig) {
    for (let i = 0; i < boxConfig.boxes.length; i++) {
      finalizePartial(`transcript-${i}`);
    }
  }

  for (const ws of sockets) {
    if (ws.readyState === WebSocket.OPEN) ws.close();
  }
  sockets = [];

  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
}
