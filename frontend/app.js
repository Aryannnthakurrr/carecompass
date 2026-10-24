import * as api from './api.js';
import { startRecording, speak, stopSpeaking, BANDS } from './voice.js';
import { LANGS, t, careText, urgencyText, flagText, skillText, detectLang, chipsFor, slotLabel } from './i18n.js';
import { LOCALITIES, nearestLocality } from './geo.js';
import { icon } from './icons.js';
import { h, initials, hue } from './dom.js';
import { renderBrowse } from './browse.js';
import { openSos, closeSos, sosOpen } from './sos.js';

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
const CARE_ICON = {
  paramedic_home_visit: 'homePlus', home_nursing: 'syringe', lab_test_home: 'flask', teleconsultation: 'video', emergency_112: 'alert',
  nurse: 'syringe', gp_teleconsult: 'video', specialist: 'stethoscope', emergency: 'alert', // offline mock's labels
};
const VIDEO_CARE = new Set(['teleconsultation', 'gp_teleconsult']);

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
  chatId: null,       // uuid for saving this conversation to the signed-in user's history
  outcome: null,      // the final /triage response (plan or emergency)
  auth: null,         // /auth/config when sign-in is available, else null
  user: null,
  chats: [],
  bookings: [],
  pendingBooking: null, // a guest's booking, saved if they sign in
};
const T = (key, vars) => t(state.lang, key, vars);

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
  renderAccountBtn();
  renderHomeAccount();
  directory?.redraw();
  if (!profileEl.hidden) renderProfile();
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
  if (l.source === 'coords') return `${l.lat.toFixed(4)}, ${l.lng.toFixed(4)}`;
  if (l.source === 'default') return T('locApprox', { place: l.name });
  return l.name ? T('locNear', { place: l.name }) : T('locHere');
}
function renderLocation() {
  $('#locLabel').textContent = locationText();
  $('#locBtn').classList.toggle('pending', !state.location);
}
function setLocation(loc) { state.location = loc; renderLocation(); directory?.refresh(); }

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
  openSheet(h('div', {}, h('h2', { class: 'sheet-title' }, T('locTitle')), note, gps, h('div', { class: 'loc-list' }, options), coordsForm()));
}

// Exact coordinates, for testing queries anywhere. Pasting "28.54, 77.27" into either box fills both.
const NCR = { lat: [28.3, 28.75], lng: [76.9, 77.6] }; // where the caregiver roster operates
function coordsForm() {
  const cur = state.location;
  const field = (label, value) => h('input', {
    class: 'coord-input', type: 'text', inputmode: 'decimal', 'aria-label': label, placeholder: label,
    value: cur ? String(value) : '',
  });
  const lat = field(T('locLat'), cur?.lat), lng = field(T('locLng'), cur?.lng);
  const msg = h('p', { class: 'coord-msg', role: 'status' });
  const split = e => {
    const parts = e.target.value.split(/[,\s]+/).filter(Boolean);
    if (parts.length === 2) [lat.value, lng.value] = parts;
  };
  lat.addEventListener('input', split);
  lng.addEventListener('input', split);
  const apply = e => {
    e.preventDefault();
    const pt = { lat: parseFloat(lat.value), lng: parseFloat(lng.value) };
    if (!(Math.abs(pt.lat) <= 90 && Math.abs(pt.lng) <= 180)) {
      msg.textContent = T('locBadCoords');
      msg.className = 'coord-msg warn';
      return;
    }
    pt.lat = +pt.lat.toFixed(5); pt.lng = +pt.lng.toFixed(5);
    setLocation({ ...pt, name: nearestLocality(pt)?.name, source: 'coords' });
    const inside = pt.lat >= NCR.lat[0] && pt.lat <= NCR.lat[1] && pt.lng >= NCR.lng[0] && pt.lng <= NCR.lng[1];
    if (inside) return closeSheet();
    msg.textContent = T('locOutside');
    msg.className = 'coord-msg warn';
  };
  return h('form', { class: 'coord-form', onsubmit: apply },
    h('p', { class: 'coord-title' }, T('locCoords')),
    h('div', { class: 'coord-row' }, lat, lng, h('button', { class: 'coord-btn', type: 'submit' }, T('locUse'))),
    msg);
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
  $('#homeBtn').hidden = false;
}

