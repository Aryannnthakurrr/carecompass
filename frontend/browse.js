// Home-page directory: browse caregivers as cards and filter by kind, service, rating, experience and distance.
// Data comes from GET /caregivers (the organiser roster); booking goes through the same flow as AI matches.
import { h, initials, hue } from './dom.js';
import { icon } from './icons.js';
import * as api from './api.js';

const PAGE = 6;
const KINDS = [['', 'kindAll'], ['doctor', 'kindDoctor'], ['medic', 'kindMedic'], ['phlebotomist', 'kindLab']];
const SORTS = [['best', 'sortBest'], ['distance', 'sortDistance'], ['rating', 'sortRating'], ['experience', 'sortExp']];
const TOGGLES = [['top', 'f4plus', 'star'], ['exp', 'f5yrs', 'award'], ['near', 'f5km', 'pin']];
const BLANK = { kind: '', skill: '', q: '', sort: 'best', top: false, exp: false, near: false };

// ctx: { T, skillText(skill), location() -> {lat, lng, name}?, slotText(iso), onBook(caregiver, button) }
export function renderBrowse(root, ctx) {
  const { T } = ctx;
  const f = { ...BLANK };
  let data = null, failed = false, shown = PAGE, req = 0, timer = 0;

  const controls = h('div', { class: 'dir-controls' });
  const list = h('div', { class: 'dir-list' });
  const head = h('div', { class: 'dir-head' });
  root.replaceChildren(head, controls, list);

  async function load() {
    const my = ++req;
    list.classList.add('loading');
    const loc = ctx.location();
    try {
      data = await api.services.caregivers({
        lat: loc?.lat, lng: loc?.lng, kind: f.kind, skill: f.skill, q: f.q.trim(), sort: f.sort,
        min_rating: f.top && 4, min_experience: f.exp && 5, max_km: f.near && 5,
      });
      failed = false;
    } catch (e) {
      console.warn('caregivers not loaded', e);
      failed = true;
    }
    if (my !== req) return;
    list.classList.remove('loading');
    shown = PAGE;
    drawHead();
    drawControls();
    drawList();
  }
  const set = patch => { Object.assign(f, patch); load(); };

  function drawHead() {
    const place = ctx.location()?.name;
    head.replaceChildren(
      h('h2', { class: 'home-h' }, icon('stethoscope'), place ? T('findCare', { place }) : T('findCareAny')),
      data && h('span', { class: 'dir-count' }, T('results', { n: data.count })));
  }

  function drawControls() {
    const focused = document.activeElement?.classList.contains('dir-search-input');
    const search = h('input', {
      class: 'dir-search-input', type: 'search', placeholder: T('searchPh'), 'aria-label': T('searchPh'), value: f.q, enterkeyhint: 'search',
    });
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => set({ q: search.value }), 250);
    });
    const select = (value, options, label, onchange) => h('label', { class: 'pill-select' + (value && value !== 'best' ? ' on' : '') },
      h('span', { class: 'sr-only' }, label),
      h('select', { onchange: e => onchange(e.target.value) },
        options.map(([v, text]) => {
          const o = h('option', { value: v }, text);
          o.selected = v === value;
          return o;
        })),
      icon('chevron', 'chev'));
    const skills = data?.skills || [];
    controls.replaceChildren(
      h('div', { class: 'dir-search' }, icon('search'), search),
      h('div', { class: 'seg', role: 'tablist' }, KINDS.map(([v, key]) => h('button', {
        role: 'tab', 'aria-selected': String(f.kind === v), class: f.kind === v ? 'on' : '', onclick: () => set({ kind: v }),
      }, T(key)))),
      h('div', { class: 'filters' },
        select(f.sort, SORTS.map(([v, k]) => [v, T(k)]), T('sortLabel'), v => set({ sort: v })),
        skills.length > 0 && select(f.skill, [['', T('anySkill')], ...skills.map(s => [s, ctx.skillText(s)])], T('anySkill'), v => set({ skill: v })),
        TOGGLES.map(([k, key, ic]) => h('button', {
          class: 'fchip' + (f[k] ? ' on' : ''), 'aria-pressed': String(f[k]), onclick: () => set({ [k]: !f[k] }),
        }, icon(ic), T(key)))),
    );
    if (focused) search.focus({ preventScroll: true });
  }

  function drawList() {
    if (failed) {
      return list.replaceChildren(h('div', { class: 'dir-empty' }, h('p', {}, T('dirError')),
        h('button', { class: 'text-btn inline', onclick: load }, T('retry'))));
    }
    const all = data?.caregivers || [];
    if (!all.length) {
      return list.replaceChildren(h('div', { class: 'dir-empty' }, h('p', {}, T('noResults')),
        h('button', { class: 'text-btn inline', onclick: () => set({ ...BLANK }) }, T('clearFilters'))));
    }
    list.replaceChildren(
      ...all.slice(0, shown).map((cg, i) => card(cg, i)),
      all.length > shown && h('button', {
        class: 'ghost-btn wide more-btn', onclick: () => { shown += PAGE; drawList(); },
      }, T('showMore', { n: all.length - shown })));
  }

  function card(cg, i) {
    const btn = h('button', { class: 'book-btn', disabled: !cg.serves_you, onclick: () => ctx.onBook(cg, btn) }, T('book'));
    const extra = cg.skills.length - 3;
    const stat = (ic, text) => h('span', { class: 'stat' }, icon(ic), text);
    return h('article', { class: 'dir-card' + (cg.serves_you ? '' : ' off'), style: `--d:${(i % PAGE) * 45}ms`, 'data-cg': cg.caregiver_id },
      h('div', { class: 'cg-head' },
        h('div', { class: 'cg-avatar', style: `--hue:${hue(cg.name)}` }, initials(cg.name)),
        h('div', { class: 'cg-id' }, h('h5', {}, cg.name), h('p', {}, cg.role)),
        h('span', { class: 'rating', 'aria-label': `${cg.rating} / 5` }, icon('star'), cg.rating.toFixed(1))),
      h('div', { class: 'stats' },
        stat('award', T('yrs', { n: cg.experience_years })),
        cg.video ? stat('video', T('videoCall')) : stat('pin', T('km', { n: cg.distance_km.toFixed(1) })),
        stat('languages', cg.languages.slice(0, 2).join(', '))),
      h('div', { class: 'tags' },
        cg.skills.slice(0, 3).map(s => h('span', { class: 'tag' }, ctx.skillText(s))),
        extra > 0 && h('span', { class: 'tag more', title: cg.skills.slice(3).map(ctx.skillText).join(', ') }, `+${extra}`)),
      h('div', { class: 'cg-foot' },
        cg.serves_you
          ? h('span', { class: 'next' }, icon('clock'), h('span', {}, h('small', {}, T('nextFree')), ctx.slotText(cg.next_slot)))
          : h('span', { class: 'next off' }, icon('pin'), T('outsideArea')),
        btn));
  }

  load();
  return {
    refresh: load, // location changed
    redraw: () => { drawHead(); drawControls(); drawList(); }, // language changed
  };
}
