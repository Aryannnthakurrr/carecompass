// Backend contract the UI speaks (indicative schema from the problem statement).
//
//   POST {API}/triage  JSON
//     { user_input, language: "auto", location: {lat, lng}, conversation_history: [{role: "user"|"assistant", content}] }
//   -> { follow_up_question, care_type, urgency, red_flags[], reasoning,
//        matched_caregivers: [{caregiver_id, score, reason}] }
//   Optional extras the UI shows when present:
//     explanation                         plain-language text in the user's language (explain.py)
//     matched_caregivers[].name, role, distance_km, languages[], rating, next_slot (ISO 8601)
//
//   POST {API}/stt     raw audio body, Content-Type from MediaRecorder (audio/webm)  -> { transcript }   Sarvam Saaras
//   POST {API}/tts     JSON { text, language: "en"|"hi"|"hinglish" }                -> audio/wav          Sarvam Bulbul
//
// The backend must allow CORS from the page's origin, or serve this folder itself.
// No server / 404 / 501 on /triage -> mock.js takes over so the demo keeps working.
// stt/tts are used whenever the backend has them (even while /triage is mocked);
// otherwise, or if a call fails, the browser's Web Speech API is the fallback.

import { mockTriage } from './mock.js';

const qs = new URLSearchParams(location.search);
export const API_BASE = (qs.get('api') ?? 'http://localhost:8000').replace(/\/$/, '');

let mock = qs.get('mock') === '1';
const mockListeners = [];
export function onMockMode(fn) {
  mockListeners.push(fn);
  if (mock) fn();
}
function useMock() {
  if (mock) return;
  mock = true;
  mockListeners.forEach(fn => fn());
}

export async function triage(req) {
  if (!mock) {
    let r = null;
    try {
      r = await fetch(`${API_BASE}/triage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
        signal: AbortSignal.timeout(30000),
      });
    } catch (e) {
      if (e.name !== 'TypeError') throw e; // a timeout means a slow backend, not a missing one
      useMock();
    }
    if (r) {
      if (r.ok) return r.json();
      if (![404, 405, 501].includes(r.status)) throw new Error(`triage ${r.status}`);
      useMock();
    }
  }
  return mockTriage(req);
}

// An endpoint the backend doesn't have is skipped for the rest of the session;
// a failed call (e.g. Sarvam 502) only falls back for that one call.
const missing = new Set();
async function optional(path, init) {
  if (missing.has(path)) return null;
  try {
    const r = await fetch(`${API_BASE}${path}`, { ...init, signal: AbortSignal.timeout(15000) });
    if (r.ok) return r;
    if ([404, 405, 501].includes(r.status)) missing.add(path);
  } catch (e) {
    if (e.name === 'TypeError') missing.add(path); // no server
  }
  return null;
}

export async function stt(blob) {
  if (!blob?.size) return null;
  const r = await optional('/stt', { method: 'POST', headers: { 'Content-Type': blob.type || 'audio/webm' }, body: blob });
  return r ? ((await r.json()).transcript || '').trim() || null : null;
}

// ---- sign-in and per-user history (session is an HttpOnly cookie set by the API)
//   GET  /auth/config  -> { google_client_id, phone: "sms" | "dev" | null }
//   GET  /auth/me, POST /auth/logout, POST /auth/google {credential}
//   POST /auth/otp/send {phone} -> { phone, resend_in, dev_code? },  POST /auth/otp/verify {phone, code}
//   GET  /me/chats, GET|PUT /me/chats/{uuid} {title, messages, outcome}, GET|POST /me/bookings

async function call(path, { method = 'GET', body } = {}) {
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body && JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${r.status}`), { status: r.status });
  return data;
}

const post = (path, body) => call(path, { method: 'POST', body });
export const account = {
  config: () => call('/auth/config'),
  me: () => call('/auth/me'),
  google: credential => post('/auth/google', { credential }),
  sendOtp: phone => post('/auth/otp/send', { phone }),
  verifyOtp: (phone, code) => post('/auth/otp/verify', { phone, code }),
  logout: () => post('/auth/logout'),
  chats: () => call('/me/chats'),
  chat: id => call(`/me/chats/${id}`),
  saveChat: (id, chat) => call(`/me/chats/${id}`, { method: 'PUT', body: chat }),
  bookings: () => call('/me/bookings'),
  book: booking => post('/me/bookings', booking),
};

// ---- caregiver directory + simulated ambulance dispatch (app/services.py)
//   GET  /caregivers?lat&lng&kind&skill&q&min_rating&min_experience&max_km&sort -> { count, caregivers[], skills[] }
//   POST /emergency/dispatch {location, address, conversation_history, red_flags, reasoning} -> dispatch + first tracking
//   GET  /emergency/{id}/tracking -> { status, position, progress, eta_s, remaining_km }
export const services = {
  caregivers: params => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '' && v !== false));
    return call(`/caregivers?${q}`);
  },
  dispatch: body => post('/emergency/dispatch', body),
  tracking: id => call(`/emergency/${encodeURIComponent(id)}/tracking`),
};

export async function tts(text, language) {
  const r = await optional('/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, language }),
  });
  return r ? r.blob() : null;
}