async function submit(text, { echo = true } = {}) {
  text = text.trim();
  if (!text || state.busy) return;
  const convo = state.convo;
  state.chatId ||= crypto.randomUUID();
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

const isEmergency = res => ['emergency_112', 'emergency'].includes(res.care_type) || res.red_flags?.length > 0;

function addQuestion(q, { live = true } = {}) {
  const chips = live ? chipsFor(q) : [];
  addBot(
    h('p', { class: 'q-label' }, T('qCount', { n: state.questions })),
    h('div', { class: 'bubble' }, q),
    chips.length && chipRow(chips, submit),
    h('div', { class: 'bubble-actions' }, listenBtn(q)),
  );
  return chips;
}

function handle(res) {
  if (isEmergency(res)) {
    state.outcome = res;
    saveChat();
    return showEmergency(res);
  }

  if (res.follow_up_question) {
    const q = res.follow_up_question;
    state.history.push({ role: 'assistant', content: q });
    state.questions++;
    setStep(1);
    const chips = addQuestion(q);
    saveChat();
    if (voice.open) voiceAsk(q, chips);
    return;
  }

  state.outcome = res;
  saveChat();
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

function caregiverCard(cg, i, careType) {
  const name = cg.name || cg.caregiver_id;
  const pct = Math.round(Math.max(0, Math.min(1, cg.score ?? 0)) * 100);
  const video = VIDEO_CARE.has(careType);
  const meta = [
    video ? [icon('video'), T('videoCall')] : cg.distance_km != null && [icon('pin'), T('km', { n: (+cg.distance_km).toFixed(1) })],
    cg.next_slot && [icon('clock'), slotText(cg.next_slot)],
    cg.languages?.length && [icon('languages'), cg.languages.join(', ')],
  ].filter(Boolean).map(([ic, txt]) => h('span', { class: 'meta-item' }, ic, txt));

  const btn = h('button', { class: 'book-btn', onclick: () => book(cg, card, btn, careType) }, T('book'));
  const card = h('article', { class: 'cg' + (i === 0 ? ' best' : ''), style: `--d:${i * 90}ms`, 'data-cg': cg.caregiver_id },
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

function markBooked(caregiverId) {
  state.booked = true;
  for (const card of thread.querySelectorAll('.cg')) {
    const btn = card.querySelector('.book-btn');
    btn.disabled = true;
    if (card.dataset.cg === caregiverId) {
      btn.replaceChildren(icon('check'), T('booked'));
      card.classList.add('is-booked');
    }
  }
}

const bookingLocation = () => state.location && { lat: state.location.lat, lng: state.location.lng, name: state.location.name ?? null };

// Booked from the AI conversation (the booking keeps its chat_id).
async function book(cg, card, btn, careType) {
  if (state.booked) return;
  state.booked = true;
  btn.disabled = true;
  btn.classList.add('loading');
  const { ref, saveNote } = await saveBooking({
    chat_id: state.chatId,
    caregiver: cg,
    care_type: careType,
    urgency: state.outcome?.urgency ?? null,
    slot: cg.next_slot ?? null,
    location: bookingLocation(),
  });
  btn.classList.remove('loading');
  markBooked(cg.caregiver_id);
  setStep(3);
  showBooked(cg, careType, ref, saveNote);
  if (state.usedVoice) speak(T('bookedSpeech', { name: cg.name || cg.caregiver_id }), state.lang, api.tts);
}

// Booked straight from the home-page directory (no conversation, chat_id null): confirm first.
function bookFromSearch(cg) {
  const video = VIDEO_CARE.has(cg.care_type);
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v)];
  const confirm = h('button', {
    class: 'primary-btn',
    onclick: async () => {
      confirm.disabled = true;
      confirm.classList.add('loading');
      const caregiver = { caregiver_id: cg.caregiver_id, name: cg.name, role: cg.role, rating: cg.rating, distance_km: video ? null : cg.distance_km };
      const { ref, saveNote } = await saveBooking({
        chat_id: null, caregiver, care_type: cg.care_type, urgency: null, slot: cg.next_slot, location: bookingLocation(),
      });
      showBooked(cg, cg.care_type, ref, saveNote);
      renderHomeAccount();
    },
  }, T('confirmBooking'));
  openSheet(h('div', { class: 'booked' },
    h('div', { class: 'cg-avatar lg', style: `--hue:${hue(cg.name)}` }, initials(cg.name)),
    h('h2', { class: 'sheet-title' }, T('bookTitle', { name: cg.name })),
    h('p', { class: 'sheet-note' }, cg.role),
    h('dl', { class: 'summary' },
      row(T('rowType'), careText(state.lang, cg.care_type).label),
      row(T('rowWhen'), slotText(cg.next_slot)),
      row(T('rowWhere'), video ? T('videoNote') : locationText()),
      row(T('rowRating'), `★ ${cg.rating.toFixed(1)} · ${T('yrs', { n: cg.experience_years })}`)),
    confirm,
    h('button', { class: 'text-btn', onclick: closeSheet }, T('cancel'))));
}

// Saved to the user's account when signed in; a guest's booking is kept to save after sign-in.
// There is no caregiver-side scheduling yet.
async function saveBooking(payload) {
  let ref = null, saveNote = null;
  if (state.user) {
    try {
      const saved = await api.account.book(payload);
      ref = saved.id;
      state.bookings.unshift(saved);
    } catch (e) {
      console.warn('booking not saved', e);
      saveNote = h('p', { class: 'sheet-note warn' }, T('notSaved'));
    }
  } else {
    await sleep(600);
    if (state.auth) {
      state.pendingBooking = payload;
      saveNote = h('div', { class: 'save-nudge' }, h('span', {}, T('saveNudge')),
        h('button', { class: 'text-btn inline', onclick: openSignIn }, T('signIn')));
    }
  }
  return { ref: ref || 'CC-' + Math.random().toString(36).slice(2, 8).toUpperCase(), saveNote };
}

function showBooked(cg, careType, ref, saveNote) {
  const name = cg.name || cg.caregiver_id;
  const video = VIDEO_CARE.has(careType);
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
    saveNote,
    h('button', { class: 'primary-btn', onclick: closeSheet }, T('done')),
    state.user
      ? h('button', { class: 'text-btn', onclick: () => { closeSheet(); openProfile(); } }, T('seeAppointments'))
      : h('button', { class: 'text-btn', onclick: () => { closeSheet(); restart(); } }, T('newRequest')),
  ));
}

