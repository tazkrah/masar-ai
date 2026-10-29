"""اختيار عينة تطوير ثابتة من dev قبل التشغيل، وحفظ معرّفاتها.

    python scripts/select_dev_sample.py --out runs/samples/dev-sample-2.json

الاختيار يعتمد على الإجابات الصحيحة (الفئة ونوع الصعوبة) بس، حتى تتوزع العينة، ومو على أي نتيجة للموديل.
عشوائي ببذرة ثابتة، وشكوى وحدة من كل عائلة قدر الإمكان، ويستثني الشكاوى اللي انشغلت قبل.
بعد الحفظ ما نبدّل أي شكوى بناءً على نتيجتها.
"""
import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "complaints"

# (الفئة، نوع الصعوبة أو clear) -> العدد. المجموع 20: 15 واضحة و5 صعبة تغطي كل أنواع الصعوبة بـ dev.
QUOTAS = {
    ("transfer_failed", "clear"): 3, ("transfer_failed", "multiple_issues"): 1,
    ("wrong_recipient", "clear"): 2, ("wrong_recipient", "similar_names"): 1,
    ("account_access", "clear"): 3,
    ("agent_dispute", "clear"): 2, ("agent_dispute", "multiple_issues"): 1,
    ("card", "clear"): 3,
    ("other", "clear"): 2, ("other", "possible_injection"): 1, ("other", "unclear_message"): 1,
}
# الشكاوى العشر الأولى من dev، استخدمناها بتجارب 1–4
EXCLUDE = ["DEV-A10-4", "DEV-K10-4", "DEV-G07-5", "DEV-G01-3", "DEV-T08-2",
           "DEV-K04-3", "DEV-O03-2", "DEV-T11-1", "DEV-O02-5", "DEV-K07-3"]
SEED = 20261029


def select(labels, quotas=QUOTAS, exclude=EXCLUDE, seed=SEED):
    """شكوى شكوى: نفضّل عائلة ما انشغلت بالعشر السابقة وما انختارت بالعينة، وإذا ما موجودة نرجع للباقي."""
    rng = random.Random(seed)
    by_id = {l["complaint_id"]: l for l in labels}
    prev_families = {by_id[i]["family_id"] for i in exclude if i in by_id}
    pool = [l for l in labels if l["complaint_id"] not in set(exclude)]
    used_families, picked = set(), []
    for (cat, kind), n in quotas.items():
        cands = sorted((l for l in pool if l["category"] == cat and (l["hard_type"] or "clear") == kind),
                       key=lambda l: l["complaint_id"])
        rng.shuffle(cands)
        if len(cands) < n:
            sys.exit(f"ما يكفي: {cat}/{kind} يحتاج {n} ولگينا {len(cands)}")
        for _ in range(n):
            cands.sort(key=lambda l: (l["family_id"] in used_families, l["family_id"] in prev_families))  # ترتيب ثابت
            l = cands.pop(0)
            used_families.add(l["family_id"])
            picked.append({"id": l["complaint_id"], "category": cat, "kind": kind, "family": l["family_id"],
                           "family_seen_before": l["family_id"] in prev_families})
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} موجود. العينة تنحفظ مرة وحدة وما تتبدل.")
    labels = list(csv.DictReader((DATA / "labels" / "dev_labels.csv").open(encoding="utf-8")))
    picked = select(labels)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "split": "dev", "created_at": datetime.now().isoformat(timespec="seconds"), "seed": SEED,
        "method": ("طبقي حسب الفئة ونوع الصعوبة من الإجابات الصحيحة، عشوائي ببذرة ثابتة. عائلة جديدة لكل شكوى قدر الإمكان، "
                   "مو من عائلات العشر السابقة؛ family_seen_before = ما كان أكو بديل بهالطبقة"),
        "quotas": {f"{c}/{k}": n for (c, k), n in QUOTAS.items()}, "excluded": EXCLUDE,
        "ids": [p["id"] for p in picked], "items": picked,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(picked)} شكوى انحفظت بـ {out}")


if __name__ == "__main__":
    main()
