// Mic capture with live level (for the animation), silence auto-stop, live captions, and speech output.
// Audio goes to the backend's Saaras STT when available; the browser recogniser is the fallback and the live caption.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const LOCALE = { en: 'en-IN', hi: 'hi-IN', hinglish: 'en-IN' };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Calibration knobs: mic level (0..1) that counts as speech, and how long a pause ends the turn.
const SPEECH_LEVEL = 0.08;
const END_SILENCE_MS = 1600;
const NO_SPEECH_GIVE_UP_MS = 8000;
const MAX_TURN_MS = 30000;
export const BANDS = 7;

export async function startRecording({ lang, onLevel, onInterim, onSilence }) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.75;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const wave = new Uint8Array(analyser.fftSize);
  const freq = new Uint8Array(analyser.frequencyBinCount);

  const chunks = [];
  const rec = new MediaRecorder(stream);
  rec.ondataavailable = e => e.data.size && chunks.push(e.data);
  rec.start();

  let finalText = '', interim = '', sr = null, srEnded = Promise.resolve();
  if (SR) {
    sr = new SR();
    sr.lang = LOCALE[lang] || 'en-IN';
    sr.continuous = true;
    sr.interimResults = true;
    srEnded = new Promise(r => { sr.onend = r; });
    sr.onresult = e => {
      interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript + ' ';
        else interim += e.results[i][0].transcript;
      }
      onInterim((finalText + interim).trim());
    };
    sr.onerror = () => {};
    try { sr.start(); } catch { sr = null; srEnded = Promise.resolve(); }
  }

  const t0 = performance.now();
  let last = t0, voicedMs = 0, lastLoud = t0, raf = 0;
  const tick = now => {
    analyser.getByteTimeDomainData(wave);
    let sum = 0;
    for (const v of wave) sum += ((v - 128) / 128) ** 2;
    const level = Math.min(1, Math.sqrt(sum / wave.length) * 5);
    analyser.getByteFrequencyData(freq);
    const bands = Array.from({ length: BANDS }, (_, i) => {
      let s = 0;
      for (let j = 2 + i * 8; j < 10 + i * 8; j++) s += freq[j];
      return Math.min(1, s / 8 / 200);
    });
    onLevel(level, bands);

    if (level > SPEECH_LEVEL) { voicedMs += now - last; lastLoud = now; }
    last = now;
    const heard = voicedMs > 300;
    if ((heard && now - lastLoud > END_SILENCE_MS) || (!heard && now - t0 > NO_SPEECH_GIVE_UP_MS) || now - t0 > MAX_TURN_MS) {
      onSilence();
      return;
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  const release = () => {
    cancelAnimationFrame(raf);
    stream.getTracks().forEach(tr => tr.stop());
    ctx.close();
  };

  return {
    async stop() {
      cancelAnimationFrame(raf);
      const recStopped = rec.state === 'inactive' ? Promise.resolve() : new Promise(r => { rec.onstop = r; rec.stop(); });
      sr?.stop();
      await Promise.all([recStopped, Promise.race([srEnded, sleep(1500)])]);
      release();
      return { blob: new Blob(chunks, { type: rec.mimeType }), transcript: (finalText + interim).trim() };
    },
    cancel() {
      if (rec.state !== 'inactive') rec.stop();
      sr?.abort();
      release();
    },
  };
}

// ---- speech output: backend Bulbul audio when available, otherwise the browser's voices

let voices = [];
const loadVoices = () => { voices = window.speechSynthesis?.getVoices() || []; };
loadVoices();
window.speechSynthesis?.addEventListener?.('voiceschanged', loadVoices);

let player = null, endCurrent = null;

export async function speak(text, lang, fetchAudio) {
  stopSpeaking();
  const blob = fetchAudio ? await fetchAudio(text, lang) : null;
  return new Promise(resolve => {
    endCurrent = resolve;
    if (blob) {
      player = new Audio(URL.createObjectURL(blob));
      player.onended = player.onerror = () => resolve();
      player.play().catch(() => resolve());
      return;
    }
    if (!window.speechSynthesis) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === 'en' ? 'en-IN' : 'hi-IN';
    u.voice = voices.find(v => v.lang === u.lang) || voices.find(v => v.lang.startsWith(u.lang.slice(0, 2))) || null;
    u.rate = 0.95;
    u.onend = u.onerror = () => resolve();
    speechSynthesis.speak(u);
  });
}

export function stopSpeaking() {
  player?.pause();
  player = null;
  window.speechSynthesis?.cancel();
  endCurrent?.();
  endCurrent = null;
}
