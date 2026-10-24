# Start Here — The Task in Plain Words

*A quick, no-jargon guide for participants and mentors. Read this first (5 minutes).*

---

## The real-life problem

Families message Sanocare on WhatsApp when someone at home is unwell. They write the way people
actually text — in Hindi, English, or a mix ("Hinglish"):

> *"papa ko do din se bukhar hai aur khansi bhi"*
> (Dad has had a fever for two days, and a cough too.)

A human care coordinator would read that and quickly figure out four things:

1. **What kind of help is needed?** A nurse visit at home? A lab test? A video call with a doctor?
2. **How urgent is it?** Is this an emergency, something for today, or something that can be scheduled?
3. **Is this an emergency we must not handle at home?** (e.g. chest pain → send them to call an ambulance, right away.)
4. **Who should we send?** Which available caregiver nearby is the best fit — speaks their language, close enough, has the right skills, free now?

**Your job is to build the software "coordinator" that does this automatically.** Given one message
(and where the person is), your program should reply with those decisions. And if the message is too
vague to decide, it should ask **one polite follow-up question** instead of guessing.

---

## What goes in, what comes out

**Goes in:** the person's message + their location (and the chat so far, if any).

**Comes out, one of two things:**

- **A decision** — the type of care, how urgent, any emergency warnings, a short reason, and a short
  list of the best-matched caregivers.
- **Or a follow-up question** — when there isn't enough to decide yet.

That's the whole game. Everything in the dataset is built around these two outcomes.

---

## What we've given you (the files)

Think of it as **"questions with model answers" + "a list of caregivers to choose from."**

| File | In plain words |
|---|---|
| **`triage_dataset.jsonl`** | 340 example messages, each with the "right answer" already filled in — the care type, urgency, any emergency flags, and which caregivers to send. This is your main material to learn from and test against. |
| **`caregivers.csv`** | A list of 60 caregivers (home nurses, doctors, sample-collectors) — where they are, what languages they speak, what they can do, and when they're free. This is who your program picks from. |
| **`triage_dataset_flat.csv`** | The exact same 340 examples, but laid out as a simple spreadsheet so you can open it in Excel/Google Sheets and just *look* at it. |
| **`multi_turn_examples.jsonl`** | Two short sample chats showing how a vague message turns into a follow-up question and then a decision. |
| **`README.md`** | The precise, technical version of everything here (field names, exact formats). Use it when you're coding. |

> **Important:** this data is **made up** for the hackathon — no real patients or real staff. It's safe
> to use freely. It's also **not** real medical advice; don't use it to make actual health decisions.

---

## What a "good" answer looks like

- **Gets the care type and urgency right** most of the time, even when the message is in Hinglish or Hindi.
- **Never misses an emergency.** If someone describes chest pain, trouble breathing, collapse, heavy
  bleeding, etc., the safe answer is always: flag it as an emergency and point them to call 112 —
  *not* a home visit. Missing one of these is the worst mistake; a false alarm is okay by comparison.
- **Picks sensible caregivers** — someone who speaks the family's language, is close by, is free, and
  has the right skill. (We've included a simple scoring recipe in the README you can match or beat.)
- **Knows when to ask.** If the message is just "I need help", a good system asks one clear question
  rather than guessing.

---

## Three ways to start (pick your comfort level)

1. **Just look first.** Open `triage_dataset_flat.csv` and `caregivers.csv` in a spreadsheet. Read 20–30
   rows. You'll understand the task faster than any explanation.
2. **Start simple.** You don't need fancy AI on day one — even sensible rules ("if the message mentions
   'injection' or 'dressing' → home nursing") get you a working first version. Improve from there.
3. **Go end-to-end.** Take a message → decide care type + urgency → check for emergencies → pick the
   best caregivers → return the answer. Then test your answers against the "right answers" we provided.

---

## For mentors — how to judge it

Good projects usually do well on four simple questions:
- **Does it understand messy, real-language input** (Hindi / English / Hinglish), not just clean English?
- **Is it safe** — does it reliably catch the emergencies?
- **Are its care-type and urgency calls sensible** on examples it hasn't seen?
- **Are its caregiver picks reasonable** — right language, nearby, available, right skill?

Bonus marks for teams that handle the "ask a follow-up question" case gracefully, and that can explain
*why* their system made each decision in plain words.

---

*Questions about the dataset during the event? It's synthetic and self-contained — everything you need
is in these five files. Build something people would actually trust with their family's health.*