// ---------------------------------------------------------------- emergency

const emergencyEl = $('#emergency');

function renderEmergencySteps() {
  $('#emSteps').replaceChildren(...T('emSteps').map(s => h('li', {}, s)));
}

// A permanent card in the thread so the call-for-help path survives closing the takeover.
function emergencyCard(res) {
  const flags = res.red_flags || [];
  return h('article', { class: 'card em-card' },
    h('div', { class: 'em-card-head' }, icon('alert'), h('h3', {}, T('emTitle'))),
    flags.length && h('div', { class: 'em-flag-list' }, flags.map(f => h('span', { class: 'em-flag' }, flagText(state.lang, f)))),
    res.reasoning && h('p', { class: 'em-card-why' }, res.reasoning),
    h('div', { class: 'em-card-actions' },
      h('a', { class: 'em-mini primary', href: 'tel:112' }, icon('phone'), T('call112')),
      h('a', { class: 'em-mini', href: 'tel:108' }, icon('phone'), T('call108'))),
    h('button', { class: 'em-mini amb', onclick: startSos }, icon('ambulance'), T('sosSendAmb')),
    h('button', { class: 'text-btn danger', onclick: openEmergency }, T('emShow')),
  );
}

// An emergency from triage goes straight into the ambulance countdown (cancelable for 10 s);
// the card stays in the thread with the call buttons and the pre-written steps.
function showEmergency(res) {
  const flags = res.red_flags || [];
  closeVoice();
  $('#emFlags').replaceChildren(...(flags.length
    ? [h('p', { class: 'em-flags-title' }, T('emNoticed')), h('div', { class: 'em-flag-list' }, flags.map(f => h('span', { class: 'em-flag' }, flagText(state.lang, f))))]
    : []));
  addWide(emergencyCard(res));
  startSos();
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

// ---------------------------------------------------------------- account: sign-in, saved history, home page

const locale = () => (state.lang === 'hi' ? 'hi-IN' : 'en-IN');
function fmtDate(v) {
  const d = new Date(typeof v === 'number' ? v * 1000 : v);
  if (isNaN(d)) return '';
  return `${d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' })}`;
}

async function initAccount() {
  try {
    const cfg = await api.account.config();
    state.auth = cfg.google_client_id || cfg.phone ? cfg : null;
  } catch {
    state.auth = null; // no API, or one without sign-in: the app works as a guest
  }
  if (state.auth) {
    try { state.user = (await api.account.me()).user; } catch { state.user = null; }
    if (state.user) await loadHistory();
  }
  renderAccountBtn();
  renderHomeAccount();
}

async function loadHistory() {
  try {
    [state.chats, state.bookings] = await Promise.all([api.account.chats(), api.account.bookings()]);
  } catch (e) {
    if (e.status === 401) {
      Object.assign(state, { user: null, chats: [], bookings: [] });
      renderAccountBtn();
    }
  }
}

function saveChat() {
  if (!state.user || !state.chatId || !state.history.length) return;
  const first = state.history.find(m => m.role === 'user')?.content || '…';
  api.account.saveChat(state.chatId, {
    title: first.slice(0, 120),
    messages: state.history.map(m => ({ role: m.role, content: m.content.slice(0, 2000) })),
    outcome: state.outcome,
  }).catch(e => console.warn('chat not saved', e));
}

function avatarFor(u) {
  if (u.picture) return h('img', { src: u.picture, alt: '', referrerpolicy: 'no-referrer' });
  return u.name ? h('span', { class: 'initials' }, initials(u.name)) : icon('user');
}

function renderAccountBtn() {
  const b = $('#accountBtn');
  b.hidden = !state.auth;
  b.classList.toggle('signed-in', !!state.user);
  b.setAttribute('aria-label', T(state.user ? 'account' : 'signIn'));
  b.replaceChildren(state.user ? avatarFor(state.user) : icon('user'));
}

function renderHomeAccount() {
  const hello = $('#hello');
  hello.hidden = !state.user;
  if (state.user) hello.textContent = state.user.name ? T('hello', { name: state.user.name.split(' ')[0] }) : T('helloAnon');

  const box = $('#homeAccount');
  welcome.classList.remove('has-history');
  if (!state.auth) return box.replaceChildren();
  if (!state.user) {
    return box.replaceChildren(h('div', { class: 'keep-card' },
      h('span', { class: 'keep-icon' }, icon('shield')),
      h('div', { class: 'keep-text' }, h('p', { class: 'keep-title' }, T('keepTitle')), h('p', { class: 'keep-sub' }, T('keepSub'))),
      h('button', { class: 'pill-btn', onclick: openSignIn }, T('signIn'))));
  }

  const { up } = splitBookings();
  const bookings = up.slice(0, 2).map(b => h('button', { class: 'hist-row', onclick: openProfile },
    h('span', { class: 'hist-icon' }, icon(CARE_ICON[b.care_type] || 'calendar')),
    h('span', { class: 'hist-main' },
      h('span', { class: 'hist-title' }, b.caregiver.name || b.caregiver.caregiver_id),
      h('span', { class: 'hist-sub' }, [b.slot && slotText(b.slot), careText(state.lang, b.care_type).label].filter(Boolean).join(' · '))),
    h('span', { class: 'status up' }, T('upcoming'))));
  const chats = state.chats.slice(0, 8).map(c => h('button', { class: 'hist-row', onclick: () => openPastChat(c.id) },
    h('span', { class: 'hist-icon' + (c.care_type === 'emergency' ? ' em' : '') }, icon(CARE_ICON[c.care_type] || 'chat')),
    h('span', { class: 'hist-main' },
      h('span', { class: 'hist-title' }, c.title),
      h('span', { class: 'hist-sub' }, `${fmtDate(c.updated_at)} · ${c.care_type ? careText(state.lang, c.care_type).label : T('inProgress')}`)),
    icon('chevronRight', 'chev')));

  const section = (iconName, title, rows, action) => rows.length && h('section', { class: 'home-section' },
    h('div', { class: 'home-h-row' }, h('h2', { class: 'home-h' }, icon(iconName), title), action),
    h('div', { class: 'hist-list' }, rows));
  const seeAll = h('button', { class: 'text-btn inline', onclick: openProfile }, T('seeAll'), icon('chevronRight'));
  welcome.classList.toggle('has-history', bookings.length + chats.length > 0); // history above the examples
  box.replaceChildren(...[
    section('calendar', T('nextAppts'), bookings, seeAll),
    section('chat', T('recentChats'), chats.slice(0, 4)),
  ].filter(Boolean));
}

// Upcoming (soonest first) and past (latest first) bookings.
function splitBookings() {
  const now = Date.now();
  const at = b => (b.slot ? new Date(b.slot).getTime() : Infinity);
  const up = state.bookings.filter(b => at(b) > now).sort((a, b) => at(a) - at(b));
  const past = state.bookings.filter(b => at(b) <= now).sort((a, b) => at(b) - at(a));
  return { up, past };
}

// ---- profile page: the user's appointments, booked by the assistant or from the directory

const profileEl = $('#profile');
let apptTab = 'up';

function openProfile() {
  closeSheet();
  renderProfile();
  profileEl.hidden = false;
  requestAnimationFrame(() => profileEl.classList.add('open'));
  setTimeout(() => $('#pfBack').focus({ preventScroll: true }), 80);
  if (state.user) loadHistory().then(() => { if (!profileEl.hidden) renderProfile(); });
}
function closeProfile() {
  profileEl.classList.remove('open');
  setTimeout(() => { if (!profileEl.classList.contains('open')) profileEl.hidden = true; }, 260);
}

function renderProfile() {
  const body = $('#pfBody');
  const u = state.user;
  if (!u) {
    return body.replaceChildren(h('div', { class: 'keep-card' },
      h('span', { class: 'keep-icon' }, icon('calendar')),
      h('div', { class: 'keep-text' }, h('p', { class: 'keep-title' }, T('appointments')), h('p', { class: 'keep-sub' }, T('signInToSee'))),
      state.auth && h('button', { class: 'pill-btn', onclick: openSignIn }, T('signIn'))));
  }
  const { up, past } = splitBookings();
  const viaAi = state.bookings.filter(b => b.chat_id).length;
  const title = u.name || u.phone || u.email;
  const list = apptTab === 'up' ? up : past;
  const stat = (n, label) => h('div', { class: 'pf-stat' }, h('b', {}, String(n)), h('span', {}, label));
  body.replaceChildren(
    h('div', { class: 'pf-card' },
      h('div', { class: 'acct-head' },
        h('span', { class: 'acct-avatar' }, avatarFor(u)),
        h('div', {},
          h('p', { class: 'acct-name' }, title),
          h('p', { class: 'acct-sub' }, [u.email, u.phone].filter(x => x && x !== title).join(' · ')))),
      h('div', { class: 'pf-stats' }, stat(up.length, T('upcoming')), stat(past.length, T('completed')), stat(viaAi, T('viaAiShort')))),
    h('h3', { class: 'home-h' }, icon('calendar'), T('appointments')),
    h('div', { class: 'seg', role: 'tablist' }, [['up', T('apptUpcoming'), up.length], ['past', T('apptPast'), past.length]].map(([k, label, n]) =>
      h('button', {
        role: 'tab', 'aria-selected': String(apptTab === k), class: apptTab === k ? 'on' : '',
        onclick: () => { apptTab = k; renderProfile(); },
      }, label, h('span', { class: 'seg-n' }, String(n))))),
    list.length
      ? h('div', { class: 'appts' }, list.map(apptCard))
      : h('div', { class: 'dir-empty' }, h('p', {}, T(apptTab === 'up' ? 'noUpcoming' : 'noPast')),
        h('button', { class: 'text-btn inline', onclick: () => { closeProfile(); restart(); } }, T('findCareCta'))),
    h('button', { class: 'ghost-btn wide', onclick: signOut }, icon('logout'), T('signOut')),
  );
}

function apptCard(b) {
  const cg = b.caregiver;
  const name = cg.name || cg.caregiver_id;
  const video = VIDEO_CARE.has(b.care_type);
  const d = b.slot ? new Date(b.slot) : null;
  const ok = d && !isNaN(d);
  const ai = !!b.chat_id;
  return h('article', { class: 'appt' + (apptTab === 'past' ? ' past' : '') },
    h('div', { class: 'appt-date' },
      h('small', {}, ok ? d.toLocaleDateString(locale(), { weekday: 'short' }) : '—'),
      h('b', {}, ok ? String(d.getDate()) : '?'),
      h('small', {}, ok ? d.toLocaleDateString(locale(), { month: 'short' }) : '')),
    h('div', { class: 'appt-main' },
      h('p', { class: 'appt-time' }, icon(CARE_ICON[b.care_type] || 'clock'),
        [ok && d.toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' }), careText(state.lang, b.care_type).label].filter(Boolean).join(' · ')),
      h('h4', {}, name),
      cg.role && h('p', { class: 'appt-role' }, cg.role),
      h('div', { class: 'appt-tags' },
        h('span', { class: 'src ' + (ai ? 'ai' : 'manual') }, icon(ai ? 'chat' : 'search'), T(ai ? 'viaAi' : 'viaSearch')),
        h('span', { class: 'tag' }, video ? T('videoCall') : (b.location?.name || T('atHome'))),
        h('span', { class: 'appt-ref' }, b.id)),
      ai && h('button', { class: 'text-btn inline', onclick: () => { closeProfile(); openPastChat(b.chat_id); } }, T('viewChat'), icon('chevronRight'))));
}

async function openPastChat(id) {
  let chat;
  try { chat = await api.account.chat(id); } catch (e) { console.warn('chat not loaded', e); return; }
  resetConversation();
  hideWelcome();
  Object.assign(state, { chatId: chat.id, history: chat.messages, outcome: chat.outcome });
  thread.append(h('p', { class: 'past-banner' }, icon('clock'), T('pastChat', { date: fmtDate(chat.updated_at) })));
  const o = chat.outcome;
  chat.messages.forEach((m, i) => {
    if (m.role === 'user') return addUser(m.content);
    state.questions++;
    addQuestion(m.content, { live: !o && i === chat.messages.length - 1 }); // an unfinished chat can be picked up again
  });
  if (o && isEmergency(o)) addWide(emergencyCard(o));
  else if (o) renderPlan(o);
  const booking = state.bookings.find(b => b.chat_id === id);
  if (booking) markBooked(booking.caregiver.caregiver_id);
  setStep(booking ? 3 : o ? 2 : state.questions ? 1 : 0);
}

// ---- sign-in sheet: Google button + phone OTP

let signInError = null;
function authMessage(e, kind) {
  if (e.status === 422) return T(kind === 'phone' ? 'errPhone' : 'errCode');
  if (e.status === 400) return T('errCode');
  if (e.status === 429) return T('errTooMany');
  return T('errSignIn');
}

function openSignIn() {
  if (!state.auth) return;
  const err = signInError = h('p', { class: 'form-error', role: 'alert' });
  const gslot = state.auth.google_client_id && h('div', { class: 'gsi-slot' });
  const phone = state.auth.phone && phoneForm(err);
  openSheet(h('div', { class: 'signin' },
    h('h2', { class: 'sheet-title' }, T('signInTitle')),
    h('p', { class: 'sheet-note' }, T('signInSub')),
    gslot,
    gslot && phone && h('p', { class: 'divider' }, h('span', {}, T('orPhone'))),
    phone,
    err));
  if (gslot) renderGoogleButton(gslot);
}

let gsi = null;
function loadGsi() {
  return (gsi ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.async = true;
    s.onload = () => {
      google.accounts.id.initialize({ client_id: state.auth.google_client_id, callback: onGoogleCredential });
      resolve();
    };
    s.onerror = () => { gsi = null; reject(new Error('Google sign-in script failed to load')); };
    document.head.append(s);
  }));
}

async function renderGoogleButton(slot) {
  try {
    await loadGsi();
    google.accounts.id.renderButton(slot, {
      theme: 'outline', size: 'large', shape: 'pill', text: 'continue_with',
      width: Math.min(340, slot.clientWidth || 320), locale: state.lang === 'hi' ? 'hi' : 'en',
    });
  } catch {
    slot.replaceWith(h('p', { class: 'form-error' }, T('errSignIn')));
  }
}

async function onGoogleCredential({ credential }) {
  try {
    onSignedIn((await api.account.google(credential)).user);
  } catch (e) {
    if (signInError) signInError.textContent = authMessage(e);
  }
}

function phoneForm(err) {
  const wrap = h('div', { class: 'phone-form' });
  let timer = 0;
  const busy = (btn, on) => { btn.disabled = on; btn.classList.toggle('loading', on); };

  function askNumber(prefill = '') {
    clearTimeout(timer);
    const num = h('input', {
      class: 'text-input', id: 'otpPhone', type: 'tel', inputmode: 'numeric', autocomplete: 'tel-national',
      maxlength: '14', placeholder: '98765 43210', value: prefill,
    });
    const send = h('button', { class: 'primary-btn', type: 'submit' }, T('sendCode'));
    wrap.replaceChildren(h('form', {
      class: 'otp-form',
      onsubmit: async e => {
        e.preventDefault();
        if (send.disabled) return;
        err.textContent = '';
        busy(send, true);
        try { askCode(await api.account.sendOtp(num.value)); }
        catch (x) { err.textContent = authMessage(x, 'phone'); busy(send, false); }
      },
    },
      h('label', { class: 'field-label', for: 'otpPhone' }, T('phoneLabel')),
      h('div', { class: 'phone-input' }, h('span', { class: 'prefix' }, '+91'), num),
      send));
    setTimeout(() => num.focus({ preventScroll: true }), 120);
  }

  function askCode(sent) {
    const code = h('input', {
      class: 'text-input code-input', id: 'otpCode', type: 'text', inputmode: 'numeric',
      autocomplete: 'one-time-code', maxlength: '6', placeholder: '••••••',
    });
    const verify = h('button', { class: 'primary-btn', type: 'submit' }, T('verify'));
    const resend = h('button', { class: 'text-btn inline', type: 'button' });
    let left = sent.resend_in ?? 30;
    const tick = () => {
      resend.disabled = left > 0;
      resend.textContent = left > 0 ? T('resendIn', { n: left }) : T('resend');
      if (left-- > 0) timer = setTimeout(tick, 1000);
    };
    resend.onclick = async () => {
      err.textContent = '';
      try { askCode(await api.account.sendOtp(sent.phone)); } catch (x) { err.textContent = authMessage(x, 'phone'); }
    };
    const form = h('form', {
      class: 'otp-form',
      onsubmit: async e => {
        e.preventDefault();
        if (verify.disabled) return;
        err.textContent = '';
        busy(verify, true);
        try { onSignedIn((await api.account.verifyOtp(sent.phone, code.value)).user); }
        catch (x) { err.textContent = authMessage(x, 'code'); busy(verify, false); code.select(); }
      },
    },
      h('label', { class: 'field-label', for: 'otpCode' }, T('codeSentTo', { phone: sent.phone.replace(/^\+91/, '+91 ') })),
      sent.dev_code && h('p', { class: 'dev-code' }, T('demoCode', { code: sent.dev_code })),
      code,
      verify,
      h('div', { class: 'otp-links' },
        h('button', { class: 'text-btn inline', type: 'button', onclick: () => askNumber(sent.phone.slice(3)) }, T('changeNumber')),
        resend));
    code.addEventListener('input', () => {
      code.value = code.value.replace(/\D/g, '');
      if (code.value.length === 6) form.requestSubmit();
    });
    wrap.replaceChildren(form);
    clearTimeout(timer);
    tick();
    setTimeout(() => code.focus({ preventScroll: true }), 120);
  }

  askNumber();
  return wrap;
}

async function onSignedIn(user) {
  state.user = user;
  renderAccountBtn();
  closeSheet();
  saveChat(); // a guest's conversation in progress joins their history
  if (state.pendingBooking) {
    try { state.bookings.unshift(await api.account.book(state.pendingBooking)); } catch (e) { console.warn('booking not saved', e); }
    state.pendingBooking = null;
  }
  await loadHistory();
  renderHomeAccount();
}

async function signOut() {
  try { await api.account.logout(); } catch { /* session may already be gone */ }
  Object.assign(state, { user: null, chats: [], bookings: [] });
  renderAccountBtn();
  closeProfile();
  restart();
}

// ---------------------------------------------------------------- one-tap emergency (sos.js)

const sosCtx = () => ({
  T,
  flagText: f => flagText(state.lang, f),
  location: () => state.location || LOCALITIES[0],
  locationText,
  history: state.history,
  outcome: state.outcome,
});
function startSos() {
  closeVoice();
  closeSheet();
  closeEmergency();
  stopSpeaking();
  openSos(sosCtx());
}

// ---------------------------------------------------------------- home-page directory (browse.js)

let directory = null;
function initDirectory() {
  directory = renderBrowse($('#directory'), {
    T,
    skillText: s => skillText(state.lang, s),
    location: () => state.location,
    slotText,
    onBook: bookFromSearch,
  });
}

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
$('#homeBtn').addEventListener('click', restart);
$('#accountBtn').addEventListener('click', () => (state.user ? openProfile() : openSignIn()));
$('#pfBack').addEventListener('click', closeProfile);
$('#sosBtn').addEventListener('click', startSos);
$('#emAmb').addEventListener('click', startSos);
$('#emBack').addEventListener('click', closeEmergency);
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!sheet.hidden) closeSheet();
  else if (sosOpen()) closeSos();
  else if (!profileEl.hidden) closeProfile();
  else if (voice.open) closeVoice();
  else if (!emergencyEl.hidden) closeEmergency();
});

function resetConversation() {
  state.convo++;
  closeVoice();
  closeSheet();
  closeEmergency();
  stopSpeaking();
  Object.assign(state, { history: [], questions: 0, booked: false, usedVoice: false, chatId: null, outcome: null, pendingBooking: null });
  setBusy(false);
  thread.replaceChildren();
  input.value = '';
  autosize();
  setStep(0);
}

// Back to the home page (welcome + the user's bookings and chats).
function restart() {
  resetConversation();
  thread.append(welcome);
  $('#homeBtn').hidden = true;
  renderHomeAccount();
  if (state.user) loadHistory().then(renderHomeAccount);
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
initDirectory();
setVoiceState('idle');
locateMe().then(ok => { if (!ok) setLocation({ ...LOCALITIES[0], source: 'default' }); });
initAccount();
