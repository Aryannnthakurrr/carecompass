// Offline stand-in for POST /triage so the full journey can be demoed before the backend is up.
// Same request/response shape as the real API. Synthetic data only; the real logic lives in app/.
import { haversineKm } from './geo.js';
import { detectLang, t, slotLabel } from './i18n.js';

const sleep = ms => new Promise(r => setTimeout(r, ms));

const Q = {
  duration: { en: 'How long has this been going on?', hi: 'यह परेशानी कब से है?', hinglish: 'Yeh pareshani kab se hai?' },
  age: { en: 'How old is the patient?', hi: 'मरीज़ की उम्र कितनी है?', hinglish: 'Mareez ki umar kitni hai?' },
  screen: {
    en: 'Is there any difficulty breathing, chest pain or confusion?',
    hi: 'क्या साँस लेने में तकलीफ़, सीने में दर्द या बेहोशी जैसा कुछ है?',
    hinglish: 'Kya saans lene mein taklif, seene mein dard ya behoshi jaisa kuch hai?',
  },
};

const RED_FLAGS = [
  ['chest pain', /chest pain|pain in (the |my |his |her )?chest|seene? (me|mein|main) dard|chhati (me|mein) dard|सीने में दर्द|छाती में दर्द/i],
  ['difficulty breathing', /(difficulty|trouble|problem) (in )?breathing|breathing (difficulty|trouble|problem)|short(ness)? of breath|breathless|can'?t breathe|saa?ns.{0,25}?(taklif|takleef|dikkat|problem|nahi aa|phool)|सा[ंँ]स.{0,25}?(तकलीफ़|तकलीफ|दिक्कत|नहीं आ|फूल)/i],
  ['unconscious or not responding', /unconscious|not responding|unresponsive|passed out|fainted|behosh|बेहोश/i],
  ['seizure', /seizure|convuls|\bfits?\b|daura|दौरा|jhatke|झटके/i],
  ['face drooping, numbness or slurred speech', /face (is )?(drooping|droop|tilted)|mu[nh]* tedha|मु[ँं]ह टेढ़ा|slurred speech|bolne (me|mein) (dikkat|taklif)|बोलने में (दिक्कत|तकलीफ)|numbness|(arm|leg|hand) (is |feels )?numb|sunn|सुन्न/i],
  ['heavy bleeding', /heavy bleeding|bleeding (a lot|heavily|badly|won'?t stop|not stopping)|bahut khoon|बहुत खून|khoon ruk nahi|खून रुक नहीं/i],
  ['head injury', /head injury|hit (his|her|my) head|sir (pe|par|me|mein) chot|सिर (पर|में) चोट/i],
  ['swallowed something harmful', /poison|zeh?e?r|zahar|ज़हर|जहर|pesticide|keetnashak|bleach|kerosene/i],
  ['face or throat swelling', /(face|throat|lips?|tongue) (is |are )?(swelling|swollen)|gala sooj|chehra sooj|honth sooj|गला सूज|चेहरा सूज|होंठ सूज/i],
  ['blue lips', /blue lips|lips (are |turning )?blue|honth neele|होंठ नीले/i],
  ['sudden confusion', /confus|disoriented|ulti seedhi baat|भ्रम/i],
  ['bleeding in pregnancy', /(pregnan|garbh|गर्भ).{0,40}(bleed|khoon|खून)|(bleed|khoon|खून).{0,40}(pregnan|garbh|गर्भ)/i],
  ['thoughts of self-harm', /suicid|kill (my|him|her)self|end (my|his|her) life|marna chaht|jaan de d|आत्महत्या|मरना चाहत/i],
];
const NEG = /\b(no|not|never|without|nahi|nahin|nhi)\b|नहीं/i;
const CLAUSE = /[.,;!?\n|]|\b(?:and|but|aur|lekin)\b|और|लेकिन/i;

// A clause like "saans lene mein koi dikkat nahi" is a denial, not a red flag.
function redFlags(text) {
  const hits = new Set();
  for (const clause of text.split(CLAUSE)) {
    if (!clause?.trim()) continue;
    for (const [label, re] of RED_FLAGS) {
      const m = clause.match(re);
      if (m && !NEG.test(clause.replace(m[0], ' '))) hits.add(label);
    }
  }
  return [...hits];
}

const SYMPTOMS = [
  ['fever', /fever|bukhar|बुखार|temperature|taap/i],
  ['cough', /cough|kh?aa?nsi|खा[ंँ]सी/i],
  ['vomiting', /vomit|ulti|उल्टी/i],
  ['loose motions', /diarrh|loose motion|dast|दस्त/i],
  ['dizziness', /dizz|chakkar|चक्कर/i],
  ['weakness', /weak|kamzori|कमज़ोरी|कमजोरी/i],
  ['headache', /headache|sir ?dard|सिर ?दर्द/i],
  ['body ache', /body ?(pain|ache)|badan dard|बदन दर्द/i],
  ['joint pain', /knee|joint|ghutn|घुटन/i],
  ['back pain', /back pain|kamar dard|कमर दर्द/i],
  ['skin rash', /rash|itch|khujli|खुजली/i],
  ['sore throat', /sore throat|gala kharab|गला खराब|gale mein dard/i],
  ['wound care', /wound|dressing|ghav|घाव|patti|पट्टी|stitch|tanke|टांके|bedsore/i],
  ['injection or drip', /injection|drip|\biv\b|cannula|इंजेक्शन/i],
  ['blood sugar or BP check', /diabet|sugar|\bbp\b|blood pressure|thyroid/i],
];
const DURATION = /\d+\s*-?\s*\d*\s*(days?|din|दिन|hours?|ghante|घंटे|weeks?|hafte|हफ़्ते|हफ्ते|months?|mahine|महीने|years?|saal|साल)|\b(ek|do|teen|char|one|two|three|four|a|few|kai)\s+(days?|din|weeks?|hafte|months?|mahine)|since|today|yesterday|kal se|aaj se|आज से|कल से|दो दिन|तीन दिन|week|hafte|हफ़्ते/i;
const STATED_AGE = /(\d+)\s*-?\s*(years?|yrs?|months?|mahine|saal|साल|वर्ष|महीने)\s*-?\s*(old|ka|ki|ke|का|की|के)|\b(dada|dadi|nana|nani|grand(father|mother|pa|ma)|elderly|buzurg)\b|बुज़ुर्ग|बुजुर्ग/i;
const ELDER = /\b(dada|dadi|nana|nani|grand(father|mother|pa|ma)|elderly|buzurg)\b|बुज़ुर्ग|बुजुर्ग/i;
const LONG = /(\d+|few|kai|several|many|teen|char)\s*(months?|mahine|महीने|years?|saal se|साल से)|chronic|purana|पुराना|more than a week|hafte se zyada|हफ़्ते से ज़्यादा/i;
const SEVERE = /high fever|very high|tez bukhar|तेज़? बुखार|10[2-5]|bahut (tez|zyada)|severe|can'?t (eat|drink)|kuch kha nahi/i;
const YES = /\b(yes|haan|han|ha)\b|हाँ|हां/i;
const UNSURE = /not sure|pata nahi|पता नहीं|maybe|shayad|शायद/i;

function parseAge(text) {
  if (/under 3|se kam|से कम/i.test(text)) return 0.1;
  const m = text.match(/(\d+(?:\.\d+)?)\s*[-–]?\s*\d*\s*(months?|mahine|महीने|weeks?|years?|yrs?|saal|साल|वर्ष)/i);
  if (!m) return ELDER.test(text) ? 65 : null;
  const n = parseFloat(m[1]);
  return /month|mahine|महीने/i.test(m[2]) ? n / 12 : /week/i.test(m[2]) ? n / 52 : n;
}

const EXPLAIN = {
  en: {
    paramedic_home_visit: 'A paramedic should visit today to check on the patient at home.',
    nurse: 'A nurse can come home to take care of this.',
    gp_teleconsult: 'A doctor can help you over a video call.',
    specialist: 'This should be seen by a specialist doctor in the next few days.',
  },
  hi: {
    paramedic_home_visit: 'आज ही एक पैरामेडिक घर आकर मरीज़ की जाँच करे, यही सबसे सही रहेगा।',
    nurse: 'एक नर्स घर आकर इसकी देखभाल कर सकती हैं।',
    gp_teleconsult: 'डॉक्टर वीडियो कॉल पर आपकी मदद कर सकते हैं।',
    specialist: 'इसे अगले कुछ दिनों में किसी विशेषज्ञ डॉक्टर को दिखाइए।',
  },
  hinglish: {
    paramedic_home_visit: 'Aaj hi ek paramedic ghar aakar mareez ki jaanch kare, yahi sabse sahi rahega.',
    nurse: 'Ek nurse ghar aakar iski dekhbhaal kar sakti hain.',
    gp_teleconsult: 'Doctor video call par aapki madad kar sakte hain.',
    specialist: 'Ise agle kuch dinon mein kisi specialist doctor ko dikhaiye.',
  },
};
const RATIONALE = {
  paramedic_home_visit: 'a paramedic should check vitals at home today',
  nurse: 'this needs hands-on nursing care at home',
  gp_teleconsult: 'a doctor can assess this safely over video',
  specialist: 'a long-standing problem is best reviewed by a specialist',
};

// [id, name, role, care types, languages, rating, offset from user in km [north, east], free slots in hours from now]
// Placed relative to the user so distances look sensible wherever the demo runs.
const CAREGIVERS = [
  ['CG_014', 'Sunita Sharma', 'Paramedic', ['paramedic_home_visit'], ['hi', 'en'], 4.8, [1.2, 2.1], [0.75, 4, 27]],
  ['CG_022', 'Ravi Kumar', 'Paramedic', ['paramedic_home_visit', 'nurse'], ['hi'], 4.5, [-0.6, 0.9], [2, 6]],
  ['CG_031', 'Imran Qureshi', 'Paramedic', ['paramedic_home_visit'], ['hi', 'en', 'ur'], 4.9, [4.5, -3.2], [1.5, 9]],
  ['CG_007', 'Anjali Verma', 'Nurse', ['nurse'], ['hi', 'en'], 4.9, [0.8, -1.1], [3, 24]],
  ['CG_018', 'Mary Thomas', 'Nurse', ['nurse'], ['en', 'ml'], 4.7, [2.4, 1.5], [1, 5]],
  ['CG_026', 'Pooja Yadav', 'Nurse', ['nurse', 'paramedic_home_visit'], ['hi'], 4.4, [-2.2, 3.0], [0.5, 8]],
  ['CG_003', 'Dr. Neha Kapoor', 'General physician', ['gp_teleconsult'], ['hi', 'en'], 4.8, [6, 4], [0.5, 2, 5]],
  ['CG_011', 'Dr. Arjun Mehta', 'General physician', ['gp_teleconsult'], ['en', 'gu'], 4.6, [9, -5], [0.25, 3]],
  ['CG_019', 'Dr. Farah Khan', 'General physician', ['gp_teleconsult'], ['hi', 'en', 'ur'], 4.9, [3, 6], [4, 20]],
  ['CG_040', 'Dr. Vikram Rao', 'Orthopaedic specialist', ['specialist'], ['en', 'hi', 'te'], 4.7, [5, 2], [26, 50]],
  ['CG_044', 'Dr. Sneha Iyer', 'Dermatology specialist', ['specialist'], ['en', 'ta'], 4.6, [7, -2], [20, 44]],
  ['CG_047', 'Dr. Rajesh Gupta', 'Internal medicine specialist', ['specialist'], ['hi', 'en'], 4.8, [3.5, -4], [28, 70]],
];
const LANG_NAME = { hi: 'Hindi', en: 'English', ur: 'Urdu', ml: 'Malayalam', gu: 'Gujarati', te: 'Telugu', ta: 'Tamil' };
const WINDOW_H = { immediate: 1, same_day: 12, routine: 96 };
const W = { distance: 0.35, language: 0.25, rating: 0.2, availability: 0.2 }; // same weights as config.yaml

function slotAt(hours) {
  const d = new Date(Date.now() + hours * 36e5);
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  return d;
}

function match(careType, urgency, loc, lang) {
  const user = loc || { lat: 28.5402, lng: 77.27 };
  const want = lang === 'en' ? 'en' : 'hi';
  const win = WINDOW_H[urgency] || 24;
  const pool = CAREGIVERS.filter(c => c[3].includes(careType));
  const scored = pool.map(([id, name, role, , langs, rating, [n, e], slots]) => {
    const at = { lat: user.lat + n / 111, lng: user.lng + e / (111 * Math.cos(user.lat * Math.PI / 180)) };
    const km = haversineKm(user, at);
    const slot = slots.find(h => h <= win) ?? slots[0];
    const score = W.distance * (1 - Math.min(km, 15) / 15) + W.language * (langs.includes(want) ? 1 : 0)
      + W.rating * (rating - 3) / 2 + W.availability * Math.max(0, 1 - slot / win);
    const when = slotLabel(slotAt(slot), lang);
    const video = careType === 'gp_teleconsult';
    const langLabel = LANG_NAME[langs.includes(want) ? want : langs[0]];
    const reason = {
      en: `Speaks ${langLabel} · ${video ? 'video call' : `${km.toFixed(1)} km away`} · free ${when}`,
      hi: `${langLabel} बोलते हैं · ${video ? 'वीडियो कॉल' : `${km.toFixed(1)} km दूर`} · ${when} खाली`,
      hinglish: `${langLabel} bolte hain · ${video ? 'video call' : `${km.toFixed(1)} km door`} · ${when} free`,
    }[lang];
    return {
      caregiver_id: id, score: Math.round(score * 100) / 100, reason,
      name, role, distance_km: Math.round(km * 10) / 10, languages: langs.map(l => LANG_NAME[l]), rating,
      next_slot: slotAt(slot).toISOString(),
    };
  });
  return scored.sort((a, b) => b.score - a.score).slice(0, 3);
}

export async function mockTriage({ user_input, conversation_history = [], location }) {
  await sleep(700 + Math.random() * 600);
  const history = conversation_history;
  const userTurns = [...history.filter(m => m.role === 'user').map(m => m.content), user_input];
  const all = userTurns.join(' . ');
  const lang = detectLang(userTurns[0]);

  // Which of our questions were asked, and what was said right after each.
  const answers = {};
  history.forEach((m, i) => {
    const key = m.role === 'assistant' && Object.keys(Q).find(k => Object.values(Q[k]).includes(m.content));
    if (key) answers[key] = history[i + 1]?.content ?? user_input;
  });

  const decision = (care_type, urgency, red_flags, reasoning, explanation) =>
    ({ follow_up_question: null, care_type, urgency, red_flags, reasoning, explanation, matched_caregivers: [] });

  const flags = redFlags(all);
  const age = parseAge(answers.age || '') ?? parseAge(all.match(STATED_AGE)?.[0] || '');
  const told = userTurns.filter(u => u !== answers.age).join(' . '); // "60 years" is an age, not a duration
  const syms = SYMPTOMS.filter(([, re]) => re.test(told)).map(([s]) => s);
  if (age != null && age < 0.25 && syms.includes('fever')) flags.push('fever in a baby under 3 months');
  if (answers.screen && YES.test(answers.screen) && !NEG.test(answers.screen)) flags.push('breathing difficulty, chest pain or confusion reported');
  if (flags.length) {
    return decision('emergency', 'immediate', flags, `Red-flag sign reported: ${flags.join(', ')}. This needs emergency help now.`, t(lang, 'emSub'));
  }

  const ask = key => ({ follow_up_question: Q[key][lang], care_type: null, urgency: null, red_flags: [], reasoning: null, matched_caregivers: [] });
  const needsScreen = syms.some(s => ['fever', 'cough', 'vomiting', 'dizziness', 'weakness', 'headache', 'body ache'].includes(s));
  if (!answers.duration && !DURATION.test(told)) return ask('duration');
  if (!answers.age && !STATED_AGE.test(all)) return ask('age');
  if (!answers.screen && needsScreen) return ask('screen');

  const elderly = age != null && age >= 60;
  const child = age != null && age < 12;
  const unsure = answers.screen && UNSURE.test(answers.screen);
  let care, urgency;
  if (syms.includes('wound care') || syms.includes('injection or drip')) {
    care = 'nurse';
    urgency = /infect|pus|pas|मवाद|bleed|khoon|खून|swell|sooj/i.test(told) ? 'same_day' : 'routine';
  } else if (LONG.test(told) && !syms.includes('fever')) {
    care = 'specialist'; urgency = 'routine';
  } else if (unsure || (syms.some(s => ['fever', 'vomiting', 'loose motions', 'dizziness', 'weakness'].includes(s)) && (elderly || child || SEVERE.test(told)))) {
    care = 'paramedic_home_visit'; urgency = 'same_day'; // safety bias: unsure about warning signs -> someone checks in person
  } else {
    care = 'gp_teleconsult';
    urgency = syms.some(s => ['fever', 'vomiting', 'loose motions', 'headache'].includes(s)) ? 'same_day' : 'routine';
  }

  const who = elderly ? ' in an older adult' : child ? ' in a child' : '';
  const what = syms.length ? syms.join(', ').replace(/^./, c => c.toUpperCase()) : 'The reported problem';
  const screened = answers.screen ? (unsure ? '; warning signs could not be ruled out' : '; no breathing difficulty, chest pain or confusion reported') : '';
  const res = decision(care, urgency, [], `${what}${who}${screened}, so ${RATIONALE[care]}.`, EXPLAIN[lang][care]);
  res.matched_caregivers = match(care, urgency, location, lang);
  return res;
}
