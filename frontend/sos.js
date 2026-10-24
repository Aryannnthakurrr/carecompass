// One-tap emergency: a 10 s countdown (cancel covers accidental taps), then a SIMULATED ambulance dispatch.
// In production the request would go to an ambulance network's developer API (e.g. Dial4242); here
// POST /emergency/dispatch fakes it and the map shows a pretend ambulance driving to the patient.
import { h } from './dom.js';
import { icon } from './icons.js';
import * as api from './api.js';

const COUNTDOWN = 10;
const RING = 2 * Math.PI * 88;
const LEAFLET = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/';
const el = document.querySelector('#sos');
let run = 0;          // bumps on close: stops timers, polls and animation of an old run
let map = null;

export const sosOpen = () => !el.hidden;

let leaflet = null;
function loadLeaflet() {
  return (leaflet ||= new Promise((resolve, reject) => {
    const s = h('script', { src: `${LEAFLET}leaflet.min.js` });
    s.onload = () => resolve(window.L);
    s.onerror = () => { leaflet = null; reject(new Error('map library failed to load')); };
    document.head.append(h('link', { rel: 'stylesheet', href: `${LEAFLET}leaflet.min.css` }), s);
  }));
}

function show(node, mode) {
  el.dataset.mode = mode;
  el.replaceChildren(node);
  if (el.hidden) {
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add('open'));
  }
}

export function closeSos() {
  if (el.hidden) return;
  run++;
  map?.remove();
  map = null;
  el.classList.remove('open');
  setTimeout(() => { if (!el.classList.contains('open')) el.hidden = true; }, 260);
}

// ctx: { T, flagText(flag), location(), locationText(), history: [{role, content}], outcome }
export function openSos(ctx) {
  const my = ++run;
  const { T } = ctx;
  loadLeaflet().catch(() => {}); // warm up during the countdown
  let left = COUNTDOWN;
  const num = h('span', { class: 'sos-num', 'aria-live': 'assertive' }, String(left));
  const ring = h('span', { class: 'sos-ring' });
  ring.innerHTML = `<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="88" class="track"/><circle cx="100" cy="100" r="88" class="arc" style="stroke-dasharray:${RING};stroke-dashoffset:0"/></svg>`;
  ring.append(num);
  const arc = ring.querySelector('.arc');
  const cancel = h('button', { class: 'sos-cancel', onclick: closeSos }, icon('x'), T('sosCancel'));
  show(h('div', { class: 'sos-count' },
    h('p', { class: 'sos-kicker' }, icon('alert'), T('sosIn')),
    ring,
    h('p', { class: 'sos-share' }, T('sosShare')),
    h('p', { class: 'sos-where' }, icon('pin'), ctx.locationText()),
    cancel,
    h('button', { class: 'sos-now', onclick: () => { left = 0; tick(); } }, T('sosNow')),
  ), 'count');
  setTimeout(() => cancel.focus({ preventScroll: true }), 80);

  let timer = 0;
  function tick() {
    clearTimeout(timer);
    if (my !== run) return;
    num.textContent = String(left);
    arc.style.strokeDashoffset = String(RING * (1 - left / COUNTDOWN));
    if (left <= 0) return dispatch(ctx, my);
    navigator.vibrate?.(80);
    timer = setTimeout(() => { left--; tick(); }, 1000);
  }
  tick();
}

