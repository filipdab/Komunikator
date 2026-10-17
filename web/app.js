"use strict";

const APP_VERSION = "0.5";
const APP_AUTHOR = "Filip Dąbrowski";
const APP_EMAIL = "filip@dabrowski.in";
const APP_WEBSITE = "www.dabrowski.in";

const EMOJI = (
  "😀 😃 😄 😁 😆 😅 😂 🤣 😊 🙂 😉 😍 😘 😎 🤓 🤔 " +
  "😐 😏 😴 😮 😢 😭 😡 🤯 😱 🥳 🙃 😇 🤗 🤫 🙄 😬 " +
  "👍 👎 👏 🙌 🙏 💪 👋 ✌️ 👌 🤝 ❤️ 💔 🔥 ⭐ 🎉 🎂 " +
  "☕ 🍺 🍕 ⚽ 🎮 🎵 💻 📱 ✅ ❌ ❓ ❗ 💯 👀 🚀 🤖"
).split(" ");

// http(s)://… albo www.… ; końcowa interpunkcja nie należy do linku (jak w client.py)
const URL_RE = /(?:https?:\/\/|www\.)[^\s<>"]+[^\s<>".,;:!?)\]'”]/g;

const TYPING_SEND_EVERY = 2000;
const TYPING_IDLE = 3000;
const TYPING_EXPIRE = 6000;
const MIC_SILENT_WARN = 4000;
const NICK_KEY = "komunikator.nick";

const $ = (id) => document.getElementById(id);

let ws = null;
let myNick = "";
let joined = false;       // serwer przyjął nasz nick
let wantConnected = false; // użytkownik chce być połączony (ponawiamy po zerwaniu)
let retryDelay = 1000;
let retryTimer = null;
let typingUsers = new Map(); // nick -> czas ostatniego sygnału
let lastTypingSent = 0;
let typingIdleTimer = null;
let unread = 0;
let voice = null;         // stan rozmowy głosowej, gdy w niej jesteśmy

// ---------- połączenie ----------

function wsUrl() {
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connect() {
  clearTimeout(retryTimer);
  const sock = new WebSocket(wsUrl());
  ws = sock;
  sock.onopen = () => sock.send(JSON.stringify({ type: "join", nick: myNick }));
  sock.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (sock === ws) handle(msg);
  };
  sock.onclose = () => {
    if (sock !== ws) return;
    ws = null;
    const wasJoined = joined;
    joined = false;
    typingUsers.clear();
    renderTyping();
    if (voice) voice.remote = true; // po powrocie dołączymy ponownie
    if (!wantConnected) return;
    if (!wasJoined && !$("login").hidden) {
      wantConnected = false;
      $("login-status").textContent = "Nie udało się połączyć z serwerem.";
      $("connect-btn").disabled = false;
      return;
    }
    if (wasJoined) addSystem("Utracono połączenie z serwerem. Łączę ponownie…");
    setStatus("Łączę ponownie…");
    retryTimer = setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  };
}

function handle(msg) {
  switch (msg.type) {
    case "welcome":
      joined = true;
      retryDelay = 1000;
      myNick = msg.nick;
      showChat();
      setStatus("Połączono jako " + myNick);
      if (voice) {
        voice.remote = false;
        send({ type: "voice_join" });
      }
      break;
    case "error":
      if ($("login").hidden) {
        // błąd przy ponownym łączeniu (np. serwer nie zauważył jeszcze starego połączenia): próbujemy dalej
        addSystem(msg.text);
      } else {
        wantConnected = false;
        $("login-status").textContent = msg.text;
        $("connect-btn").disabled = false;
        ws && ws.close();
      }
      break;
    case "msg":
      typingUsers.delete(msg.nick);
      renderTyping();
      addMessage(msg.nick, msg.text, msg.time);
      if (document.hidden && msg.nick !== myNick) {
        unread++;
        document.title = `(${unread}) Komunikator`;
      }
      break;
    case "system":
      addSystem(msg.text, msg.time);
      break;
    case "users":
      renderUsers($("users-list"), msg.users);
      $("users-count").textContent = msg.users.length;
      break;
    case "voice_users":
      renderUsers($("voice-list"), msg.users);
      $("voice-count").textContent = msg.users.length ? `W rozmowie: ${msg.users.length}` : "";
      break;
    case "typing":
      if (msg.active) typingUsers.set(msg.nick, Date.now());
      else typingUsers.delete(msg.nick);
      renderTyping();
      break;
    case "audio":
      if (voice && voice.playback) playAudio(msg.nick, msg.data);
      break;
  }
}

