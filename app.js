const AAI_MODELS = ["u3-pro", "universal-streaming-english"];
const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");

let isRecording = false;
let aaiSockets = [null, null];
let dgSocket = null;
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
    // Get temp tokens
    console.log("[debug] Fetching tokens...");
    const tokenResp = await fetch("/tokens");
    const tokenData = await tokenResp.json();
    console.log("[debug] Token response:", JSON.stringify({...tokenData, tokens: tokenData.tokens.map(t => t.slice(0,10) + "..."), deepgram_key: tokenData.deepgram_key ? "***" : "missing"}));
    const tokens = tokenData.tokens;
    const sampleRate = tokenData.sample_rate;
    const wssBase = tokenData.wss_base;
    const deepgramKey = tokenData.deepgram_key;

    // Get microphone
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: sampleRate, channelCount: 1, echoCancellation: true, noiseSuppression: true }
    });

    audioContext = new AudioContext({ sampleRate: sampleRate });
    const source = audioContext.createMediaStreamSource(stream);

    // Load AudioWorklet for PCM extraction (buffer ~100ms chunks)
    const workletCode = `
      class PCMProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          this._buf = [];
          this._len = 0;
          this._target = 1600; // 100ms at 16kHz
        }
        process(inputs) {
          const ch = inputs[0] && inputs[0][0];
          if (!ch) return true;
          const n = ch.length;
          const pcm = new Int16Array(n);
          for (let i = 0; i < n; i++) {
            const s = Math.max(-1, Math.min(1, ch[i]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          this._buf.push(pcm);
          this._len += n;
          if (this._len >= this._target) {
            const out = new Int16Array(this._len);
            let off = 0;
            for (let j = 0; j < this._buf.length; j++) {
              out.set(this._buf[j], off);
              off += this._buf[j].length;
            }
            this.port.postMessage(out.buffer, [out.buffer]);
            this._buf = [];
            this._len = 0;
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

    // Track how many sockets have connected
    let connectedCount = 0;
    const totalSockets = 3; // 2 AAI + 1 Deepgram

    function checkAllConnected() {
      connectedCount++;
      if (connectedCount >= totalSockets) {
        statusEl.textContent = "Recording — speak now";
        startBtn.textContent = "Stop Recording";
        startBtn.classList.add("recording");
        startBtn.disabled = false;
        isRecording = true;
      }
    }

    // Open WebSocket for each AAI model
    for (let i = 0; i < AAI_MODELS.length; i++) {
      const url = `${wssBase}?sample_rate=${sampleRate}&speech_model=${AAI_MODELS[i]}&token=${tokens[i]}&format_turns=true`;
      console.log(`[debug] Connecting ${AAI_MODELS[i]}: ${url.replace(tokens[i], tokens[i].slice(0,10) + '...')}`);
      const ws = new WebSocket(url);
      aaiSockets[i] = ws;

      ws.onopen = () => {
        console.log(`[debug] Connected: ${AAI_MODELS[i]} (readyState=${ws.readyState})`);
        checkAllConnected();
      };

      ws.onmessage = (event) => {
        console.log(`[debug] Message from ${AAI_MODELS[i]}:`, event.data);
        const msg = JSON.parse(event.data);
        handleAAIMessage(i, msg);
      };

      ws.onerror = (err) => {
        console.error(`[debug] WebSocket error (${AAI_MODELS[i]}):`, err);
        statusEl.textContent = `Error on ${AAI_MODELS[i]}`;
      };

      ws.onclose = (event) => {
        console.log(`[debug] Disconnected: ${AAI_MODELS[i]} code=${event.code} reason="${event.reason}" wasClean=${event.wasClean}`);
      };
    }

    // Open WebSocket for Deepgram
    if (deepgramKey) {
      const dgUrl = `wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=${sampleRate}&channels=1&model=nova-3&punctuate=true&smart_format=true`;
      console.log(`[debug] Connecting Deepgram: ${dgUrl}`);
      const dgWs = new WebSocket(dgUrl, ["token", deepgramKey]);
      dgSocket = dgWs;

      dgWs.onopen = () => {
        console.log(`[debug] Connected: Deepgram (readyState=${dgWs.readyState})`);
        checkAllConnected();
      };

      dgWs.onmessage = (event) => {
        console.log(`[debug] Message from Deepgram:`, event.data);
        const msg = JSON.parse(event.data);
        handleDGMessage(msg);
      };

      dgWs.onerror = (err) => {
        console.error(`[debug] WebSocket error (Deepgram):`, err);
        statusEl.textContent = "Error on Deepgram";
      };

      dgWs.onclose = (event) => {
        console.log(`[debug] Disconnected: Deepgram code=${event.code} reason="${event.reason}" wasClean=${event.wasClean}`);
      };
    } else {
      console.warn("[debug] No Deepgram API key — skipping Deepgram");
      document.getElementById("transcript-2").innerHTML = '<div class="turn empty">No DEEPGRAM_API_KEY set</div>';
      checkAllConnected(); // count it as "connected" so recording still starts
    }

    // Send raw binary audio chunks to all sockets
    let audioChunkCount = 0;
    workletNode.port.onmessage = (event) => {
      const pcmBuffer = event.data;
      audioChunkCount++;
      if (audioChunkCount <= 3) {
        console.log(`[debug] Sending audio chunk #${audioChunkCount}, size=${pcmBuffer.byteLength} bytes`);
      }
      for (let i = 0; i < aaiSockets.length; i++) {
        if (aaiSockets[i] && aaiSockets[i].readyState === WebSocket.OPEN) {
          aaiSockets[i].send(pcmBuffer);
        }
      }
      if (dgSocket && dgSocket.readyState === WebSocket.OPEN) {
        dgSocket.send(pcmBuffer);
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

  // Close AAI sockets
  for (let i = 0; i < aaiSockets.length; i++) {
    if (aaiSockets[i] && aaiSockets[i].readyState === WebSocket.OPEN) {
      aaiSockets[i].send(JSON.stringify({ terminate_session: true }));
      aaiSockets[i].close();
    }
    aaiSockets[i] = null;
  }

  // Close Deepgram socket
  if (dgSocket && dgSocket.readyState === WebSocket.OPEN) {
    dgSocket.send(JSON.stringify({ type: "CloseStream" }));
    dgSocket.close();
  }
  dgSocket = null;

  // Stop audio
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
}

