import * as api from './api.js';
import { startRecording, speak, stopSpeaking, BANDS } from './voice.js';
import { LANGS, t, careText, urgencyText, detectLang, chipsFor, slotLabel } from './i18n.js';
import { LOCALITIES, nearestLocality } from './geo.js';
import { icon } from './icons.js';

const $ = sel => document.querySelector(sel);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = {
  get: k => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const EXAMPLES = [
  'Papa ko do din se bukhar hai aur khansi bhi',
  'I need a nurse to change a wound dressing at home',
  'मेरी माँ के घुटनों में 3 महीने से दर्द है',
];
const CARE_ICON = { paramedic_home_visit: 'homePlus', nurse: 'syringe', gp_teleconsult: 'video', specialist: 'stethoscope', emergency: 'alert' };

const state = {
  lang: LANGS.some(l => l.code === store.get('cc.lang')) ? store.get('cc.lang') : 'en',
  location: null,     // {lat, lng, name?, source: 'gps' | 'manual' | 'default'}
  history: [],        // [{role, content}] sent as conversation_history
  questions: 0,
  step: 0,
  busy: false,
  booked: false,
  usedVoice: false,
  convo: 0,           // bumps on restart so late responses from an old conversation are dropped
};
const T = (key, vars) => t(state.lang, key, vars);

// ---------------------------------------------------------------- tiny DOM helper

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...kids.flat(Infinity).filter(c => c != null && c !== false));
  return el;
}

const thread = $('#thread');
const welcome = $('#welcome');
const input = $('#input');

function scrollDown() {
  requestAnimationFrame(() => thread.scrollTo({ top: thread.scrollHeight, behavior: 'smooth' }));
}

// ---------------------------------------------------------------- language, steps, location

function renderLangSwitch() {
  $('#langSwitch').replaceChildren(...LANGS.map(l => h('button', {
    role: 'radio', 'aria-checked': String(l.code === state.lang), class: l.code === state.lang ? 'on' : '',
    lang: l.code === 'hi' ? 'hi' : 'en',
    onclick: () => { state.lang = l.code; store.set('cc.lang', l.code); applyLang(); },
  }, l.label)));
}

function applyLang() {
  document.documentElement.lang = state.lang === 'hi' ? 'hi' : 'en';
  for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = T(el.dataset.i18n);
  for (const el of document.querySelectorAll('[data-i18n-ph]')) el.placeholder = T(el.dataset.i18nPh);
  for (const el of document.querySelectorAll('[data-i18n-aria]')) el.setAttribute('aria-label', T(el.dataset.i18nAria));
  renderLangSwitch();
  renderSteps();
  renderLocation();
  renderEmergencySteps();
  setVoiceState(voiceEl.dataset.state);
}

function renderSteps() {
  $('#steps').replaceChildren(...T('steps').map((label, i) => h('li', {
    class: i < state.step ? 'done' : i === state.step ? 'current' : '',
    'aria-current': i === state.step ? 'step' : null,
  }, h('span', { class: 'step-dot' }, i < state.step ? icon('check') : String(i + 1)), h('span', { class: 'step-label' }, label))));
}
function setStep(i) { state.step = i; renderSteps(); }

function locationText() {
  const l = state.location;
  if (!l) return T('locating');
  if (l.source === 'manual') return l.name;
  if (l.source === 'default') return T('locApprox', { place: l.name });
  return l.name ? T('locNear', { place: l.name }) : T('locHere');
}
function renderLocation() {
  $('#locLabel').textContent = locationText();
  $('#locBtn').classList.toggle('pending', !state.location);
}
function setLocation(loc) { state.location = loc; renderLocation(); }

function locateMe() {
  return new Promise(resolve => {
    if (!navigator.geolocation) return resolve(false);
    navigator.geolocation.getCurrentPosition(
      p => {
        const pt = { lat: +p.coords.latitude.toFixed(5), lng: +p.coords.longitude.toFixed(5) };
        setLocation({ ...pt, name: nearestLocality(pt)?.name, source: 'gps' });
        resolve(true);
      },
      () => resolve(false),
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
    );
  });
}