// ---------- ekran logowania ----------

$("nick").value = localStorage.getItem(NICK_KEY) || "";

$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const nick = $("nick").value.trim();
  if (!nick) return;
  localStorage.setItem(NICK_KEY, nick);
  myNick = nick;
  wantConnected = true;
  retryDelay = 1000;
  $("connect-btn").disabled = true;
  $("login-status").textContent = "Łączenie…";
  connect();
});

function showChat() {
  $("login").hidden = true;
  $("chat").hidden = false;
  $("connect-btn").disabled = false;
  $("login-status").textContent = "";
  if (!matchMedia("(pointer: coarse)").matches) $("input").focus();
}

$("disconnect-btn").addEventListener("click", () => {
  wantConnected = false;
  clearTimeout(retryTimer);
  stopVoice(false);
  if (ws) ws.close();
  ws = null;
  joined = false;
  $("messages").replaceChildren();
  $("chat").hidden = true;
  $("login").hidden = false;
});

function setStatus(text) {
  $("conn-status").textContent = text;
}

// ---------- wiadomości ----------

function timeText(t) {
  const d = t ? new Date(t * 1000) : new Date();
  return d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

function appendLinked(parent, text) {
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const a = document.createElement("a");
    a.textContent = m[0];
    a.href = m[0].startsWith("www.") ? "https://" + m[0] : m[0];
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    parent.append(a);
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

function addLine(line) {
  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.append(line);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function addMessage(nick, text, t) {
  const line = document.createElement("div");
  line.className = "line";
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = timeText(t);
  const who = document.createElement("span");
  who.className = nick === myNick ? "nick me" : "nick";
  who.textContent = nick + ": ";
  line.append(time, who);
  appendLinked(line, text);
  addLine(line);
}

function addSystem(text, t) {
  const line = document.createElement("div");
  line.className = "line system";
  line.textContent = `${timeText(t)} ${text}`;
  addLine(line);
}

function renderUsers(list, users) {
  list.replaceChildren(...users.map((u) => {
    const li = document.createElement("li");
    li.textContent = u === myNick ? `${u} (Ty)` : u;
    return li;
  }));
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    unread = 0;
    document.title = "Komunikator";
  }
});

// ---------- pisanie ----------

function sendMessage() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || !joined) return;
  send({ type: "msg", text });
  input.value = "";
  $("emoji-panel").hidden = true;
  stopTyping();
}

$("send-form").addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage();
});

$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(pointer: coarse)").matches) {
    e.preventDefault();
    sendMessage();
  }
});

$("input").addEventListener("input", () => {
  if (!$("input").value.trim()) {
    stopTyping();
    return;
  }
  const now = Date.now();
  if (now - lastTypingSent > TYPING_SEND_EVERY) {
    lastTypingSent = now;
    send({ type: "typing", active: true });
  }
  clearTimeout(typingIdleTimer);
  typingIdleTimer = setTimeout(stopTyping, TYPING_IDLE);
});

function stopTyping() {
  clearTimeout(typingIdleTimer);
  if (lastTypingSent) send({ type: "typing", active: false });
  lastTypingSent = 0;
}

function renderTyping() {
  const nicks = [...typingUsers.keys()].filter((n) => n !== myNick);
  let text = "";
  if (nicks.length === 1) text = `${nicks[0]} pisze…`;
  else if (nicks.length > 1 && nicks.length <= 3) text = nicks.slice(0, -1).join(", ") + ` i ${nicks.at(-1)} piszą…`;
  else if (nicks.length > 3) text = "Kilka osób pisze…";
  $("typing").textContent = text;
}

setInterval(() => {
  const now = Date.now();
  let changed = false;
  for (const [nick, t] of typingUsers) {
    if (now - t > TYPING_EXPIRE) { typingUsers.delete(nick); changed = true; }
  }
  if (changed) renderTyping();
}, 1000);

// ---------- emotki, lista osób, o programie ----------

for (const emo of EMOJI) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = emo;
  b.addEventListener("click", () => {
    const input = $("input");
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.setRangeText(emo, start, end, "end");
    input.dispatchEvent(new Event("input"));
  });
  $("emoji-panel").append(b);
}

$("emoji-btn").addEventListener("click", () => {
  $("emoji-panel").hidden = !$("emoji-panel").hidden;
});

$("users-btn").addEventListener("click", () => $("users-panel").classList.toggle("open"));
$("messages").addEventListener("click", () => $("users-panel").classList.remove("open"));

