// Self-check for the offline mock's routing and red-flag handling.  Run: node frontend/mock.check.mjs
import assert from 'node:assert/strict';

globalThis.setTimeout = fn => queueMicrotask(fn); // skip the simulated network delay
const { mockTriage } = await import('./mock.js');

async function run(...turns) {
  let history = [], r;
  for (const u of turns) {
    r = await mockTriage({ user_input: u, conversation_history: history, location: { lat: 28.54, lng: 77.27 } });
    history = [...history, { role: 'user', content: u }];
    if (r.follow_up_question) history.push({ role: 'assistant', content: r.follow_up_question });
  }
  return r.follow_up_question ? 'ask' : `${r.care_type}/${r.urgency}`;
}

const cases = [
  // red flags in every language go straight to emergency
  [['My father has chest pain and is sweating'], 'emergency/immediate'],
  [['Mummy ko saans nahi aa rahi'], 'emergency/immediate'],
  [['दादी बेहोश हो गई हैं'], 'emergency/immediate'],
  [['My 2 month old baby has had fever since today'], 'emergency/immediate'],
  [['Baby has fever since today', 'Under 3 months'], 'emergency/immediate'],
  [['Papa ko do din se bukhar hai', '60 saal ya zyada', 'Haan'], 'emergency/immediate'],
  // denials are not red flags
  [['Papa ko 2 din se bukhar hai, saans lene mein koi dikkat nahi', '60 saal ya zyada', 'Nahi'], 'paramedic_home_visit/same_day'],
  [['Fever for 2 days, no chest pain', '13–59 years', 'No'], 'gp_teleconsult/same_day'],
  // routing
  [['Fever and cough since yesterday', '13–59 years', 'Not sure'], 'paramedic_home_visit/same_day'],
  [['I have had a headache since yesterday', '13–59 years', 'No'], 'gp_teleconsult/same_day'],
  [['I need a nurse to change a wound dressing at home', 'Since today', '13–59 years'], 'nurse/routine'],
  [['मेरी माँ के घुटनों में 3 महीने से दर्द है', '60 साल या ज़्यादा'], 'specialist/routine'],
  // asks before deciding
  [['Papa ko bukhar hai'], 'ask'],
];

for (const [turns, want] of cases) assert.equal(await run(...turns), want, turns.join(' → '));

const res = await mockTriage({ user_input: 'Need a nurse for dressing', conversation_history: [
  { role: 'user', content: 'x' }, { role: 'assistant', content: 'How long has this been going on?' },
  { role: 'user', content: 'Since today' }, { role: 'assistant', content: 'How old is the patient?' },
], location: { lat: 28.54, lng: 77.27 } });
assert.ok(res.matched_caregivers.length > 0 && res.matched_caregivers.every(c => c.reason && c.score > 0));
assert.ok(res.matched_caregivers.every((c, i, a) => i === 0 || a[i - 1].score >= c.score), 'ranked by score');

console.log(`mock.check: ${cases.length + 1} checks passed`);
