# MedGemma fine-tune: plan, honest odds, and how to start

## Short answer

- **Yes, a task-specific dataset + Unsloth QLoRA is the right way to improve MedGemma.** Today's base model needs
  7 worked examples in its prompt to be usable at all, and it is still the weakest and slowest part of the classifier.
- **It will not move the dataset score much.** The full pipeline already gets macro-F1 1.0 and 56/56 emergencies on the
  organiser data, and Qwen carries most of that. If the hidden test set looks like the released one, the ceiling is
  already reached.
- **Where a fine-tune does pay off:** robustness to paraphrases and Hindi phrasing the templates never show; a second
  vote you can actually trust (so MedGemma can break ties instead of only raising alarms); a shorter prompt and faster
  replies (response time is a tie-break); and a pitch: *"a 4B medical model, fine-tuned, running offline on a 6 GB
  laptop GPU"* fits a healthcare-access track.
- **Train on Kaggle (free T4, 16 GB), not on the laptop.** Reasons are below.
- **Time-box it to ~3 hours.** Ship it only if it clears the gates in section 6.

## 1. What MedGemma does today

Production path (`app/triage.py`): the red-flag rules run first, then Qwen turns the conversation into English `CaseFacts`,
then MedGemma and Qwen each produce a `Decision` from the facts alone. Either one saying emergency means emergency;
otherwise the more urgent answer wins; ties go to Qwen (`tie_break: second_opinion`).
MedGemma never sees raw Hindi or Hinglish: on raw text it caught only 5/11 emergencies.

| Setup (30 trilingual cases, `eval/bench_v2.py`) | Valid JSON | Care macro-F1 | Emergencies | Under-triaged | Needless follow-ups | Median |
|---|---|---|---|---|---|---|
| MedGemma 4B, no examples | 26/30 | 0.47 | 10/11 | 6 | 15/16 | 5.4 s |
| MedGemma 4B + examples (production today) | 27/30 | 0.85 | 11/11 | 1 | 9/16 | 4.8 s |
| Qwen 3.8 27B (Cerebras) | 30/30 | 0.93 | 11/11 | 2 | 6/19 | 0.45 s |
| Pair, as wired | | 0.93 | 11/11 | 0 | | |

The two numbers that matter: the pair's **0 under-triaged** comes from MedGemma's extra emergency votes, and
MedGemma's **4.8 s is the slowest step in a dataset-mode turn**, because the pipeline waits for both votes.

The held-out baseline on the new split (`training/eval_finetune.py`) is in section 6.

## 2. What the fine-tune should and should not try to do

**Train the exact production task:** `Patient facts: {CaseFacts JSON}` → `Decision` JSON, with the system prompt set to
`triage.RULES` (the taxonomy rules without the 7 examples). The examples get baked into the weights, the prompt halves,
and `config.yaml → classifier.prompt: rules_only` switches the app over. The training prompt is imported from
`app/triage.py`, so the two can never drift apart.