function openLocationSheet() {
  const note = h('p', { class: 'sheet-note' }, T('locNote'));
  const gps = h('button', {
    class: 'loc-option gps',
    onclick: async () => {
      gps.classList.add('loading');
      const ok = await locateMe();
      gps.classList.remove('loading');
      if (ok) closeSheet(); else note.textContent = T('locGpsFail');
    },
  }, icon('crosshair'), h('span', {}, T('locUseGps')));
  const options = LOCALITIES.map(l => h('button', {
    class: 'loc-option' + (state.location?.source !== 'gps' && state.location?.name === l.name ? ' selected' : ''),
    onclick: () => { setLocation({ ...l, source: 'manual' }); closeSheet(); },
  }, icon('pin'), h('span', {}, l.name)));
  openSheet(h('div', {}, h('h2', { class: 'sheet-title' }, T('locTitle')), note, gps, h('div', { class: 'loc-list' }, options)));
}

// ---------------------------------------------------------------- bottom sheet

const sheet = $('#sheet');
let sheetReturnFocus = null;
function openSheet(node) {
  sheetReturnFocus = document.activeElement;
  $('#sheetBody').replaceChildren(node);
  sheet.hidden = false;
  requestAnimationFrame(() => sheet.classList.add('open'));
  setTimeout(() => $('#sheetBody').querySelector('button, a')?.focus({ preventScroll: true }), 60);
}
function closeSheet() {
  if (sheet.hidden) return;
  sheet.classList.remove('open');
  setTimeout(() => { if (!sheet.classList.contains('open')) sheet.hidden = true; }, 260);
  sheetReturnFocus?.focus?.({ preventScroll: true });
}
sheet.addEventListener('click', e => { if (e.target === sheet) closeSheet(); });

// ---------------------------------------------------------------- conversation

function listenBtn(text) {
  return h('button', { class: 'listen', onclick: () => speak(text, detectLang(text), api.tts) }, icon('volume'), T('listen'));
}

function addUser(text) {
  thread.append(h('div', { class: 'msg user' }, h('div', { class: 'bubble' }, text)));
  scrollDown();
}

function addBot(...nodes) {
  const row = h('div', { class: 'msg bot' }, h('div', { class: 'avatar' }, icon('compass')), h('div', { class: 'bot-body' }, nodes));
  thread.append(row);
  scrollDown();
  return row;
}

function addWide(...nodes) {
  const row = h('div', { class: 'msg wide' }, nodes);
  thread.append(row);
  scrollDown();
  return row;
}

function freezeChips() {
  for (const g of thread.querySelectorAll('.chips:not(.stale)')) {
    g.classList.add('stale');
    g.querySelectorAll('button').forEach(b => { b.disabled = true; });
  }
}

function chipRow(items, onPick) {
  return h('div', { class: 'chips', role: 'group' }, items.map(c => h('button', {
    class: 'chip', onclick: e => { e.currentTarget.classList.add('picked'); onPick(c); },
  }, c)));
}

function setBusy(b) {
  state.busy = b;
  $('#app').classList.toggle('busy', b);
  $('#sendBtn').disabled = b || !input.value.trim();
}

function hideWelcome() {
  if (welcome.isConnected) welcome.remove();
  $('#restartBtn').hidden = false;
}

