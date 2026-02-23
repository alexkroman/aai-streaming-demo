# Voice Agent Demo

Real-time voice agent comparing AssemblyAI speech models side by side. Uses AssemblyAI streaming STT, Claude LLM, and Rime TTS, orchestrated by Pipecat.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set environment variables (or add to a `.env` file):

```
ASSEMBLYAI_API_KEY=your-key
ANTHROPIC_API_KEY=your-key
RIME_API_KEY=your-key
```

## Side-by-side comparison

Open two terminals and run:

**Terminal 1 — u3-pro (default model) on port 7860:**

```bash
python voice_agent.py --transport webrtc --port 7860 --model u3-pro
```

**Terminal 2 — universal-streaming-english on port 7861:**

```bash
python voice_agent.py --transport webrtc --port 7861 --model universal-streaming-english
```

Then open both in your browser side by side:

- http://localhost:7860
- http://localhost:7861

## Available models

| Flag value | Description |
|---|---|
| `u3-pro` | AssemblyAI U3 Pro (default) |
| `universal-streaming-english` | AssemblyAI Universal Streaming (English) |
| `universal-streaming-multilingual` | AssemblyAI Universal Streaming (Multilingual) |
