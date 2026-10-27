"""تقييم النظام على مجموعة بيانات ومقارنته بالإجابات الصحيحة.

    python scripts/evaluate.py --split dev                 # التطوير (البيانات المولّدة بالموديل)
    python scripts/evaluate.py --split dev --dataset template   # بيانات القوالب (لاختبار الكود)
    python scripts/evaluate.py --split dev --limit 10      # تجربة صغيرة أولاً
    python scripts/evaluate.py --split calib --sweep       # ضبط حد الثقة
    python scripts/evaluate.py --split test --final        # الاختبار النهائي (مرة وحدة بالنهاية)

كل تشغيل ينحفظ بمجلد runs/ بقاعدة بيانات منفصلة، فما يختلط ويا شغل الموظف.
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from masar import config, db, ingest, llm, pipeline  # noqa: E402

DATASETS = {"llm": ROOT / "data" / "complaints", "template": ROOT / "data" / "template_complaints"}
RUNS = ROOT / "runs"  # التقارير (تنرفع لـ GitHub)
RUN_DBS = config.LOCAL_DIR / "runs"  # قواعد بيانات التشغيل (محلية فقط)
PRICES = {  # دولار لكل مليون توكن (إدخال، إخراج)
    "claude-sonnet-4-6": (3.0, 15.0),
    "openai/gpt-oss-120b": (0.0, 0.0),  # خطة Groq المجانية
}


def load(split, dataset):
    data = DATASETS[dataset]
    if not (data / f"{split}.csv").exists():
        sys.exit("البيانات المولّدة بالموديل ما موجودة بعد. شغّل: ./run.sh generate" if dataset == "llm"
                 else f"ما لگينا {data / f'{split}.csv'}")
    rows, _ = ingest.parse_csv((data / f"{split}.csv").read_bytes())
    labels = {r["complaint_id"]: r for r in csv.DictReader((data / "labels" / f"{split}_labels.csv").open(encoding="utf-8"))}
    return rows, labels


def f1_macro(pairs, classes):
    f1s = {}
    for c in classes:
        tp = sum(1 for g, p in pairs if g == c and p == c)
        fp = sum(1 for g, p in pairs if g != c and p == c)
        fn = sum(1 for g, p in pairs if g == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s[c] = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return sum(f1s.values()) / len(f1s), f1s


def norm(v):
    return None if v in ("", None) else str(v)


def score(results, labels, threshold):
    done = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] != "done"]
    pairs, lenient, errors = [], 0, []
    field_ok = Counter()
    prio_ok = dept_ok = 0
    review_hits = review_expected = 0
    auto, auto_ok = 0, 0
    reasons = Counter()
    conf = defaultdict(Counter)

    for r in done:
        g = labels[r["source_id"]]
        pairs.append((g["category"], r["category"]))
        conf[g["category"]][r["category"]] += 1
        ok_cat = g["category"] == r["category"]
        lenient += ok_cat or (bool(g["secondary_category"]) and r["category"] == g["secondary_category"])
        mism = []
        for f in ("transaction_id", "amount", "date"):
            if norm(g[f]) == norm(r[f]):
                field_ok[f] += 1
            else:
                mism.append(f"{f}: متوقع={g[f] or 'فارغ'} طلع={r[f] if r[f] is not None else 'فارغ'}")
        prio_ok += g["priority"] == r["priority"]
        dept_ok += g["department"] == r["department"]
        needs_review = bool(r["review_reasons"])
        for x in r["review_reasons"]:
            reasons[x] += 1
        if int(g["expect_review"]):
            review_expected += 1
            review_hits += needs_review
        if not needs_review:
            auto += 1
            auto_ok += g["department"] == r["department"]
        if not ok_cat or mism or g["priority"] != r["priority"] or (int(g["expect_review"]) and not needs_review):
            errors.append({
                "id": r["source_id"], "family": g["family_id"], "message": r["message"],
                "category": f"{g['category']} -> {r['category']}" if not ok_cat else "صح",
                "priority": f"{g['priority']} -> {r['priority']}" if g["priority"] != r["priority"] else "صح",
                "fields": mism, "confidence": r["confidence"], "review_reasons": r["review_reasons"],
                "expected_review": g["hard_type"] or None, "reason": r["reason"],
            })

    n = len(done)
    macro, per_class = f1_macro(pairs, list(config.CATEGORIES))
    tin = sum(r["input_tokens"] or 0 for r in done)
    tout = sum(r["output_tokens"] or 0 for r in done)
    price = PRICES.get(done[0]["model"]) if done else None
    return {
        "n": len(results), "processed": n, "failed": len(failed),
        "failures": [{"id": r["source_id"], "error": r["error"]} for r in failed],
        "category_accuracy": sum(g == p for g, p in pairs) / n if n else None,
        "category_macro_f1": macro, "per_class_f1": per_class,
        "category_accuracy_lenient_multi": lenient / n if n else None,
        "field_exact": {f: field_ok[f] / n for f in ("transaction_id", "amount", "date")} if n else {},
        "field_exact_all3": sum(1 for r in done if all(norm(labels[r["source_id"]][f]) == norm(r[f]) for f in ("transaction_id", "amount", "date"))) / n if n else None,
        "priority_accuracy": prio_ok / n if n else None,
        "department_accuracy_all": dept_ok / n if n else None,
        "auto_routed_share": auto / n if n else None,
        "auto_routed_department_accuracy": auto_ok / auto if auto else None,
        "review_rate": sum(1 for r in done if r["review_reasons"]) / n if n else None,
        "hard_cases_sent_to_review": f"{review_hits}/{review_expected}",
        "review_reasons": dict(reasons),
        "confusion": {g: dict(c) for g, c in conf.items()},
        "threshold": threshold,
        "avg_latency_ms": sum(r["latency_ms"] or 0 for r in done) / n if n else None,
        "tokens": {"input": tin, "output": tout},
        "est_cost_usd": round(tin / 1e6 * price[0] + tout / 1e6 * price[1], 4) if price else None,
        "errors": errors,
    }


def sweep(results, labels):
    """نجرب حدود ثقة مختلفة على النتائج المحفوظة. نستخدمه على calib فقط."""
    done = [r for r in results if r["status"] == "done"]
    out = []
    for t in [0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]:
        auto = [r for r in done if r["confidence"] >= t and not [x for x in r["review_reasons"] if x != "low_confidence"]]
        ok = sum(labels[r["source_id"]]["department"] == r["department"] for r in auto)
        out.append({"threshold": t, "auto_share": len(auto) / len(done), "auto_accuracy": ok / len(auto) if auto else None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "calib", "test"], required=True)
    ap.add_argument("--dataset", choices=list(DATASETS), default="llm")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-drafts", action="store_true")
    ap.add_argument("--workers", type=int, default=None, help="الافتراضي MASAR_WORKERS (1)")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--final", action="store_true", help="مطلوب لتشغيل مجموعة الاختبار النهائي")
    ap.add_argument("--retry-run", help="مسار قاعدة تشغيل سابق: نعيد الفاشلة بس")
    args = ap.parse_args()

    if args.split == "test" and not args.final:
        sys.exit("مجموعة الاختبار النهائي مقفولة. ما نستخدمها للتطوير. إذا هذا التشغيل النهائي فعلاً، أضف --final.")
    if args.sweep and args.split == "test":
        sys.exit("ضبط حد الثقة يصير على calib فقط.")
    env, ok = llm.key_status()
    if not ok:
        sys.exit(f"ما لگينا {env}. انسخ .env.example إلى .env وحط المفتاح.")

    rows, labels = load(args.split, args.dataset)
    if args.limit:
        rows = rows[: args.limit]

    RUNS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.retry_run:
        db_path = Path(args.retry_run)
        with db.connect(db_path) as conn:
            batch_id = conn.execute("SELECT MAX(id) FROM batches").fetchone()[0]
    else:
        RUN_DBS.mkdir(exist_ok=True)
        db_path = RUN_DBS / f"{args.dataset}-{args.split}-{stamp}.db"
        db.init(db_path)
        with db.connect(db_path) as conn:
            batch_id, _ = db.create_batch(conn, f"{args.split}.csv", rows, [])
    with db.connect(db_path) as conn:
        ids = db.batch_ids_to_process(conn, batch_id)

    t0 = time.time()

    def progress(i, n, cid, status):
        print(f"\r{i}/{n} {status:<6}", end="", flush=True)

    pipeline.process(ids, with_draft=not args.no_drafts, workers=args.workers, on_progress=progress, db_path=db_path)
    print()
    with db.connect(db_path) as conn:
        results = db.batch_rows(conn, batch_id)

    report = score(results, labels, config.CONFIDENCE_THRESHOLD)
    report.update({
        "split": args.split, "dataset": args.dataset, "provider": config.PROVIDER, "model": config.MODEL,
        "prompt_version": config.PROMPT_VERSION, "drafts": not args.no_drafts,
        "wall_seconds": round(time.time() - t0, 1), "db": str(db_path.relative_to(ROOT)),
        "forbidden_in_drafts": sum("draft_forbidden_phrase" in r["review_reasons"] for r in results if r["status"] == "done"),
    })
    if args.sweep:
        report["sweep"] = sweep(results, labels)
    out = RUNS / f"{args.dataset}-{args.split}-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.split == "test":
        with (RUNS / "test_runs.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} model={config.MODEL} prompt={config.PROMPT_VERSION} threshold={config.CONFIDENCE_THRESHOLD}\n")

    show = {k: v for k, v in report.items() if k not in ("errors", "confusion", "per_class_f1", "failures")}
    print(json.dumps(show, ensure_ascii=False, indent=2))
    print(f"\nأخطاء: {len(report['errors'])} — التفاصيل بـ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