async function submit(text, { echo = true } = {}) {
  text = text.trim();
  if (!text || state.busy) return;
  const convo = state.convo;
  hideWelcome();
  freezeChips();
  if (echo) addUser(text);
  setBusy(true);
  const typing = addBot(h('div', { class: 'bubble typing', role: 'status', 'aria-label': T('thinking') }, h('i'), h('i'), h('i')));

  let res;
  try {
    res = await api.triage({
      user_input: text,
      language: 'auto',
      location: state.location && { lat: state.location.lat, lng: state.location.lng },
      conversation_history: state.history,
    });
  } catch (err) {
    console.error(err);
    if (convo !== state.convo) return;
    typing.remove();
    setBusy(false);
    addBot(h('div', { class: 'bubble error' }, T('error')), chipRow([T('retry')], () => submit(text, { echo: false })));
    if (voice.open) setVoiceState('error', T('error'));
    return;
  }
  if (convo !== state.convo) return;
  typing.remove();
  setBusy(false);
  state.history = [...state.history, { role: 'user', content: text }];
  handle(res);
}

function handle(res) {
  if (res.care_type === 'emergency' || res.red_flags?.length) return showEmergency(res);

  if (res.follow_up_question) {
    const q = res.follow_up_question;
    state.history.push({ role: 'assistant', content: q });
    state.questions++;
    setStep(1);
    const chips = chipsFor(q);
    addBot(
      h('p', { class: 'q-label' }, T('qCount', { n: state.questions })),
      h('div', { class: 'bubble' }, q),
      chips.length && chipRow(chips, submit),
      h('div', { class: 'bubble-actions' }, listenBtn(q)),
    );
    if (voice.open) voiceAsk(q, chips);
    return;
  }

  setStep(2);
  renderPlan(res);
  if (voice.open) {
    closeVoice();
    speak(planSpeech(res), state.lang, api.tts);
  }
}

// ---------------------------------------------------------------- care plan + caregivers

function planSpeech(res) {
  return res.explanation || `${careText(state.lang, res.care_type).label}. ${T('urgency')}: ${urgencyText(state.lang, res.urgency)}.`;
}

function renderPlan(res) {
  const care = careText(state.lang, res.care_type);
  const plan = h('article', { class: `card plan care-${res.care_type}` },
    h('div', { class: 'plan-head' },
      h('div', { class: 'care-icon' }, icon(CARE_ICON[res.care_type] || 'stethoscope')),
      h('div', {},
        h('p', { class: 'eyebrow' }, T('planEyebrow')),
        h('h3', { class: 'plan-title' }, care.label),
        care.desc && h('p', { class: 'plan-desc' }, care.desc))),
    h('div', { class: `urgency u-${res.urgency}` },
      h('span', { class: 'u-dot' }), h('span', { class: 'u-key' }, T('urgency')), h('strong', {}, urgencyText(state.lang, res.urgency))),
    res.explanation && h('p', { class: 'explain' }, res.explanation),
    res.reasoning && h('div', { class: 'why' }, h('p', { class: 'eyebrow' }, T('why')), h('p', {}, res.reasoning)),
    h('div', { class: 'bubble-actions' }, listenBtn(planSpeech(res))),
  );

  const cgs = res.matched_caregivers || [];
  const place = state.location?.name;
  const matches = h('section', { class: 'matches' },
    h('h4', { class: 'section-title' }, place ? T('matchesTitle', { place }) : T('matchesTitleAny')),
    cgs.length ? cgs.map((cg, i) => caregiverCard(cg, i, res.care_type)) : h('p', { class: 'empty' }, T('noMatches')),
  );
  addWide(plan, matches);
}

function slotText(s) {
  const d = new Date(s);
  return isNaN(d) ? String(s) : slotLabel(d, state.lang);
}

const initials = name => name.replace(/^Dr\.?\s+/i, '').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
const hue = s => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);