**Don't:**
- train on raw user text (that is Qwen's job, and MedGemma was bad at it);
- train it to diagnose or name diseases (the reasoning sentences in the data are routing reasons only);
- teach it anything that under-triages (section 3 filters those rows out).

## 3. The dataset (`training/build_sft.py`, already built)

| Stage | What happens | Output |
|---|---|---|
| generate | Two generators, **Qwen** and **gpt-oss-120b**, so the messages are not one model's style. They write requests for 50 clear scenarios plus **18 gray-zone scenarios with no intended answer** (sleepy elderly parent, 103°F in a toddler, sugar above 400, fainted but now talking...) × 3 languages × several of 5 styles (WhatsApp, voice-note ramble, polite with age, negations, two-turn vague then detail). Adds the organiser's 66 distinct messages with their gold labels. | `sft/requests.jsonl` |
| facts | The **real structurer** (`app/intake.py`, Qwen, exactly as in production) turns each request into `CaseFacts`, so training inputs look like production inputs, noise included. Teachers see these facts only: a label that depends on details the facts lost would teach MedGemma to guess. | `sft/facts.jsonl` |
| label | **Teacher panel, in parallel:** gpt-oss-120b (Cerebras) + **gpt-5.4-mini** (OpenAI) + Qwen. All agree → kept. Any split → **gpt-5.4** adjudicates with every vote in view. It may not overrule an emergency vote on its own; those rows go to human review. Qwen only votes, so it can trigger adjudication but never decides alone. Organiser rows keep the organiser label (the panel's view is recorded). **OpenAI spend is metered from real token usage and capped at $3.75 across all runs** (`sft/spend.json`). Past the cap, mini leaves the panel and gpt-oss-120b at high reasoning adjudicates. | `sft/labelled.jsonl`, `sft/review.jsonl` |
| export | Dedupe identical facts, then split by generation batch (paraphrase siblings never straddle train and test), stratified by the final label: 75 / 10 / 15. `stats.json` reports each model's agreement with the final labels: how often the panel corrected Qwen is the "beyond Qwen" number. | `sft/train|val|test.jsonl`, `sft/stats.json` |

Pilot (40 rows): 38 unanimous, 1 adjudicated. That one was a rambling post-discharge message: Qwen and gpt-oss said home visit,
mini said emergency, gpt-5.4 ruled emergency. That is the kind of label Qwen alone would get wrong. Cost: ≈ $0.0011 per row
for mini, ≈ $0.009 per adjudication. (SambaNova's key returns 401 on chat, so DeepSeek is not on the panel. Fix the key to add a
third model family.)

Re-run any stage on its own; each one resumes. `python training/build_sft.py export` re-splits without calling any model.

**What the smoke run already caught (useful even if you never train):**
- Hindi negation over a list (`… साँस लेने में तकलीफ, या त्वचा पर … नहीं हैं`, `… बेहोशी, या सीने में दर्द नहीं है`)
  trips the raw-text red-flag rules: false emergencies. See the `raw-text rules fire on a non-emergency` rows in `review.jsonl`.
- Hindi head-injury and confusion paraphrases (`सिर से गिरकर चोट`, `भ्रमित हो गए और बोलना भूल गए`) slip past the raw-text
  rules; the English-facts pass is what catches them. `stats.json → emergency_paraphrases_rules_missed` counts these.
- An elderly father "falling asleep mid-sentence, can't keep food down" was written as a home visit and Qwen agreed;
  gpt-5.5, gpt-5.6-sol, gpt-oss-120b and Gemini all said emergency. That case is why Qwen is no longer the teacher.

**Still be upfront about it:** synthetic data labelled by models is only as good as the panel. The unbiased checks are (a) the
organiser rows in `test.jsonl` and (b) a small **human-written hard set**: 40–60 messages your team types by hand, especially
Hindi and Hinglish, labelled by you. Write them before you look at any fine-tune output. That set also makes a strong slide.

## 4. Where to train

| | Kaggle / Colab T4 (recommended) | Laptop RTX 4050, 6 GB |
|---|---|---|
| Fits? | Easily: 4-bit 4B model + LoRA ≈ 6–8 GB of 16 GB | Maybe, at batch 1, sequence length 1024, gradient checkpointing; Ollama has to be stopped first (MedGemma holds ~3.3 GB) |
| Setup | `pip install unsloth`, done | Unsloth on native Windows needs triton-windows, a matching CUDA toolkit and VS build tools; WSL2 is more reliable. Either way you risk breaking the CUDA setup on your **demo machine** mid-hackathon |
| Speed | ~20–40 min for ~1.2k rows × 2 epochs | Similar or slower, and the laptop is unusable meanwhile |
| Getting the model back | Download a ~2.5 GB GGUF, or push it to a Hugging Face repo and `ollama pull hf.co/<you>/<repo>:Q4_K_M` | Already local |

Use Kaggle (30 free GPU hours a week). Train on the laptop only if Kaggle is down, and then from WSL2.

## 5. Training recipe (`training/medgemma_lora.ipynb`)

- Base: `unsloth/medgemma-4b-it` (not gated; HAI-DEF terms apply to the fine-tune too), `FastModel`, 4-bit QLoRA.
- LoRA on language layers only (attention + MLP), `r=16, alpha=16, dropout=0`. The vision tower is never used.
- Gemma 3 chat template; loss on the model's reply only (`train_on_responses_only`).
- Batch 4 × grad-accum 4, lr 2e-4, 2 epochs, eval every 30 steps, keep the best checkpoint by val loss.
- A greedy-decoding check on `test.jsonl` inside the notebook catches a broken run before export.
- Export: GGUF **Q4_K_M**, the same quantisation as today's base, so the comparison is fair. Save the LoRA adapter
  first as a fallback. API names that change between releases are marked VERIFY in the notebook.

**If the first run underfits** (val loss flat, test accuracy below base + examples): 3 epochs, or `r=32`. **If it
overfits** (train loss → 0, val rising): 1 epoch, or more data via `--per-call 8`. Don't tune on the test split.

## 6. Evaluation and the gates for swapping it in

```
python training/eval_finetune.py --prompt few_shot --pair                                  # base + examples (today)
python training/eval_finetune.py --prompt rules_only                                       # base without examples
python training/eval_finetune.py --model carecompass-medgemma --prompt rules_only --pair   # the fine-tune
python training/eval_finetune.py --qwen                                                    # reference
```

Swap the fine-tune into `config.yaml` only if **all** of these hold on `test.jsonl` and on the hand-written hard set:

1. Emergency recall stays at 100%, and under-triage in the **pair** stays at 0.
2. Care macro-F1 at or above base + examples (target: within 0.03 of Qwen).
3. Valid JSON on every row.
4. Median latency no worse than today (target < 3 s).
5. `eval/eval_dataset.py` still gives 1.0 / 56/56 with the new model wired in.

If it clears the gates **and** beats Qwen on the hard set, consider `tie_break: medgemma`. Otherwise keep Qwen as the
tie-breaker and use MedGemma as the faster, stronger safety vote. That is still a win worth reporting.

**Baseline on the held-out split** (fill in after training):

| Model | Prompt | Valid | Care F1 | Emergencies | Under-triaged | Median | Organiser rows |
|---|---|---|---|---|---|---|---|
| base | few_shot | | | | | | |
| base | rules_only | | | | | | |
| fine-tune | rules_only | | | | | | |
| Qwen | few_shot | | | | | — | |

## 7. Wiring it back

1. Put `medgemma-triage.Q4_K_M.gguf` in `models/` (git-ignored).
2. `ollama create carecompass-medgemma -f models/Modelfile.medgemma-triage` (the template is copied from the base model).
3. `config.yaml`: `classifier.model: carecompass-medgemma`, `classifier.prompt: rules_only`.
4. Rerun `eval/eval_dataset.py` and `eval/eval_smart.py --e2e`. Smart mode still sends MedGemma its own few-shot
   prompt (`app/smart.py`). Check it there too: a narrow fine-tune can get worse at the other task.

## 8. Phase 2 (only if time is left): smart mode

MedGemma also runs the smart-mode seriousness analyser (`SmartDecision`: seriousness, specialty, first tests). Same recipe:
add a `--task smart` target to `build_sft.py` that labels with `smart.SYSTEM`, and train one multi-task LoRA on both
system prompts. This is harder to label well (specialty and test choices are softer), so do it after phase 1 has shipped.

## 9. Timeline

| Step | Time |
|---|---|
| Dataset build (done in this thread; OpenAI budget ≤ $3.75, training itself uses no OpenAI) | ~45 min of API calls |
| Write the 40–60 hand-labelled hard cases | 30 min (whole team) |
| Kaggle run: install, train, in-notebook check | 45–60 min |
| Export + download/pull GGUF + `ollama create` | 30–45 min |
| Eval, gates, decision, write-up numbers | 30 min |

## 10. Prompt for the new thread

> We're fine-tuning MedGemma 4B for CareCompass's triage classifier. Read `training/FINETUNE_PLAN.md` first, then
> `app/triage.py` (RULES, combine), `training/build_sft.py` and `training/medgemma_lora.ipynb`. The dataset is already in
> `training/sft/` (train/val/test.jsonl, stats.json, review.jsonl). Steps: (1) look over `review.jsonl` and a sample of
> `train.jsonl` for bad labels; (2) help me write a 50-case hand-labelled hard set at `training/sft/hard.jsonl` in the same
> format; (3) run the baselines with `training/eval_finetune.py`; (4) walk me through the Kaggle notebook and fix any
> Unsloth/TRL API changes (VERIFY lines); (5) once I have the GGUF, create the Ollama model, run the gates in section 6,
> and switch `config.yaml` only if every gate passes. Never add Claude attribution to commits.