// --- AAI message handling ---
function handleAAIMessage(modelIdx, msg) {
  const area = document.getElementById(`transcript-${modelIdx}`);

  if (msg.type === "Begin") {
    area.innerHTML = "";
    return;
  }

  if (msg.type === "Turn") {
    const text = msg.transcript || msg.text || "";
    if (!text) return;

    const isFormatted = msg.turn_is_formatted === true;
    const isFinal = msg.end_of_turn === true || isFormatted;
    if (!isFinal) return;

    const div = document.createElement("div");
    div.className = "turn final";
    div.textContent = text;
    area.appendChild(div);
    area.scrollTop = area.scrollHeight;
    return;
  }

  if (msg.type === "Termination") {
    console.log(`Session terminated: ${AAI_MODELS[modelIdx]}`);
    return;
  }
}

// --- Deepgram message handling ---
let dgStarted = false;

function handleDGMessage(msg) {
  const area = document.getElementById("transcript-2");

  // Clear placeholder on first message
  if (!dgStarted) {
    area.innerHTML = "";
    dgStarted = true;
  }

  if (msg.type === "Results") {
    const transcript = msg.channel && msg.channel.alternatives && msg.channel.alternatives[0] && msg.channel.alternatives[0].transcript;
    if (!transcript) return;

    const div = document.createElement("div");
    div.className = "turn final";
    div.textContent = transcript;
    area.appendChild(div);
    area.scrollTop = area.scrollHeight;
  }
}
