"""Retrieval of similar, already-graded cases for MedGemma's emergency screen (retrieval-augmented few-shot).

The index is the training split of the labelled dataset (training/sft/train.jsonl: teacher-panel labels plus the
recall-first manual review), written as the same clinical notes the screener reads. For a new case, the k most similar
notes by TF-IDF cosine are shown to MedGemma with their grades, so it sees how cases like THIS one were judged instead of
a fixed handful of examples. Test-split cases are never indexed. Local, no API, a few milliseconds per lookup.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.schemas import CaseFacts

log = logging.getLogger("carecompass.retrieval")
ROOT = Path(__file__).resolve().parents[1]
INDEX_FILE = ROOT / "training/sft/train.jsonl"
TEST_FILES = (ROOT / "training/sft/test.jsonl", ROOT / "training/sft/test_v2.jsonl")
GRADE = {"emergency_112": "emergency_now", "lab_test_home": "routine"}  # every other care type is same-day care
_note = None  # app.screen.note, bound lazily to avoid a circular import


def _grade(target: str) -> str:
    return GRADE.get(target, "see_doctor_today")


@lru_cache(maxsize=1)
def _index():
    from sklearn.feature_extraction.text import TfidfVectorizer

    from app import screen
    global _note
    _note = screen.note
    notes, grades = [], []
    if not INDEX_FILE.exists():
        log.warning("no retrieval index at %s", INDEX_FILE)
        return None, None, [], []
    # never index a case that any evaluation scores (the current and the earlier test split)
    held_out = set()
    for f in TEST_FILES:
        if f.exists():
            held_out |= {json.loads(line)["id"] for line in f.read_text(encoding="utf-8").splitlines() if line.strip()}
    for line in INDEX_FILE.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["target"] == "need_more_info" or r["id"] in held_out:
            continue
        try:
            facts = CaseFacts(**json.loads(r["messages"][1]["content"][len("Patient facts: "):]))
        except Exception:  # noqa: BLE001 - a malformed row is skipped, not fatal
            continue
        if not facts.symptoms_present:
            continue  # the screen only runs on symptom complaints
        notes.append(_note(facts))
        grades.append(_grade(r["target"]))
    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)
    matrix = vec.fit_transform(notes)
    log.info("retrieval index: %d graded cases", len(notes))
    return vec, matrix, notes, grades


def warm() -> None:
    """Build the index now (API startup) so the first patient does not wait for it."""
    _index()


def similar(facts: CaseFacts, k: int = 4) -> list[tuple[str, str]]:
    """-> up to k (note, grade) pairs most similar to this case, most similar first; [] if no index."""
    vec, matrix, notes, grades = _index()
    if vec is None:
        return []
    q = vec.transform([_note(facts)])
    scores = (matrix @ q.T).toarray().ravel()
    out, seen = [], set()
    for i in scores.argsort()[::-1]:
        if scores[i] <= 0 or len(out) == k:
            break
        if notes[i] in seen:
            continue
        seen.add(notes[i])
        out.append((notes[i], grades[i]))
    return out
