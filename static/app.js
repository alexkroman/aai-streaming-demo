const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");

let isRecording = false;
let sockets = [];
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

function appendTranscript(areaId, text) {
  const area = document.getElementById(areaId);
  const div = document.createElement("div");
  div.className = "turn final";
  div.textContent = text;
  area.appendChild(div);
  area.scrollTop = area.scrollHeight;
}

function clearArea(areaId) {
  document.getElementById(areaId).innerHTML = "";
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
  if (msg.type === "Turn") {
    const text = msg.transcript || msg.text || "";
    if (!text) return;
    if (!msg.end_of_turn && !msg.turn_is_formatted) return;
    appendTranscript(areaId, text);
  }
}

function parseDGMessage(areaId, msg) {
  if (msg.type === "Results") {
    const transcript = msg.channel?.alternatives?.[0]?.transcript;
    if (!transcript) return;
    appendTranscript(areaId, transcript);
  }
}

async function startRecording() {
  startBtn.disabled = true;
  statusEl.textContent = "Connecting...";

  try {
    const tokenResp = await fetch("/tokens");
    const { tokens, sample_rate: sampleRate, wss_base: wssBase, deepgram_key: deepgramKey } = await tokenResp.json();

    stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate, channelCount: 1, echoCancellation: true, noiseSuppression: true }
    });

    audioContext = new AudioContext({ sampleRate });
    const source = audioContext.createMediaStreamSource(stream);
    await audioContext.audioWorklet.addModule("/static/pcm-processor.js");
    workletNode = new AudioWorkletNode(audioContext, "pcm-processor");
    source.connect(workletNode);

    const connections = [
      connectSocket(
        `${wssBase}?sample_rate=${sampleRate}&speech_model=u3-pro&token=${tokens[0]}&format_turns=true`,
        null,
        { areaId: "transcript-0", onMessage: (msg) => parseAAIMessage("transcript-0", msg) }
      ),
      connectSocket(
        `${wssBase}?sample_rate=${sampleRate}&speech_model=universal-streaming-english&token=${tokens[1]}&format_turns=true`,
        null,
        { areaId: "transcript-1", onMessage: (msg) => parseAAIMessage("transcript-1", msg) }
      ),
    ];

    connections.push(connectSocket(
      `wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=${sampleRate}&channels=1&model=nova-3&punctuate=true&smart_format=true`,
      ["token", deepgramKey],
      { areaId: "transcript-2", onMessage: (msg) => parseDGMessage("transcript-2", msg) }
    ));

    await Promise.all(connections);

    workletNode.port.onmessage = (event) => {
      for (const ws of sockets) {
        if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
      }
    };

    clearArea("transcript-0");
    clearArea("transcript-1");
    clearArea("transcript-2");
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

  for (const ws of sockets) {
    if (ws.readyState === WebSocket.OPEN) ws.close();
  }
  sockets = [];

  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
}
