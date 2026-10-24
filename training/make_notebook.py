"""Generates training/medgemma_lora.ipynb: python training/make_notebook.py training/medgemma_lora.ipynb"""
import json
import sys

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(keepends=True)})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": s.strip("\n").splitlines(keepends=True)})


md("""
# CareCompass: MedGemma 4B LoRA for the triage classifier

Teaches MedGemma the exact production task: **CaseFacts JSON in, Decision JSON out** (`app/triage.py`).
Data comes from `training/build_sft.py` (`train.jsonl`, `val.jsonl`, `test.jsonl`; chat format, system prompt = `triage.RULES`).

**Kaggle setup:** Settings -> Accelerator **GPU T4 x1** (or P100), Internet **on**. Add the three JSONL files as a Kaggle Dataset
(e.g. `carecompass-sft`). Colab: upload them next to the notebook. Training takes roughly 20-40 min on ~1.2k rows.

Lines marked **VERIFY** use library APIs that change often (Unsloth / TRL). If a cell fails, compare it with Unsloth's current
Gemma 3 notebook: MedGemma 4B is a Gemma 3 4B fine-tune, so their Gemma 3 recipe applies.

MedGemma is under Google's Health AI Developer Foundations terms; the fine-tune inherits them.
""")
code("""
# Parameters. training/kaggle_run.py rewrites these two lines for each run (MedGemma vs plain Gemma 3).
MODEL_NAME = "unsloth/medgemma-4b-it"   # VERIFY name; not gated on HF. Control: "unsloth/gemma-3-4b-it"
RUN = "medgemma"
EXPORT_GGUF = True
""")
code("""
import torch
cap = torch.cuda.get_device_capability(0) if torch.cuda.is_available() else (0, 0)
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU", cap)
assert cap >= (7, 0), "Unsloth needs a T4 or newer (compute capability 7.0+). On Kaggle pick Accelerator: GPU T4 x2, not P100."
""")
code("""
%%capture
# Unsloth's own Gemma 3 notebook (checked 24 Oct 2026) installs unsloth, then pins these two. VERIFY if this cell fails.
!pip install -q unsloth
!pip install -q transformers==4.57.6
!pip install -q --no-deps trl==0.22.2
""")
code("""
import glob, json, os
hits = glob.glob("/kaggle/input/**/train.jsonl", recursive=True) or glob.glob("train.jsonl")
DATA = os.path.dirname(hits[0]) or "."
print("data in", DATA, {f: sum(1 for _ in open(f"{DATA}/{f}.jsonl", encoding="utf-8")) for f in ("train", "val", "test")})
""")
md("""
## 1. Model + LoRA
Language layers only: the vision tower is never used (MedGemma only ever sees text facts).
`r=16` is plenty for a narrow 6-label task; raise it to 32 only if val loss plateaus high.
""")
code("""
from unsloth import FastModel
import torch

model, tokenizer = FastModel.from_pretrained(
    model_name=MODEL_NAME,
    max_seq_length=2048,
    load_in_4bit=True,                      # QLoRA: fits a T4 (16 GB) easily
    full_finetuning=False,
)
model = FastModel.get_peft_model(
    model,
    finetune_vision_layers=False, finetune_language_layers=True,
    finetune_attention_modules=True, finetune_mlp_modules=True,
    r=16, lora_alpha=16, lora_dropout=0, bias="none", random_state=3407,
)
""")
md("""
## 2. Data -> Gemma 3 chat text
The Gemma 3 template folds the system prompt into the first user turn, which is what Ollama's MedGemma template does too.
""")
code("""
from datasets import load_dataset
from unsloth.chat_templates import get_chat_template, train_on_responses_only

tokenizer = get_chat_template(tokenizer, chat_template="gemma-3")
ds = load_dataset("json", data_files={"train": f"{DATA}/train.jsonl", "val": f"{DATA}/val.jsonl"})

def to_text(batch):
    return {"text": [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False).removeprefix("<bos>")
                     for m in batch["messages"]]}

ds = ds.map(to_text, batched=True, remove_columns=ds["train"].column_names)
print(ds["train"][0]["text"][-700:])
lens = [len(tokenizer(text=t)["input_ids"]) for t in ds["train"]["text"][:200]]
print("tokens per example: max", max(lens), "mean", sum(lens) // len(lens))
""")
md("""
## 3. Train (loss on the model's answer only)
""")
code("""
from trl import SFTTrainer, SFTConfig

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,                    # VERIFY: newer TRL calls this processing_class
    train_dataset=ds["train"],
    eval_dataset=ds["val"],
    args=SFTConfig(
        dataset_text_field="text",
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,      # effective batch 16
        num_train_epochs=2,                 # ~1.2k rows -> ~150 steps; the best checkpoint by val loss is kept
        learning_rate=2e-4,
        warmup_steps=10,
        lr_scheduler_type="linear",
        optim="adamw_8bit",
        weight_decay=0.01,
        logging_steps=10,
        eval_strategy="steps", eval_steps=30,
        save_strategy="steps", save_steps=30, save_total_limit=3,
        load_best_model_at_end=True, metric_for_best_model="eval_loss",
        seed=3407, output_dir="outputs", report_to="none",
    ),
)
trainer = train_on_responses_only(trainer, instruction_part="<start_of_turn>user\\n", response_part="<start_of_turn>model\\n")

# sanity: only the JSON answer should be unmasked
ex = trainer.train_dataset[0]
print(tokenizer.decode([t for t, l in zip(ex["input_ids"], ex["labels"]) if l != -100]))
""")
code("""
stats = trainer.train()
print(stats)
""")
md("""
## 4. Quick held-out check before exporting
Greedy decoding, like production (temperature 0). The real comparison runs locally with `training/eval_finetune.py`
against the quantised GGUF; this only catches a broken run before you spend time on export.
""")
code("""
FastModel.for_inference(model)
test = [json.loads(l) for l in open(f"{DATA}/test.jsonl", encoding="utf-8")]
ok = valid = em = em_hit = 0
misses = []
for r in test:
    inputs = tokenizer.apply_chat_template(r["messages"][:2], add_generation_prompt=True, tokenize=True,
                                           return_dict=True, return_tensors="pt").to("cuda")
    out = model.generate(**inputs, max_new_tokens=200, do_sample=False)
    text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    try:
        d = json.loads(text[text.find("{"): text.rfind("}") + 1]); valid += 1
        got = "need_more_info" if d.get("status") == "need_more_info" else d.get("care_type")
    except Exception:
        got = "INVALID"
    ok += got == r["target"]
    if r["target"] == "emergency_112":
        em += 1; em_hit += got == "emergency_112"
    if got != r["target"]:
        misses.append({"gold": r["target"], "pred": got, "facts": r["messages"][1]["content"][15:]})
        print("MISS", r["target"], "->", got, "|", r["messages"][1]["content"][15:140])
print(f"accuracy {ok}/{len(test)}  valid JSON {valid}/{len(test)}  emergency recall {em_hit}/{em}")
json.dump({"run": RUN, "model": MODEL_NAME, "rows": len(test), "accuracy": ok / len(test), "valid": valid,
           "emergency_recall": f"{em_hit}/{em}", "train_loss": stats.training_loss, "misses": misses}, open(f"metrics_{RUN}.json", "w"), indent=1)
""")
md("""
## 5. Save
Always save the LoRA adapter first (small, ~100-150 MB): it is the fallback if GGUF export fails.
""")
code("""
model.save_pretrained(f"{RUN}_triage_lora")
tokenizer.save_pretrained(f"{RUN}_triage_lora")
import shutil
shutil.make_archive(f"{RUN}_triage_lora", "zip", f"{RUN}_triage_lora")
shutil.rmtree(f"{RUN}_triage_lora")   # keep only the zip in the output
!ls -lh *.zip
""")
md("""
### GGUF Q4_K_M for Ollama (same quantisation as today's base, so the comparison is fair)
For Gemma 3 models Unsloth's exporter writes only F16, BF16 or Q8_0, so we export F16 to `/tmp` (the merged model plus
F16 GGUF would nearly fill Kaggle's 20 GB working dir) and quantise to Q4_K_M with llama.cpp. If that fails: Q8_0.
Only the text-model `.gguf` (~2.5 GB) is kept; the vision projector (`mmproj`) is not needed.
""")
code("""
import glob, os, subprocess
if EXPORT_GGUF:
    try:
        model.save_pretrained_gguf(f"/tmp/{RUN}_gguf", tokenizer, quantization_method="F16")
        src = [g for g in glob.glob(f"/tmp/{RUN}_gguf*/**/*.gguf", recursive=True) + glob.glob(f"/tmp/{RUN}_gguf*.gguf")
               if "mmproj" not in g.lower()]
        print("exported:", src)
        q = next((p for p in ("llama.cpp/llama-quantize", "llama.cpp/build/bin/llama-quantize") if os.path.exists(p)), None)
        if q is None:
            subprocess.run("git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp && "
                           "cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF && "
                           "cmake --build /tmp/llama.cpp/build -j 4 --target llama-quantize", shell=True, check=True)
            q = "/tmp/llama.cpp/build/bin/llama-quantize"
        subprocess.run([q, src[0], f"{RUN}-triage.Q4_K_M.gguf", "Q4_K_M"], check=True)
    except Exception as e:
        print("Q4_K_M route failed, exporting Q8_0 instead:", e)
        model.save_pretrained_gguf(f"/tmp/{RUN}_q8", tokenizer, quantization_method="Q8_0")
        q8 = [g for g in glob.glob(f"/tmp/{RUN}_q8*/**/*.gguf", recursive=True) + glob.glob(f"/tmp/{RUN}_q8*.gguf")
              if "mmproj" not in g.lower()]
        if q8:
            os.replace(q8[0], f"{RUN}-triage.Q8_0.gguf")
    !ls -lh *.gguf
""")
code("""
# Keep only what gets downloaded: the 4-bit GGUF (text model), the adapter zip, the metrics.
# Anything else the exporter or trainer left in the working dir would be downloaded too.
import glob, shutil
ggufs = [g for g in glob.glob("**/*.gguf", recursive=True) if "mmproj" not in g.lower()]
best = [g for g in ggufs if "Q4_K_M" in g.upper()] or [g for g in ggufs if "Q8_0" in g.upper()]
if best:
    quant = "Q4_K_M" if "Q4_K_M" in best[0].upper() else "Q8_0"
    shutil.move(best[0], f"{RUN}-triage.{quant}.gguf")
for d in glob.glob(f"{RUN}_triage_gguf*") + ["outputs", "unsloth_compiled_cache", "_unsloth_sentencepiece_temp"]:
    shutil.rmtree(d, ignore_errors=True)
for g in glob.glob("*.gguf"):
    if not g.startswith(f"{RUN}-triage."):
        os.remove(g)
!ls -lh /kaggle/working
""")
md("""
## 6. Back on the laptop
1. Put the `.gguf` in `models/` as `medgemma-triage.Q4_K_M.gguf` (git-ignored) and run
   `ollama create carecompass-medgemma -f models/Modelfile.medgemma-triage`.
2. `python training/eval_finetune.py --model carecompass-medgemma --prompt rules_only --pair`
3. Only if it passes the gates in `training/FINETUNE_PLAN.md`: in `config.yaml` set
   `classifier.model: carecompass-medgemma` and `classifier.prompt: rules_only`, then rerun `eval/eval_dataset.py`.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                    "language_info": {"name": "python"}, "accelerator": "GPU"},
      "nbformat": 4, "nbformat_minor": 5}
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(nb, indent=1, ensure_ascii=False))