function caregiverCard(cg, i, careType) {
  const name = cg.name || cg.caregiver_id;
  const pct = Math.round(Math.max(0, Math.min(1, cg.score ?? 0)) * 100);
  const video = careType === 'gp_teleconsult';
  const meta = [
    video ? [icon('video'), T('videoCall')] : cg.distance_km != null && [icon('pin'), T('km', { n: (+cg.distance_km).toFixed(1) })],
    cg.next_slot && [icon('clock'), slotText(cg.next_slot)],
    cg.languages?.length && [icon('languages'), cg.languages.join(', ')],
  ].filter(Boolean).map(([ic, txt]) => h('span', { class: 'meta-item' }, ic, txt));

  const btn = h('button', { class: 'book-btn', onclick: () => book(cg, card, btn, careType) }, T('book'));
  const card = h('article', { class: 'cg' + (i === 0 ? ' best' : ''), style: `--d:${i * 90}ms` },
    i === 0 && h('span', { class: 'best-tag' }, icon('award'), T('bestMatch')),
    h('div', { class: 'cg-head' },
      h('div', { class: 'cg-avatar', style: `--hue:${hue(name)}` }, initials(name)),
      h('div', { class: 'cg-id' },
        h('h5', {}, name),
        h('p', {}, [cg.role, cg.name && cg.caregiver_id].filter(Boolean).join(' · '))),
      cg.rating != null && h('span', { class: 'rating', 'aria-label': `${cg.rating} / 5` }, icon('star'), (+cg.rating).toFixed(1))),
    cg.reason && h('p', { class: 'cg-reason' }, icon('check'), h('span', {}, cg.reason)),
    meta.length && h('div', { class: 'cg-meta' }, meta),
    h('div', { class: 'cg-foot' },
      h('div', { class: 'score', title: T('match', { n: pct }) },
        h('span', { class: 'score-bar' }, h('span', { style: `--w:${pct}%` })),
        h('span', { class: 'score-txt' }, T('match', { n: pct }))),
      btn),
  );
  return card;
}

// ponytail: booking is mocked client-side (no booking endpoint in the contract yet).
async function book(cg, card, btn, careType) {
  if (state.booked) return;
  state.booked = true;
  btn.disabled = true;
  btn.classList.add('loading');
  await sleep(700);
  btn.classList.remove('loading');
  btn.replaceChildren(icon('check'), T('booked'));
  card.classList.add('is-booked');
  thread.querySelectorAll('.book-btn').forEach(b => { if (b !== btn) b.disabled = true; });
  setStep(3);

  const name = cg.name || cg.caregiver_id;
  const video = careType === 'gp_teleconsult';
  const ref = 'CC-' + Math.random().toString(36).slice(2, 7).toUpperCase();
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v)];
  openSheet(h('div', { class: 'booked' },
    h('div', { class: 'check-anim', 'aria-hidden': 'true' }, icon('check')),
    h('h2', { class: 'sheet-title' }, T('bookedTitle')),
    h('p', { class: 'sheet-note' }, T(video ? 'bookedVideo' : 'bookedHome', { name })),
    h('dl', { class: 'summary' },
      row(T('rowWith'), [name, cg.role].filter(Boolean).join(' · ')),
      row(T('rowType'), careText(state.lang, careType).label),
      row(T('rowWhen'), cg.next_slot ? slotText(cg.next_slot) : T('toBeConfirmed')),
      row(T('rowWhere'), video ? T('videoNote') : locationText()),
      row(T('rowRef'), ref)),
    h('button', { class: 'primary-btn', onclick: closeSheet }, T('done')),
    h('button', { class: 'text-btn', onclick: () => { closeSheet(); restart(); } }, T('newRequest')),
  ));
  if (state.usedVoice) speak(T('bookedSpeech', { name }), state.lang, api.tts);
}

// ---------------------------------------------------------------- emergency

const emergencyEl = $('#emergency');

function renderEmergencySteps() {
  $('#emSteps').replaceChildren(...T('emSteps').map(s => h('li', {}, s)));
}

