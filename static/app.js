const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");
const columnsEl = document.getElementById("columns");

let isRecording = false;
let ws = null;
let audioContext = null;
let workletNode = null;
let stream = null;
let boxConfig = null;
const currentTurn = {};

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
    const modeLabel = box.mode === "batch" ? " batch" : "";

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
    badge.textContent = box.provider + modeLabel;

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
  if (!area) return;
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
  if (!area) return;
  const el = area.querySelector(".partial");
  if (el) el.className = "turn final";
  area.scrollTop = area.scrollHeight;
}

function showInfo(areaId, text) {
  const area = document.getElementById(areaId);
  if (!area) return;
  area.querySelectorAll(".info").forEach((el) => el.remove());
  const el = document.createElement("div");
  el.className = "turn info";
  el.textContent = text;
  area.appendChild(el);
  area.scrollTop = area.scrollHeight;
}

function clearArea(areaId) {
  const area = document.getElementById(areaId);
  if (area) area.replaceChildren();
}

function handleMessage(msg) {
  if (msg.type === "done") {
    statusEl.textContent = "Done";
    return;
  }
  if (msg.type === "error" && msg.box === undefined) {
    statusEl.textContent = "Error: " + msg.text;
    return;
  }

  const areaId = `transcript-${msg.box}`;

  if (msg.type === "speech_started") {
    const area = document.getElementById(areaId);
    if (!area) return;
    const el = document.createElement("div");
    el.className = "turn speech-started";
    el.textContent = "Speech detected";
    area.appendChild(el);
    area.scrollTop = area.scrollHeight;
  } else if (msg.type === "partial") {
    if (msg.turn_order !== undefined) {
      if (currentTurn[areaId] !== undefined && msg.turn_order !== currentTurn[areaId]) {
        finalizePartial(areaId);
      }
      currentTurn[areaId] = msg.turn_order;
    }
    updatePartial(areaId, msg.text);
  } else if (msg.type === "final") {
    if (msg.turn_order !== undefined) {
      if (currentTurn[areaId] !== undefined && msg.turn_order !== currentTurn[areaId]) {
        finalizePartial(areaId);
      }
      currentTurn[areaId] = msg.turn_order;
    }
    updatePartial(areaId, msg.text);
    finalizePartial(areaId);
  } else if (msg.type === "info" || msg.type === "error") {
    showInfo(areaId, msg.text);
  }
}

async function startRecording() {
  startBtn.disabled = true;
  statusEl.textContent = "Connecting...";

  try {
    const resp = await fetch("/config");
    boxConfig = await resp.json();
    const { sample_rate: sampleRate, boxes } = boxConfig;

    buildColumns(boxes);

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("WebSocket connection failed"));
    });

    ws.onmessage = (event) => handleMessage(JSON.parse(event.data));

    stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate, channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    audioContext = new AudioContext({ sampleRate });
    const source = audioContext.createMediaStreamSource(stream);
    await audioContext.audioWorklet.addModule("/static/pcm-processor.js");
    workletNode = new AudioWorkletNode(audioContext, "pcm-processor");
    source.connect(workletNode);

    workletNode.port.onmessage = (event) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(event.data);
      }
    };

    for (let i = 0; i < boxes.length; i++) {
      clearArea(`transcript-${i}`);
      delete currentTurn[`transcript-${i}`];
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

  if (boxConfig) {
    for (let i = 0; i < boxConfig.boxes.length; i++) {
      finalizePartial(`transcript-${i}`);
    }
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop" }));
    const hasBatch = boxConfig && boxConfig.boxes.some((b) => b.mode === "batch");
    statusEl.textContent = hasBatch ? "Processing batch transcriptions..." : "Stopped";
  }

  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
}
