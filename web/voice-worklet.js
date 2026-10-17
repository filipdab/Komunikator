// Procesory dźwięku działające w osobnym wątku audio przeglądarki.
// Format sieciowy taki sam jak w voice.py: PCM 16 kHz, mono, 16 bit, ramki po 320 próbek (20 ms).

const NET_RATE = 16000;
const FRAME = 320;
const PREBUFFER = 3;   // ile ramek zebrać, zanim zacznie się odtwarzanie danej osoby
const MAX_BUFFER = 12; // przy większym zapasie najstarsze ramki są wyrzucane (mniejsze opóźnienie)

// Mikrofon: zmiana częstotliwości próbkowania na 16 kHz i pocięcie na ramki.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / NET_RATE;
    this.t = 0;
    this.lp = 0;
    // filtr dolnoprzepustowy ok. 6 kHz przed zmniejszeniem częstotliwości
    this.alpha = 1 - Math.exp(-2 * Math.PI * 6000 / sampleRate);
    this.frame = new Int16Array(FRAME);
    this.n = 0;
    this.peak = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.lp += this.alpha * (input[i] - this.lp);
      this.t += 1;
      if (this.t >= this.ratio) {
        this.t -= this.ratio;
        const s = Math.max(-1, Math.min(1, this.lp));
        const v = Math.round(s * 32767);
        this.frame[this.n++] = v;
        const a = v < 0 ? -v : v;
        if (a > this.peak) this.peak = a;
        if (this.n === FRAME) {
          this.port.postMessage({ frame: this.frame.buffer, peak: this.peak }, [this.frame.buffer]);
          this.frame = new Int16Array(FRAME);
          this.n = 0;
          this.peak = 0;
        }
      }
    }
    return true;
  }
}

// Głośnik: osobny bufor dla każdej osoby, miksowanie i zmiana częstotliwości na częstotliwość karty.
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = NET_RATE / sampleRate;
    this.streams = new Map(); // nick -> { frames, cur, pos, playing }
    this.port.onmessage = (e) => {
      const { nick, frame } = e.data;
      let s = this.streams.get(nick);
      if (!s) {
        s = { frames: [], cur: null, pos: 0, playing: false };
        this.streams.set(nick, s);
      }
      s.frames.push(new Int16Array(frame));
      if (s.frames.length > MAX_BUFFER) s.frames.splice(0, s.frames.length - MAX_BUFFER);
      if (!s.playing && s.frames.length >= PREBUFFER) s.playing = true;
    };
  }

  process(inputs, outputs) {
    const out = outputs[0][0];
    for (const [nick, s] of this.streams) {
      if (!s.playing) continue;
      for (let i = 0; i < out.length; i++) {
        if (!s.cur) {
          s.cur = s.frames.shift() || null;
          if (!s.cur) { // skończył się zapas: czekamy na nowe ramki
            s.playing = false;
            break;
          }
        }
        const idx = Math.floor(s.pos);
        const frac = s.pos - idx;
        const a = s.cur[idx];
        const b = idx + 1 < s.cur.length ? s.cur[idx + 1] : (s.frames.length ? s.frames[0][0] : a);
        out[i] += (a + (b - a) * frac) / 32768;
        s.pos += this.step;
        if (s.pos >= s.cur.length) {
          s.pos -= s.cur.length;
          s.cur = null;
        }
      }
      if (!s.playing && !s.frames.length) this.streams.delete(nick);
    }
    for (let i = 0; i < out.length; i++) {
      if (out[i] > 1) out[i] = 1;
      else if (out[i] < -1) out[i] = -1;
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}

registerProcessor("capture", CaptureProcessor);
registerProcessor("playback", PlaybackProcessor);