function showEmergency(res) {
  const flags = res.red_flags || [];
  closeVoice();
  $('#emFlags').replaceChildren(...(flags.length
    ? [h('p', { class: 'em-flags-title' }, T('emNoticed')), h('div', { class: 'em-flag-list' }, flags.map(f => h('span', { class: 'em-flag' }, f)))]
    : []));
  // A permanent card in the thread so the call-for-help path survives closing the takeover.
  addWide(h('article', { class: 'card em-card' },
    h('div', { class: 'em-card-head' }, icon('alert'), h('h3', {}, T('emTitle'))),
    flags.length && h('div', { class: 'em-flag-list' }, flags.map(f => h('span', { class: 'em-flag' }, f))),
    res.reasoning && h('p', { class: 'em-card-why' }, res.reasoning),
    h('div', { class: 'em-card-actions' },
      h('a', { class: 'em-mini primary', href: 'tel:112' }, icon('phone'), T('call112')),
      h('a', { class: 'em-mini', href: 'tel:108' }, icon('phone'), T('call108'))),
    h('button', { class: 'text-btn danger', onclick: openEmergency }, T('emShow')),
  ));
  openEmergency();
  if (state.usedVoice) speak([T('emTitle'), T('emSub'), T('call112'), ...T('emSteps')].join(' '), state.lang, api.tts);
}

function openEmergency() {
  emergencyEl.hidden = false;
  requestAnimationFrame(() => emergencyEl.classList.add('open'));
  setTimeout(() => $('#emCall112').focus({ preventScroll: true }), 80);
}
function closeEmergency() {
  emergencyEl.classList.remove('open');
  setTimeout(() => { if (!emergencyEl.classList.contains('open')) emergencyEl.hidden = true; }, 260);
  stopSpeaking();
}

// ---------------------------------------------------------------- voice conversation

const voiceEl = $('#voice');
const voice = { open: false, session: null, gen: 0 };
const bars = Array.from({ length: BANDS }, () => h('span', { class: 'bar' }));
$('#vBars').append(...bars);

const STATE_KEYS = {
  listening: ['listening', 'hintListening'],
  thinking: ['thinking', 'hintThinking'],
  speaking: ['speaking', 'hintSpeaking'],
  idle: ['tapToStart', ''],
  error: ['tapToStart', ''],
};
function setVoiceState(s, caption) {
  voiceEl.dataset.state = s;
  const [title, hint] = STATE_KEYS[s] || STATE_KEYS.idle;
  $('#vState').textContent = T(title);
  $('#vHint').textContent = hint ? T(hint) : '';
  if (caption !== undefined) $('#vCaption').textContent = caption;
  $('#vDone').hidden = s !== 'listening';
  if (s !== 'listening') voiceEl.style.setProperty('--lvl', 0);
}

function renderVoiceChips(items) {
  $('#vChips').replaceChildren(...items.map(c => h('button', {
    class: 'chip',
    onclick: () => {
      voice.gen++;
      voice.session?.cancel();
      voice.session = null;
      stopSpeaking();
      setVoiceState('thinking', c);
      renderVoiceChips([]);
      submit(c);
    },
  }, c)));
}

function openVoice() {
  if (state.busy) return;
  stopSpeaking();
  closeSheet();
  voice.open = true;
  state.usedVoice = true;
  voiceEl.hidden = false;
  requestAnimationFrame(() => voiceEl.classList.add('open'));
  renderVoiceChips([]);
  listen();
}

async function listen() {
  const gen = ++voice.gen;
  setVoiceState('listening', '');
  if (!navigator.mediaDevices?.getUserMedia) return setVoiceState('error', T('micBlocked'));
  let session;
  try {
    session = await startRecording({
      lang: state.lang,
      onLevel: (lvl, bands) => {
        voiceEl.style.setProperty('--lvl', lvl.toFixed(3));
        bands.forEach((b, i) => bars[i].style.setProperty('--b', b.toFixed(3)));
      },
      onInterim: text => { if (gen === voice.gen) $('#vCaption').textContent = text; },
      onSilence: () => { if (gen === voice.gen) finishListening(); },
    });
  } catch {
    if (gen === voice.gen) setVoiceState('error', T('micBlocked'));
    return;
  }
  if (gen !== voice.gen) return session.cancel(); // closed while the permission prompt was up
  voice.session = session;
}

