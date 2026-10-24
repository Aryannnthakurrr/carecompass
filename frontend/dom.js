// Tiny DOM helper shared by the UI modules: h('div', {class, onclick, ...attrs}, ...children).
export function h(tag, props = {}, ...kids) {
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

export const initials = name => name.replace(/^Dr\.?\s+/i, '').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
export const hue = s => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);