$("about-version").textContent = `Wersja ${APP_VERSION}`;
$("about-author").textContent = `Autor: ${APP_AUTHOR}`;
$("about-email").textContent = APP_EMAIL;
$("about-email").href = `mailto:${APP_EMAIL}`;
$("about-web").textContent = APP_WEBSITE;
$("about-web").href = `https://${APP_WEBSITE}`;
for (const b of document.querySelectorAll(".about-btn")) {
  b.addEventListener("click", () => $("about").showModal());
}

// ---------- rozmowa głosowa ----------

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

function fromBase64(data) {
  const s = atob(data);
  const bytes = new Uint8Array(s.length & ~1);
  for (let i = 0; i < bytes.length; i++) bytes[i] = s.charCodeAt(i);
  return bytes.buffer;
}

function playAudio(nick, data) {
  let frame;
  try { frame = fromBase64(data); } catch { return; }
  voice.playback.port.postMessage({ nick, frame }, [frame]);
}

async function startVoice() {
  if (!window.isSecureContext || !navigator.mediaDevices || !window.AudioWorkletNode) {
    addSystem("Rozmowa głosowa w przeglądarce wymaga połączenia HTTPS (adres https://…). Zobacz README, rozdział o HTTPS.");
    return;
  }
  // AudioContext tworzymy od razu po kliknięciu: Safari na iPhonie pozwala włączyć dźwięk tylko wtedy
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const state = { ctx, stream: null, capture: null, playback: null, muted: false, remote: false, maxPeak: 0, level: 0, started: Date.now(), warned: false };
  voice = state;
  setVoiceUi(true);
  try {
    ctx.resume();
    await ctx.audioWorklet.addModule("voice-worklet.js");
    const playback = new AudioWorkletNode(ctx, "playback", { numberOfInputs: 0, outputChannelCount: [2] });
    playback.connect(ctx.destination);
    state.playback = playback;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    state.stream = stream;
    if (voice !== state) { // w międzyczasie kliknięto „Rozłącz”
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    const capture = new AudioWorkletNode(ctx, "capture", { numberOfOutputs: 0 });
    capture.port.onmessage = (e) => onMicFrame(state, e.data);
    ctx.createMediaStreamSource(stream).connect(capture);
    state.capture = capture;
    send({ type: "voice_join" });
  } catch (err) {
    console.error(err);
    const denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
    addSystem(denied
      ? "Brak dostępu do mikrofonu. Zezwól przeglądarce na użycie mikrofonu i spróbuj ponownie."
      : "Nie udało się włączyć dźwięku: " + (err && err.message ? err.message : err));
    stopVoice(false);
  }
}

function onMicFrame(state, { frame, peak }) {
  if (voice !== state) return;
  state.level = Math.max(peak, state.level * 0.7);
  if (peak > state.maxPeak) state.maxPeak = peak;
  $("mic-level").style.width = state.muted ? "0" : Math.min(100, Math.sqrt(state.level / 32767) * 100) + "%";
  if (!state.warned && state.maxPeak === 0 && Date.now() - state.started > MIC_SILENT_WARN) {
    state.warned = true;
    addSystem("Mikrofon nic nie nagrywa. Sprawdź, czy przeglądarka ma dostęp do właściwego mikrofonu.");
  }
  if (!state.muted && joined) send({ type: "audio", data: toBase64(frame) });
}

function stopVoice(notify = true) {
  if (!voice) return;
  const state = voice;
  voice = null;
  if (notify) send({ type: "voice_leave" });
  if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
  state.ctx.close().catch(() => {});
  setVoiceUi(false);
}

function setVoiceUi(active) {
  const btn = $("voice-btn");
  btn.textContent = active ? "📞 Rozłącz rozmowę" : "📞 Dołącz do rozmowy";
  btn.className = active ? "voice-leave" : "voice-join";
  $("mute-btn").hidden = !active;
  $("mic-meter").hidden = !active;
  $("mute-btn").textContent = "🎤 Wycisz";
  $("mic-level").style.width = "0";
}

$("voice-btn").addEventListener("click", () => {
  if (voice) stopVoice();
  else startVoice();
});

$("mute-btn").addEventListener("click", () => {
  if (!voice) return;
  voice.muted = !voice.muted;
  $("mute-btn").textContent = voice.muted ? "🔇 Włącz mikrofon" : "🎤 Wycisz";
});

if (matchMedia("(pointer: coarse)").matches) $("input").placeholder = "Wiadomość…";

if (!window.isSecureContext) {
  $("voice-btn").title = "Rozmowa głosowa wymaga HTTPS";
}