async function dispatch(ctx, my) {
  const { T } = ctx;
  navigator.vibrate?.([200, 100, 200]);
  const steps = ['sosStep1', 'sosStep2', 'sosStep3'].map(k => h('li', {}, h('span', { class: 'st' }), T(k)));
  show(h('div', { class: 'sos-req' },
    h('div', { class: 'sos-pulse' }, icon('ambulance')),
    h('h2', {}, T('sosRequesting')),
    h('ol', { class: 'sos-steps' }, steps),
  ), 'request');

  const loc = ctx.location();
  const body = {
    location: { lat: loc.lat, lng: loc.lng },
    address: ctx.locationText(),
    conversation_history: ctx.history.slice(-40).map(m => ({ role: m.role, content: m.content.slice(0, 2000) })),
    red_flags: ctx.outcome?.red_flags || [],
    reasoning: ctx.outcome?.reasoning?.slice(0, 600) || null,
  };
  const staged = (async () => {
    for (const li of steps) {
      li.classList.add('doing');
      await new Promise(r => setTimeout(r, 850));
      li.classList.replace('doing', 'done');
    }
  })();
  let d;
  try {
    [d] = await Promise.all([api.services.dispatch(body), staged]);
  } catch (e) {
    console.error('dispatch failed', e);
    if (my !== run) return;
    return show(h('div', { class: 'sos-req' },
      h('div', { class: 'sos-pulse' }, icon('alert')),
      h('h2', {}, T('sosFail')),
      h('a', { class: 'em-call primary', href: 'tel:112' }, icon('phone'), h('span', {}, h('b', {}, T('call112')), h('small', {}, T('call112Sub')))),
      h('button', { class: 'sos-close', onclick: closeSos }, T('close')),
    ), 'request');
  }
  if (my === run) track(ctx, d, my);
}

// ---------------------------------------------------------------- live tracking

const mins = s => Math.max(1, Math.round(s / 60));

