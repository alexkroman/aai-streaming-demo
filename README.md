# Streaming STT Model Comparison

Real-time side-by-side comparison of three streaming speech-to-text models using the same microphone input:

- **AssemblyAI U3 Pro** — AssemblyAI's latest streaming model
- **AssemblyAI Universal Streaming** — AssemblyAI's general-purpose streaming model
- **Deepgram Nova-3** — Deepgram's latest streaming model

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
ASSEMBLYAI_API_KEY=your-key DEEPGRAM_API_KEY=your-key python comparison_demo.py
```

Then open http://localhost:8000 in your browser and click **Start Recording**.

### Options

```
--host HOST    Server host (default: localhost)
--port PORT    Server port (default: 8000)
```

## How it works

1. The browser captures microphone audio and converts it to 16kHz PCM16 via an AudioWorklet
2. Audio chunks are sent simultaneously to all three provider WebSockets
3. Each pane displays partial (interim) and final transcription results independently

The Deepgram API key is optional. If `DEEPGRAM_API_KEY` is not set, the third pane will show a disabled message and the other two models will still work.
