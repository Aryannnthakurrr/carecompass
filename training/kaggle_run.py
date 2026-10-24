"""Train on Kaggle from this laptop: upload the data, run the notebook for each candidate model on a T4, wait,
pull the metrics, pick the winner, download its GGUF, load it into Ollama and score it locally. No paid APIs.

One-time setup (yours, it needs your Kaggle login):  kaggle auth login
Then:
  python training/kaggle_run.py all                 # everything below, in order
  python training/kaggle_run.py upload              # private dataset <you>/carecompass-sft (train/val/test.jsonl)
  python training/kaggle_run.py push                # one private notebook per candidate, GPU T4
  python training/kaggle_run.py wait                # poll until every run finishes
  python training/kaggle_run.py pull                # metrics -> pick winner -> GGUF -> ollama create -> local eval

Trains MedGemma 4B-it only (the model the app runs). Add an entry to CANDIDATES to train another model the same way.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "training" / "kaggle"
OUT = ROOT / "models" / "kaggle"
KAGGLE = str(Path(sys.executable).with_name("kaggle.exe" if sys.platform == "win32" else "kaggle"))
CANDIDATES = {"medgemma": "unsloth/medgemma-4b-it"}
DATASET_SLUG = "carecompass-sft"
OLLAMA_NAME = "carecompass-triage"
TAG = "v3"  # Kaggle notebook suffix: one notebook per training round (v3 = recall-first relabelled data)


def kaggle(*args, check=True):
    r = subprocess.run([KAGGLE, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if check and r.returncode:
        sys.exit(f"kaggle {' '.join(args)} failed:\n{out}")
    return out


def username(a):
    if a.user:
        return a.user
    m = re.search(r"username:\s*(\S+)", kaggle("config", "view", check=False))
    if not m or m.group(1) in ("None", "null"):
        sys.exit("Kaggle username unknown: run `kaggle auth login` first, or pass --user <your kaggle username>")
    return m.group(1)


def upload(a):
    user, d = username(a), STAGE / "data"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for f in ("train", "val", "test"):
        shutil.copy(ROOT / "training/sft" / f"{f}.jsonl", d)
    (d / "dataset-metadata.json").write_text(json.dumps(
        {"title": "CareCompass triage SFT (synthetic)", "id": f"{user}/{DATASET_SLUG}", "licenses": [{"name": "other"}]}, indent=1))
    out = kaggle("datasets", "create", "-p", str(d), check=False)  # private unless -u is passed
    if "already" in out.lower() or "exists" in out.lower():
        out = kaggle("datasets", "version", "-p", str(d), "-m", f"update {time.strftime('%Y-%m-%d %H:%M')}")
    print(out)
    print(f"dataset: https://www.kaggle.com/datasets/{user}/{DATASET_SLUG} (private). Kaggle needs a minute to process it.")


def push(a):
    user = username(a)
    nb = json.loads((ROOT / "training/medgemma_lora.ipynb").read_text(encoding="utf-8"))
    for run, model in CANDIDATES.items():
        if a.only and run != a.only:
            continue
        d = STAGE / run
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        cell = nb["cells"][1]  # the parameters cell
        src = "".join(cell["source"])
        assert src.lstrip().startswith("# Parameters"), "notebook layout changed: regenerate with training/make_notebook.py"
        src = re.sub(r'MODEL_NAME = "[^"]*"', f'MODEL_NAME = "{model}"', src)
        src = re.sub(r'RUN = "[^"]*"', f'RUN = "{run}"', src)
        mine = json.loads(json.dumps(nb))
        mine["cells"][1]["source"] = src.splitlines(keepends=True)
        (d / "notebook.ipynb").write_text(json.dumps(mine, indent=1, ensure_ascii=False), encoding="utf-8")
        (d / "kernel-metadata.json").write_text(json.dumps({
            "id": f"{user}/carecompass-{run}-{TAG}", "title": f"carecompass {run} {TAG}", "code_file": "notebook.ipynb",
            "language": "python", "kernel_type": "notebook", "is_private": True, "enable_gpu": True,
            "enable_internet": True, "dataset_sources": [f"{user}/{DATASET_SLUG}"]}, indent=1))
        print(kaggle("kernels", "push", "-p", str(d), "--accelerator", "NvidiaTeslaT4"))


def status(user, run):
    out = kaggle("kernels", "status", f"{user}/carecompass-{run}-{TAG}", check=False).lower()
    for s in ("complete", "error", "cancel", "running", "queued"):
        if s in out:
            return s
    return out[-120:]


def wait(a):
    user = username(a)
    runs = [r for r in CANDIDATES if not a.only or r == a.only]
    while True:
        states = {r: status(user, r) for r in runs}
        print(time.strftime("%H:%M"), states, flush=True)
        if all(s in ("complete", "error", "cancel") for s in states.values()):
            return states
        time.sleep(120)


def pull(a):
    user = username(a)
    runs = [r for r in CANDIDATES if not a.only or r == a.only]
    metrics = {}
    for run in runs:
        d = OUT / run
        d.mkdir(parents=True, exist_ok=True)
        kaggle("kernels", "output", f"{user}/carecompass-{run}-{TAG}", "-p", str(d), "--file-pattern", r"metrics_.*\.json", "-o", check=False)
        f = d / f"metrics_{run}.json"
        if f.exists():
            metrics[run] = json.loads(f.read_text())
            m = metrics[run]
            print(f"{run}: accuracy {m['accuracy']:.3f}  valid {m['valid']}/{m['rows']}  emergencies {m['emergency_recall']}  train loss {m['train_loss']:.3f}")
        else:
            print(f"{run}: no metrics (run failed or still going); see https://www.kaggle.com/code/{user}/carecompass-{run}-{TAG}")
    if not metrics:
        sys.exit("nothing to compare")

    def key(run):  # full emergency recall first, then accuracy
        hit, total = map(int, metrics[run]["emergency_recall"].split("/"))
        return (hit == total, metrics[run]["accuracy"])

    win = max(metrics, key=key)
    print(f"winner: {win} ({CANDIDATES[win]})")
    d = OUT / win
    print("downloading its GGUF (~2.5 GB) ...", flush=True)
    kaggle("kernels", "output", f"{user}/carecompass-{win}-{TAG}", "-p", str(d), "--file-pattern", r".*\.(gguf|zip)$", "-o")
    ggufs = sorted(d.glob(f"{win}-triage.*.gguf"))
    if not ggufs:
        sys.exit(f"no GGUF in the {win} output; the LoRA zip is in {d} (Modelfile ADAPTER fallback)")
    target = ROOT / "models" / ggufs[0].name
    shutil.move(str(ggufs[0]), target)
    mf = (ROOT / "models/Modelfile.medgemma-triage").read_text(encoding="utf-8")
    mf = re.sub(r"^FROM \./\S+", f"FROM ./{target.name}", mf, count=1, flags=re.M)
    mf_path = ROOT / "models" / f"Modelfile.{win}-triage"
    mf_path.write_text(mf, encoding="utf-8")
    ollama = shutil.which("ollama") or str(Path.home() / "AppData/Local/Programs/Ollama/ollama.exe")
    subprocess.run([ollama, "create", OLLAMA_NAME, "-f", str(mf_path)], check=True, cwd=ROOT / "models")
    print(f"ollama model {OLLAMA_NAME} created from {target.name}; scoring it on the local test split ...", flush=True)
    subprocess.run([sys.executable, str(ROOT / "training/eval_finetune.py"), "--model", OLLAMA_NAME, "--prompt", "rules_only"], check=True)
    gates()


def gates():
    """Compare the fine-tune with the base-model baselines (eval/results/finetune_*.json) on the ship gates."""
    res = ROOT / "eval/results"
    def load(pattern):
        fs = sorted(res.glob(pattern))
        return next(iter(json.loads(fs[-1].read_text(encoding="utf-8")).values())) if fs else None
    base = load("finetune_medgemma-4b-it-GGUF_Q4_K_M__few_shot__test.json")
    ft = load(f"finetune_{OLLAMA_NAME}__rules_only__test.json")
    if not (base and ft):
        print("gates: need both the baseline and the fine-tune results")
        return
    hit, tot = map(int, ft["emergency_recall"].split("/"))
    checks = {
        "emergency recall 100%": hit == tot,
        "care macro-F1 >= base + examples": ft["care_macro_f1"] >= base["care_macro_f1"],
        "valid JSON on every row": ft["valid"].split("/")[0] == ft["valid"].split("/")[1],
        "median latency <= base": ft["median_s"] <= base["median_s"],
    }
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"  base: F1 {base['care_macro_f1']} em {base['emergency_recall']} {base['median_s']}s | "
          f"fine-tune: F1 {ft['care_macro_f1']} em {ft['emergency_recall']} {ft['median_s']}s")
    print("  all local gates pass: set classifier.model / classifier.prompt: rules_only in config.yaml, then rerun "
          "eval/eval_dataset.py once Cerebras is back" if all(checks.values()) else "  keep the base model")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["upload", "push", "wait", "pull", "gates", "all"])
    ap.add_argument("--user", help="Kaggle username (default: from `kaggle config view`)")
    ap.add_argument("--only", choices=list(CANDIDATES), help="run a single candidate")
    a = ap.parse_args()
    if a.step == "gates":
        gates()
    elif a.step == "all":
        upload(a)
        time.sleep(90)  # let Kaggle finish processing the dataset before notebooks mount it
        push(a)
        wait(a)
        pull(a)
    else:
        globals()[a.step](a)