function track(ctx, d, my) {
  const { T } = ctx;
  const a = d.ambulance;
  const mapBox = h('div', { class: 'sos-map' });
  const status = h('p', { class: 'trk-status' });
  const eta = h('p', { class: 'trk-eta' });
  const away = h('p', { class: 'trk-away' });
  const fill = h('span');
  const brief = d.crew_brief;
  const row = (ic, k, v) => h('div', { class: 'trk-row' }, h('span', { class: 'trk-ic' }, icon(ic)), h('span', {}, h('small', {}, k), h('b', {}, v)));

  const root = h('div', { class: 'sos-track' },
    mapBox,
    h('span', { class: 'sim-badge' }, T('sosSim')),
    h('div', { class: 'trk-panel' },
      h('div', { class: 'trk-top' }, h('div', {}, status, eta, away), h('span', { class: 'trk-ref' }, d.id)),
      h('span', { class: 'trk-bar' }, fill),
      h('div', { class: 'trk-grid' },
        row('ambulance', `${a.code} · ${a.name}`, a.vehicle_no),
        row('user', T('sosDriver'), a.driver),
        row('syringe', T('sosNurse'), a.nurse),
        row('wallet', T('sosFare'), `₹${d.fare_estimate.toLocaleString('en-IN')} · ${T('sosFareNote')}`)),
      h('section', { class: 'brief' },
        h('h3', {}, icon('shield'), T('sosShared')),
        brief.red_flags.length > 0 && h('div', { class: 'em-flag-list' }, brief.red_flags.map(f => h('span', { class: 'em-flag' }, ctx.flagText(f)))),
        brief.patient_said.length
          ? h('div', {}, h('small', {}, T('sosSaid')), h('ul', { class: 'said' }, brief.patient_said.map(s => h('li', {}, `“${s}”`))))
          : h('p', { class: 'muted' }, T('sosNoSymptoms')),
        brief.assessment && h('p', { class: 'muted' }, brief.assessment),
        h('p', { class: 'brief-loc' }, icon('pin'), brief.address || `${d.patient[0].toFixed(4)}, ${d.patient[1].toFixed(4)}`)),
      h('details', { class: 'prod' },
        h('summary', {}, icon('code'), T('sosProd')),
        h('p', {}, T('sosProdText', { provider: d.provider })),
        h('ul', {}, d.would_call.map(c => h('li', {}, h('code', {}, c))))),
      h('div', { class: 'trk-actions' },
        h('a', { class: 'em-mini primary', href: 'tel:112' }, icon('phone'), T('call112')),
        h('button', { class: 'em-mini', onclick: closeSos }, T('close'))),
    ),
  );
  show(root, 'track');

  // The server's tracking endpoint is the source of truth (polled); between polls the marker is
  // moved along the route every frame with the same time-based formula, so the motion is smooth.
  const route = d.route;
  const seg = route.slice(1).map((p, i) => dist(route[i], p));
  const total = seg.reduce((x, y) => x + y, 0);
  let t0 = performance.now() - d.progress * d.demo_drive_s * 1000;
  let st = d;
  const pointAt = frac => {
    let left = frac * total;
    for (let i = 0; i < seg.length; i++) {
      if (left <= seg[i]) {
        const k = seg[i] ? left / seg[i] : 1;
        return { pos: [route[i][0] + (route[i + 1][0] - route[i][0]) * k, route[i][1] + (route[i + 1][1] - route[i][1]) * k], i };
      }
      left -= seg[i];
    }
    return { pos: route[route.length - 1], i: seg.length - 1 };
  };

  function paintText() {
    const arrived = st.status === 'arrived';
    status.textContent = T(arrived ? 'sosArrived' : st.status === 'arriving' ? 'sosArriving' : 'sosOnWay');
    eta.textContent = arrived ? '' : T('sosMin', { n: mins(st.eta_s) });
    away.textContent = arrived ? T('sosArrivedSub') : T('sosKmAway', { n: st.remaining_km.toFixed(1) });
    root.classList.toggle('arrived', arrived);
  }
  paintText();

  (async function poll() {
    while (my === run && st.status !== 'arrived') {
      await new Promise(r => setTimeout(r, 3000));
      if (my !== run) return;
      try {
        st = await api.services.tracking(d.id);
        t0 = performance.now() - st.progress * d.demo_drive_s * 1000;
        paintText();
        if (st.status === 'arrived') navigator.vibrate?.([300, 120, 300]);
      } catch (e) { console.warn('tracking failed', e); }
    }
  })();

  (function bar(now) { // progress bar runs even if the map library never loads
    if (my !== run) return;
    fill.style.width = `${Math.min(1, (now - t0) / (d.demo_drive_s * 1000)) * 100}%`;
    requestAnimationFrame(bar);
  })(performance.now());

  loadLeaflet().then(L => {
    if (my !== run) return;
    map = L.map(mapBox, { zoomControl: false, attributionControl: true });
    // ponytail: OSM's public tile server is fine for a demo; a real app needs its own tile provider
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© OpenStreetMap contributors',
    }).addTo(map);
    const done = L.polyline([], { color: '#8C949C', weight: 5, opacity: .55, dashArray: '2 9' }).addTo(map);
    const ahead = L.polyline(route, { color: '#C42121', weight: 6, opacity: .9 }).addTo(map);
    L.marker(d.patient, { icon: L.divIcon({ className: 'pin-you', html: '<span></span>', iconSize: [22, 22] }), zIndexOffset: 500 }).addTo(map);
    const amb = L.marker(route[0], {
      icon: L.divIcon({ className: 'pin-amb', html: icon('ambulance').outerHTML, iconSize: [40, 40] }), zIndexOffset: 1000,
    }).addTo(map);
    map.fitBounds(L.latLngBounds(route), { padding: [36, 36] });
    let follow = performance.now();
    (function frame(now) {
      if (my !== run || !map) return;
      const frac = Math.min(1, (now - t0) / (d.demo_drive_s * 1000));
      const { pos, i } = pointAt(frac);
      amb.setLatLng(pos);
      ahead.setLatLngs([pos, ...route.slice(i + 1)]);
      done.setLatLngs([...route.slice(0, i + 1), pos]);
      if (now - follow > 5000 && frac < 1) { // zoom in as it closes in, keeping both in view
        follow = now;
        map.flyToBounds(L.latLngBounds([pos, d.patient]), { padding: [60, 60], maxZoom: 16, duration: 1.2 });
      }
      requestAnimationFrame(frame);
    })(performance.now());
  }).catch(() => mapBox.replaceChildren(h('p', { class: 'map-fail' }, icon('pin'), T('sosNoMap'))));
}

function dist(a, b) {
  const r = Math.PI / 180;
  const x = Math.sin((b[0] - a[0]) * r / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}
