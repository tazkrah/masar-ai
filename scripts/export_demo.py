"""يصدّر نتائج تجربة محفوظة (قاعدة SQLite محلية) لملف JSON بالمستودع، حتى الديمو يشتغل بدون القاعدة وبدون API.

يقرأ القاعدة للقراءة فقط وما يغيّرها. يصدّر الرسائل ونتائج المعالجة مثل ما انحفظت بالتجربة، بدون سجل الطلبات
(llm_calls) وبدون أقفال التشغيل. الحقول اللي ما كانت موجودة وقت التجربة ما تنضاف.

الاستخدام:
  python scripts/export_demo.py local.nosync/runs/llm-dev-20261029-184044.db runs/llm-dev-20261029-184044.json \
      runs/demo/dev-sample-2-v4.json
"""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP = {"run_id", "run_started_at"}
# نسخة البيانات وقت تجربة v4 على العينة 2 (29/10). التقرير ما يسجلها، فهي من سجل revisions بـ manifest.json.
DATA_VERSION_AT_RUN = 4


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(db_path, report_path, out_path):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if report.get("split") != "dev":
        raise SystemExit("الديمو من مجموعة التطوير بس")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    batch = con.execute("SELECT filename, uploaded_at FROM batches ORDER BY id LIMIT 1").fetchone()
    items = con.execute(
        "SELECT c.id, c.source_id, c.message, c.received_at, c.channel, bi.row_number "
        "FROM batch_items bi JOIN complaints c ON c.id = bi.complaint_id ORDER BY bi.row_number").fetchall()
    results = {r["complaint_id"]: {k: r[k] for k in r.keys() if k not in SKIP}
               for r in con.execute("SELECT * FROM results")}
    if con.execute("SELECT count(*) FROM reviews").fetchone()[0]:
        raise SystemExit("القاعدة بيها قرارات مراجعة؛ الديمو لازم يبدأ من نتائج المعالجة بس")
    versions = {r["prompt_version"] for r in results.values()}
    out = {
        "about": "نتائج معالجة سابقة محفوظة مثل ما طلعت، للعرض بس. مو تقييم جديد.",
        "source": {"run_report": Path(report_path).as_posix(), "run_report_sha256": sha256(report_path),
                   "database": Path(db_path).name, "database_sha256": sha256(db_path),
                   "split": report["split"], "model": report["model"], "provider": report["provider"],
                   "prompt_version": sorted(versions), "data_version_at_run": DATA_VERSION_AT_RUN,
                   "data_version_note": "manifest.json: النسخة 5 انطبقت يوم 2026-11-03، بعد التجربة",
                   "batch_filename": batch["filename"], "processed_at": batch["uploaded_at"]},
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complaints": [dict(r) for r in items],
        "results": [results[r["id"]] for r in items],
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(items)} شكوى ← {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
