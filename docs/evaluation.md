# Evaluation: every claim and how to reproduce it

Each number in the deck and in [architecture.md](architecture.md) carries a reference [n] that maps to a row below.
Offline checks need nothing but the repository; the screener needs MedGemma in Ollama; benchmark and conversation suites
call the model APIs.

## Claims, data and scripts

| Ref | Claim | Data | Method | Reproduce | Result file |
|---|---|---|---|---|---|
| [1] | Model choice: Qwen 0.45 s median per call vs a frontier model 1.81 s; care-type macro-F1 0.928 vs 0.961; 11/11 emergencies; follow-ups in the right script 17/17 at 0.38 s | 30 hand-written cases: 10 English, 10 Hinglish, 10 Devanagari; 11 emergencies, 7 denials | Same facts to every model; macro-F1, emergencies caught, median wall-clock per call | `python eval/bench_v2.py structure\|classify\|phrase` | `eval/results/v2_structure.json`, `v2_classify.json`, `v2_phrase.json` |
| [2] | Safety chain on held-out cases: rules + Qwen 66/77; + graded screen 72/77 (1/56 false alarms); + retrieval 74/77 (2/56) | 133 held-out cases (77 emergencies), split by generation batch; never trained on, never retrieved, not in prompt examples | Union of rule, Qwen and MedGemma votes | `python eval/eval_screener.py` | `eval/results/screener.json`; commits 805548e, f3e11bb |
| [3] | MedGemma format probe: 37/77 picking care labels vs 73/77 as a screener (14/56 false alarms); median screen call 6.5 s | The same 133 cases | Same facts asked four ways, local Ollama | `python training/medgemma_probe.py` | `eval/results/medgemma_probe.json` |
| [4] | Red-flag rules: 85/85 checks; 444/639 dataset emergencies by rules alone; 0/1,052 false alarms; 0.9 ms median | 1,691 labelled cases with recall-first labels; 1,666 texts for timing | Recall and false alarms; `time.perf_counter` on the laptop CPU | `python eval/test_redflags.py`, `python eval/time_redflags.py` | console output |
| [5] | Organiser data: 56/56 emergencies, 0 false emergencies, care macro-F1 0.955, urgency 0.962; top-3 overlap 195/198 | 340 organiser rows, 60-person roster | Full pipeline on every row | `python eval/eval_dataset.py` | `eval/results/dataset_eval.json`, `predictions.csv` |
| [6] | 47 scripted conversations; a capped 22-check live run including 12/12 organiser sample messages | Adversarial, multilingual, emergency conversations | Expected outcome per conversation | `python eval/eval_conversations.py`, `python eval/smoke_budget.py` | console output |
| [7] | Emergency card in about 1 s; full decision turns 9–13 s on the laptop | 30 LLM role-played sessions on the live pipeline | Wall-clock seconds logged per reply | `python eval/replay_sessions.py` | `eval/results/sessions/qa_*.json` |
| [8] | Smart-mode picks shift with the user's words and the kind of need | 186-provider directory | Curated queries through the real matcher, fixed clock Sun 25 Oct 2026 11:00, no model calls | `python eval/curated_queries.py` | console output |
| [9] | Pilot ₹2.02 per voice conversation, ₹0.87 text; frontier ₹3.32; 39% cheaper at pilot scale, about 60% self-hosted | Token counts measured on a 3-turn case; list prices checked 24 Oct 2026; ₹88 per US$; GST excluded | Per-call price × calls per turn | — | [Model Selection Rationale](https://claude.ai/artifact/JY6yW4KHZ4K37TVbZKzBUZ), Economics |
| [10] | Self-hosting: two L40S GPUs at about ₹102/hour; break-even about 2 lakh conversations a month; models ₹0.17–0.22 per conversation | E2E Networks pricing; a public vLLM throughput benchmark of Qwen 3.8 27B | Planning estimate, to be confirmed by a load test | — | Model Selection Rationale, "API or self-hosting" |
| [11] | Data | Sanocare synthetic care-triage dataset v1 (organisers); our synthetic set of 1,715 cases | — | `python training/build_sft.py export` | `training/sft/` |

## How the labels were made

1,715 synthetic cases from 71 scenarios, three languages and five writing styles went through the production intake
analyst, so every label is on the same noisy facts the app sees. Three models voted on each row, a stronger model settled
splits, and a recall-first review by hand relabelled risky gray-zone cases (for example sudden drowsiness in an elderly
person) to emergency. Splits are a stable hash of the generation batch, so paraphrases never straddle train and test.
Details: [medgemma-finetune.md](medgemma-finetune.md) and the
[MedGemma Dataset & Training Strategy](https://claude.ai/artifact/9YcKrftBNp5bK3UqyfAayN) doc.

## Limits

- The 30-case benchmark and the held-out labels are ours, written recall-first, not doctors' labels.
- Voice was tested on synthetic speech and role-play, not real callers on noisy phones.
- Self-hosting capacity is an estimate from a public benchmark, not a load test.
