"""تجربة شكوى وهمية وحدة من البداية للنهاية: CSV -> قاعدة بيانات -> موديل -> فحص -> مسودة.

    python scripts/try_one.py
    python scripts/try_one.py "نص شكوى ثانية"

تستخدم قاعدة بيانات تجريبية منفصلة (local.nosync/try_one.db)، وما ترسل أي شي لأي زبون.
"""
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from masar import config, db, ingest, llm, pipeline  # noqa: E402

DEFAULT = "السلام عليكم، حولت ٧٥ الف لاخوي البارحة وانخصمت من رصيدي بس هو يگول ما وصله شي، رقم العملية 48213097"


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    env, ok = llm.key_status()
    if not ok:
        sys.exit(f"ما لگينا {env} بملف .env.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["complaint_id", "received_at", "channel", "message"])
    w.writerow(["TRY-1", "2026-10-27 10:00", "واتساب", message])
    rows, _ = ingest.parse_csv(buf.getvalue().encode("utf-8"))

    path = config.LOCAL_DIR / "try_one.db"
    path.unlink(missing_ok=True)
    db.init(path)
    with db.connect(path) as conn:
        batch_id, _ = db.create_batch(conn, "try_one.csv", rows, [])
        ids = db.batch_ids_to_process(conn, batch_id)
    print(f"المزوّد: {config.PROVIDER} · الموديل: {config.MODEL} · التفكير: {config.REASONING_EFFORT}")
    print("الرسالة:", message, "\n")
    s = pipeline.process(ids, db_path=path)
    with db.connect(path) as conn:
        r = db.batch_rows(conn, batch_id)[0]
    if r["status"] != "done":
        print("فشلت المعالجة:", r["error"])
    else:
        show = {
            "نوع المشكلة": config.CATEGORIES[r["category"]],
            "الأولوية": config.PRIORITIES[r["priority"]],
            "القسم": r["department"],
            "رقم العملية": r["transaction_id"] or "فارغ",
            "المبلغ": f"{r['amount']:,} د.ع" if r["amount"] else "فارغ",
            "التاريخ": r["date"] or "فارغ",
            "الثقة": f"{r['confidence']:.2f}",
            "سبب التصنيف": r["reason"],
            "أسباب المراجعة": [config.REVIEW_REASON_LABELS.get(x, x) for x in r["review_reasons"]] or "ما في",
            "معلومات ناقصة": [config.FIELD_LABELS[x] for x in r["missing_fields"]] or "ما في",
        }
        for k, v in show.items():
            print(f"{k}: {v}")
        print("\nمسودة الرد (للمراجعة، ما انرسلت):\n" + (r["draft"] or ""))
        print(f"\nالزمن: {r['latency_ms']} ms · توكنات: {r['input_tokens']} إدخال / {r['output_tokens']} إخراج · محاولات: {r['attempts']}")
    if llm.last_rate_limits:
        print("حدود الاستخدام (من رد المزوّد):", json.dumps(llm.last_rate_limits, ensure_ascii=False))
    sys.exit(0 if s["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