async function finishListening() {
  const session = voice.session;
  if (!session) return;
  voice.session = null;
  const gen = ++voice.gen;
  setVoiceState('thinking');
  const { blob, transcript } = await session.stop();
  const text = (await api.stt(blob)) || transcript;
  if (gen !== voice.gen || !voice.open) return;
  if (!text) return setVoiceState('idle', T('didntCatch'));
  $('#vCaption').textContent = text;
  renderVoiceChips([]);
  submit(text);
}

async function voiceAsk(q, chips) {
  const gen = ++voice.gen;
  setVoiceState('speaking', q);
  renderVoiceChips(chips);
  await speak(q, detectLang(q), api.tts);
  if (gen === voice.gen && voice.open) listen();
}

function closeVoice() {
  if (!voice.open) return;
  voice.open = false;
  voice.gen++;
  voice.session?.cancel();
  voice.session = null;
  stopSpeaking();
  voiceEl.classList.remove('open');
  setTimeout(() => { if (!voice.open) voiceEl.hidden = true; }, 260);
}

$('#vOrb').addEventListener('click', () => {
  const s = voiceEl.dataset.state;
  if (s === 'listening') finishListening();
  else if (s === 'speaking') { voice.gen++; stopSpeaking(); listen(); }
  else if (s === 'idle' || s === 'error') listen();
});
$('#vDone').addEventListener('click', finishListening);
$('#vClose').addEventListener('click', closeVoice);
$('#vType').addEventListener('click', () => { closeVoice(); input.focus({ preventScroll: true }); });

// ---------------------------------------------------------------- composer + misc wiring

function autosize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  $('#sendBtn').disabled = state.busy || !input.value.trim();
}
input.addEventListener('input', autosize);
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#composer').requestSubmit(); }
});
$('#composer').addEventListener('submit', e => {
  e.preventDefault();
  if (state.busy || !input.value.trim()) return;
  const text = input.value;
  input.value = '';
  autosize();
  submit(text);
});
$('#micBtn').addEventListener('click', openVoice);
$('#heroMic').addEventListener('click', openVoice);
$('#locBtn').addEventListener('click', openLocationSheet);
$('#restartBtn').addEventListener('click', restart);
$('#emBack').addEventListener('click', closeEmergency);
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!sheet.hidden) closeSheet();
  else if (voice.open) closeVoice();
  else if (!emergencyEl.hidden) closeEmergency();
});

function restart() {
  state.convo++;
  closeVoice();
  closeSheet();
  closeEmergency();
  stopSpeaking();
  Object.assign(state, { history: [], questions: 0, booked: false, usedVoice: false });
  setBusy(false);
  thread.replaceChildren(welcome);
  $('#restartBtn').hidden = true;
  input.value = '';
  autosize();
  setStep(0);
  requestAnimationFrame(() => thread.scrollTo({ top: 0, behavior: 'instant' })); // cancels any smooth scroll in flight
}

api.onMockMode(() => {
  const tag = $('.brand-tag');
  tag.dataset.i18n = 'demo';
  tag.classList.add('is-demo');
  tag.textContent = T('demo');
});

// ---------------------------------------------------------------- boot

for (const el of document.querySelectorAll('i[data-icon]')) el.replaceWith(icon(el.dataset.icon, el.className));
$('#examples').append(...EXAMPLES.map(ex => h('button', {
  class: 'example', lang: detectLang(ex) === 'hi' ? 'hi' : 'en', onclick: () => submit(ex),
}, h('span', {}, ex), icon('send'))));
applyLang();
setVoiceState('idle');
locateMe().then(ok => { if (!ok) setLocation({ ...LOCALITIES[0], source: 'default' }); });
