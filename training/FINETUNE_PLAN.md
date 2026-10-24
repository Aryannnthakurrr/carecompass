# MedGemma fine-tune: runbook and status

Shareable write-up with all the numbers: [MedGemma Dataset & Training Strategy](https://claude.ai/artifact/9YcKrftBNp5bK3UqyfAayN).
This file is the repo-side runbook.

## Goal

Miss as few emergencies as possible: over-triage is acceptable, under-triage is not. Make MedGemma the strongest
part of the classifier. Today base MedGemma catches 37 of 73 held-out emergencies, and each one it catches, the
red-flag rules or Qwen catch already (rules 52, + Qwen 66, + MedGemma 66).

## The task MedGemma learns

The exact production call (`app/triage.py`): `Patient facts: {CaseFacts JSON}` in, `Decision` JSON out
(`status, care_type, red_flags, missing_info, reasoning`). The system prompt is `triage.RULES`, the rules without the
worked examples. MedGemma never sees the patient's own words; Qwen's structurer writes the facts.
`config.yaml → classifier.prompt: rules_only` switches the app to the fine-tune's prompt.

## Pipeline (all steps resume; outputs in `training/sft/`)

| Step | Command | What it does |
|---|---|---|
| Generate | `python training/build_sft.py generate [--generator gpt-oss-120b]` | Synthetic trilingual requests, 71 scenarios incl. 18 gray zones |
| Facts | `python training/build_sft.py facts` | The production structurer (Qwen) on every request |
| Label | `python training/build_sft.py label` | Panel gpt-oss-120b + gpt-5.4-mini + Qwen; splits → gpt-5.4 (or gpt-oss-120b high past the OpenAI cap) |
| Manual review | `python training/manual_review.py` | Recall-first relabels (106 → emergency) + 17 recovered review rows |
| Follow-ups | `python training/manual_followups.py` | 34 hand-written "ask a follow-up" cases |
| Export | `python training/build_sft.py export` | Dedupe, stable hash split by batch (15% test, 10% val), chat JSONL |
| Train | `python training/kaggle_run.py all` | Private Kaggle dataset → notebook on a T4 → wait → metrics → GGUF → `ollama create carecompass-triage` → local eval → gates |
| Score | `python training/eval_finetune.py --model <tag> --prompt rules_only [--split test\|test_v2\|hard]` | Accuracy, macro-F1, emergency recall, under-triage, latency |
| Probe | `python training/medgemma_probe.py` | Same facts asked four ways (JSON or clinical note; decide-first, reason-first, or emergency-only) |

**Bulk-job controls.** `build_sft.py` stops on the first quota error, caps requests per provider
(`--max-calls-cerebras`, `--max-calls-openai`, default 1,500 each), meters OpenAI usage cumulatively in `sft/spend.json`
(`--budget`, default $3.75), and uses a separate `CEREBRAS_TRAINING_API_KEY` from `.env` if set, so dataset jobs and the
live app run on separate keys.

## Data (24 Oct)

1,715 rows after dedupe: train 1,326 / val 129 / test 260; 651 emergencies. `test_v2.jsonl` is the earlier split
the deleted v2 run was trained against. Label sources: 1,393 unanimous, 166 adjudicated, 122 manual, 34 hand-written.

## Kaggle notes

- GPUs need a phone-verified account; the notebook asserts a T4 (compute capability 7.0 or newer) before doing anything.
- Gemma 3 trains in float32 on a T4: 160 steps take about 1–2 hours.
- Unsloth exports only F16/BF16/Q8_0 for Gemma 3, so the notebook quantises F16 → Q4_K_M with llama.cpp (Q8_0 fallback).
- Single notebook versions can't be deleted, so each round gets its own notebook: `kaggle_run.py` `TAG = "v3"`.
- The notebook source is generated: edit `training/make_notebook.py`, then `python training/make_notebook.py training/medgemma_lora.ipynb`.

## Runs

| Run | Data | Outcome |
|---|---|---|
| v1 | Panel labels | No GPU (account not phone-verified) |
| v2 | Panel labels | Stopped and deleted: labels too lenient on gray zones |
| v3 | Recall-first labels | Training (`carecompass-medgemma-v3`) |

## Ship gates (on `test.jsonl`)

1. Emergency recall at least that of base MedGemma, target close to 100%; the pair (MedGemma + Qwen) under-triages less than today.
2. Care macro-F1 at or above base + examples.
3. Valid JSON on every row; median latency no worse than today's 4.2 s.
4. `eval/eval_dataset.py` still macro-F1 1.0 and 56/56; `eval/eval_smart.py --e2e` no regression; `eval/test_redflags.py` passes.

If they pass: `config.yaml` → `classifier.model: carecompass-triage`, `classifier.prompt: rules_only`.

## Probe so far (base model, recall-first labels)

| Variant | Emergencies caught | False alarms | Invalid |
|---|---|---|---|
| Today's setup (JSON, decide first) | 37 / 73 (old labels) | 0 | 4 / 254 |
| Clinical note, emergency screener | 73 / 77 | 14 / 56 | 5 / 133 (replies cut off) |
| JSON, reason first | running | | |
| Clinical note, reason first | running | | |

If the screener holds up, the next step is a design change: MedGemma becomes the dedicated emergency screener beside
Qwen (either one saying emergency still wins), and it is fine-tuned in that format (v4).
