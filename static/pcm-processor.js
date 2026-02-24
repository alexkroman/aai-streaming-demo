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
