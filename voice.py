"""Rozmowa głosowa: nagrywanie z mikrofonu, miksowanie i odtwarzanie rozmówców.

Dźwięk to surowe PCM: 16 kHz, mono, 16 bitów, ramki po 20 ms. Ramki idą przez
serwer czatu (base64 w JSON), więc nie trzeba otwierać dodatkowych portów.
Wymaga biblioteki `sounddevice` (pip install sounddevice).
"""

import array
import base64
import collections
import queue
import threading

try:
    import sounddevice as sd
    IMPORT_ERROR = None
except Exception as e:  # brak biblioteki albo biblioteki PortAudio w systemie
    sd = None
    IMPORT_ERROR = e

RATE = 16000
FRAME = 320                 # próbek w ramce (20 ms)
FRAME_BYTES = FRAME * 2
PREBUFFER = 3               # ile ramek zebrać przed rozpoczęciem odtwarzania rozmówcy
MAX_BUFFER = 12             # powyżej tylu ramek opóźnienie rośnie, więc odrzucamy najstarsze
SILENCE = bytes(FRAME_BYTES)


def available():
    return sd is not None


def peak(frame):
    """Najgłośniejsza próbka w ramce (0–32767)."""
    samples = array.array("h")
    samples.frombytes(frame)
    return max((abs(v) for v in samples), default=0)


def mix(frames):
    """Sumuje ramki PCM 16-bit z obcięciem do zakresu próbek."""
    if not frames:
        return SILENCE
    if len(frames) == 1:
        return frames[0]
    acc = array.array("i", bytes(FRAME * 4))
    for frame in frames:
        samples = array.array("h")
        samples.frombytes(frame)
        for i, v in enumerate(samples):
            acc[i] += v
    return array.array("h", (32767 if v > 32767 else -32768 if v < -32768 else v for v in acc)).tobytes()


class JitterBuffer:
    """Bufory ramek od każdego rozmówcy, wyrównujące nierówne tempo sieci."""

    def __init__(self):
        self.lock = threading.Lock()
        self.buffers = {}   # nick -> deque ramek
        self.playing = set()

    def push(self, nick, frame):
        if len(frame) != FRAME_BYTES:
            return
        with self.lock:
            buf = self.buffers.setdefault(nick, collections.deque())
            buf.append(frame)
            if len(buf) > MAX_BUFFER:
                while len(buf) > PREBUFFER:
                    buf.popleft()

    def pop_mixed(self):
        frames = []
        with self.lock:
            for nick, buf in self.buffers.items():
                if nick not in self.playing:
                    if len(buf) < PREBUFFER:
                        continue
                    self.playing.add(nick)
                if buf:
                    frames.append(buf.popleft())
                else:
                    self.playing.discard(nick)  # bufor pusty: znowu czekamy na zapas
        return mix(frames)

    def remove(self, nick):
        with self.lock:
            self.buffers.pop(nick, None)
            self.playing.discard(nick)

    def clear(self):
        with self.lock:
            self.buffers.clear()
            self.playing.clear()


class VoiceEngine:
    """Łączy mikrofon i głośniki z połączeniem czatu.

    send_frame(bytes) wywoływane jest z osobnego wątku, nie z wątku audio,
    żeby wolna sieć nie powodowała trzasków.
    """

    def __init__(self, send_frame):
        self.send_frame = send_frame
        self.jitter = JitterBuffer()
        self.outgoing = queue.Queue(maxsize=50)
        self.muted = False
        self.mic_level = 0       # poziom ostatniej ramki z mikrofonu
        self.mic_max = 0         # najwyższy poziom od dołączenia (0 = mikrofon daje samą ciszę)
        self.stream = None
        self.running = False

    def start(self):
        if sd is None:
            raise RuntimeError(f"Brak biblioteki sounddevice: {IMPORT_ERROR}")
        self.running = True
        threading.Thread(target=self._sender, daemon=True).start()
        self.stream = sd.RawStream(samplerate=RATE, blocksize=FRAME, channels=1, dtype="int16",
                                   callback=self._callback)
        self.stream.start()

    def stop(self):
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.jitter.clear()
        try:
            self.outgoing.put_nowait(None)  # budzi wątek wysyłający
        except queue.Full:
            pass

    def receive(self, nick, data):
        """Ramka od innego rozmówcy (base64)."""
        try:
            frame = base64.b64decode(data)
        except ValueError:
            return
        self.jitter.push(nick, frame)

    def _callback(self, indata, outdata, frames, time_info, status):
        frame = bytes(indata)
        self.mic_level = peak(frame)
        self.mic_max = max(self.mic_max, self.mic_level)
        if not self.muted and frames == FRAME:
            try:
                self.outgoing.put_nowait(frame)
            except queue.Full:
                pass  # sieć nie nadąża: gubimy ramkę zamiast opóźniać rozmowę
        mixed = self.jitter.pop_mixed()
        if len(outdata) == len(mixed):
            outdata[:] = mixed
        else:
            outdata[:] = bytes(len(outdata))

    def _sender(self):
        while self.running:
            frame = self.outgoing.get()
            if frame is None or not self.running:
                break
            try:
                self.send_frame(base64.b64encode(frame).decode("ascii"))
            except OSError:
                break
