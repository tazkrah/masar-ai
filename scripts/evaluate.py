"""تقييم النظام على مجموعة بيانات ومقارنته بالإجابات الصحيحة.

    python scripts/evaluate.py --split dev                 # التطوير (البيانات المولّدة بالموديل)
    python scripts/evaluate.py --split dev --dataset template   # بيانات القوالب (لاختبار الكود)
    python scripts/evaluate.py --split dev --limit 10      # تجربة صغيرة أولاً
    python scripts/evaluate.py --split dev --ids runs/samples/dev-sample-2.json   # عينة محفوظة مسبقاً
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


FIELDS = ("transaction_id", "amount", "date")
MODEL_REASONS = {"low_confidence"} | pipeline.MODEL_FLAGS  # أسباب مراجعة من الموديل نفسه، مو من حماية الكود


def raw_fields(raw):
    """حقول جواب الموديل الخام قبل الحماية. رقم العملية يتوحد شكله بس (أرقام إنجليزية بلا فواصل)."""
    txid = pipeline._digits_only(raw.get("transaction_id")) or None
    return {"transaction_id": txid, "amount": raw.get("amount_iqd"), "date": raw.get("date") or None}


def raw_needs_review(r, raw, threshold):
    """قرار المراجعة لو اعتمدنا على الموديل بس: ثقة منخفضة أو إشارة منه."""
    try:
        conf = float(raw.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.0
    flags = set(raw.get("flags") or []) & pipeline.MODEL_FLAGS
    return conf < threshold or bool(flags) or bool(raw.get("secondary_category"))


def raw_vs_guarded(done, labels, threshold):
    """نتيجة الموديل الخام مقابل النتيجة بعد حماية الكود (مسح القيم غير المسنودة، فحص التبرير والمسودة)."""
    with_raw = [r for r in done if r.get("raw_output")]
    guard = Counter(x for r in done for x in r["review_reasons"] if x not in MODEL_REASONS)
    out = {"rows_with_raw": len(with_raw), "rows_without_raw": len(done) - len(with_raw),
           "guard_review_reasons": dict(guard)}
    if not with_raw:
        out["note"] = "النتائج قبل v3 ما بيها جواب خام محفوظ، فالمقارنة غير متوفرة لهالصفوف."
        return out
    n = len(with_raw)
    raw_ok, grd_ok, changed = Counter(), Counter(), Counter()
    raw_rev = grd_rev = raw_hard = grd_hard = hard = 0
    for r in with_raw:
        g, rf = labels[r["source_id"]], raw_fields(r["raw_output"])
        for f in FIELDS:
            raw_ok[f] += norm(g[f]) == norm(rf[f])
            grd_ok[f] += norm(g[f]) == norm(r[f])
            changed[f] += norm(rf[f]) != norm(r[f])
        rr, gr = raw_needs_review(r, r["raw_output"], threshold), bool(r["review_reasons"])
        raw_rev += rr
        grd_rev += gr
        if int(g["expect_review"]):
            hard += 1
            raw_hard += rr
            grd_hard += gr
    out.update({
        "field_exact": {"raw": {f: raw_ok[f] / n for f in FIELDS}, "guarded": {f: grd_ok[f] / n for f in FIELDS}},
        "fields_changed_by_guard": dict(changed),
        "review_rate": {"raw": raw_rev / n, "guarded": grd_rev / n},
        "hard_cases_sent_to_review": {"raw": f"{raw_hard}/{hard}", "guarded": f"{grd_hard}/{hard}"},
    })
    return out


def class_coverage(pairs, per_class):
    """كل فئة: كم مرة بالإجابات الصحيحة وكم مرة توقعها الموديل. الفئة الغايبة عن الاثنين F1 مالتها بلا معنى."""
    gold, pred = Counter(g for g, _ in pairs), Counter(p for _, p in pairs)
    cov = {c: {"gold": gold[c], "predicted": pred[c], "f1": per_class[c] if gold[c] or pred[c] else None}
           for c in config.CATEGORIES}
    present = [c for c in config.CATEGORIES if gold[c] or pred[c]]
    absent = [c for c in config.CATEGORIES if c not in present]
    macro_present = sum(per_class[c] for c in present) / len(present) if present else None
    return cov, present, absent, macro_present


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
    coverage, present, absent, macro_present = class_coverage(pairs, per_class)
    tin = sum(r["input_tokens"] for r in done if r["input_tokens"] is not None)
    tout = sum(r["output_tokens"] for r in done if r["output_tokens"] is not None)
    tunknown = sum(r["input_tokens"] is None or r["output_tokens"] is None for r in done)
    price = PRICES.get(done[0]["model"]) if done else None
    return {
        "n": len(results), "processed": n, "failed": len(failed),
        "failures": [{"id": r["source_id"], "error": r["error"]} for r in failed],
        "category_accuracy": sum(g == p for g, p in pairs) / n if n else None,
        "category_macro_f1_present_classes": macro_present,
        "category_macro_f1_all_classes": macro,
        "classes_absent_from_sample": absent,
        "class_coverage_note": (f"الفئات الغايبة عن العينة ({'، '.join(absent)}) ما إلها أمثلة، فالـ F1 مالتها ما ينقاس. "
                                "macro-F1 لكل الفئات يحسبها صفر وينزل بدون سبب حقيقي.") if absent else None,
        "class_coverage": coverage,
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
        "tokens": {"input": tin, "output": tout, "rows_with_unknown_tokens": tunknown},
        "est_cost_usd": round(tin / 1e6 * price[0] + tout / 1e6 * price[1], 4) if price else None,
        "raw_vs_guarded": raw_vs_guarded(done, labels, threshold),
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


def run_db_path(arg):
    """مسار قاعدة تشغيل سابق من --retry-run. نسبي = من مجلد المشروع. الروابط (symlink) تنحل."""
    p = Path(arg).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.is_file():
        sys.exit(f"ما لگينا قاعدة التشغيل: {p}")
    return p


def select_ids(rows, ids_path, split):
    """الشكاوى المذكورة بملف العينة بس. أي معرّف ناقص يوقف التشغيل بدال ما نشغّل عينة مختلفة."""
    p = Path(ids_path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    sample = json.loads(p.read_text(encoding="utf-8"))
    if sample.get("split") not in (None, split):
        sys.exit(f"العينة من {sample['split']} مو {split}")
    by_id = {r["source_id"]: r for r in rows}
    missing = [i for i in sample["ids"] if i not in by_id]
    if missing:
        sys.exit(f"معرّفات مو موجودة بـ {split}: {', '.join(missing)}")
    return [by_id[i] for i in sample["ids"]]


def show_path(p):
    """المسار نسبةً للمشروع إذا داخله، وإلا المسار الكامل (بدال ما يطيح)."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "calib", "test"], required=True)
    ap.add_argument("--dataset", choices=list(DATASETS), default="llm")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="ملف JSON بعينة محفوظة مسبقاً (المفتاح ids): نشغّل هذي الشكاوى بس، بنفس ترتيبها")
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
    if args.ids:
        rows = select_ids(rows, args.ids, args.split)
    if args.limit:
        rows = rows[: args.limit]

    RUNS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.retry_run:
        db_path = run_db_path(args.retry_run)
        db.init(db_path)  # قواعد التشغيل القديمة تاخذ الأعمدة والجداول الجديدة بدون تغيير بياناتها
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

    run = pipeline.process(ids, with_draft=not args.no_drafts, workers=args.workers, on_progress=progress,
                           db_path=db_path)
    print()
    if run["stopped"]:
        print(f"الدفعة وقفت: {run['stopped']}" + (f" — الاستئناف بعد {run['resume_after']} (UTC)" if run["resume_after"] else ""))
    with db.connect(db_path) as conn:
        results = db.batch_rows(conn, batch_id)

    report = score(results, labels, config.CONFIDENCE_THRESHOLD)
    report.update({
        "split": args.split, "dataset": args.dataset, "provider": config.PROVIDER, "model": config.MODEL,
        "prompt_version": config.PROMPT_VERSION, "drafts": not args.no_drafts,
        "wall_seconds": round(time.time() - t0, 1), "db": show_path(db_path),
        "run": {k: run[k] for k in ("run_id", "done", "failed", "skipped", "not_started", "stopped", "resume_after")},
        "forbidden_in_drafts": sum("draft_forbidden_phrase" in r["review_reasons"] for r in results if r["status"] == "done"),
    })
    with db.connect(db_path) as conn:
        report["llm_calls"] = db.usage_summary(conn, run["run_id"])  # كل طلبات HTTP، حتى الفاشلة والإعادات
    if args.sweep:
        report["sweep"] = sweep(results, labels)
    out = RUNS / f"{args.dataset}-{args.split}-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.split == "test":
        with (RUNS / "test_runs.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} model={config.MODEL} prompt={config.PROMPT_VERSION} threshold={config.CONFIDENCE_THRESHOLD}\n")

    show = {k: v for k, v in report.items() if k not in ("errors", "confusion", "failures")}
    print(json.dumps(show, ensure_ascii=False, indent=2))
    print(f"\nأخطاء: {len(report['errors'])} — التفاصيل بـ {show_path(out)}")


if __name__ == "__main__":
    main()
