"""يطبق تصحيحات الإجابات (scripts/label_corrections.py) على dev_labels.csv وينشئ نسخة البيانات التالية.

    python scripts/apply_label_corrections.py

- نصوص الرسائل والتقسيم ما تتغير؛ السكربت يتأكد من بصمة dev.csv قبل وبعد.
- مجموعتي الضبط والاختبار ما تنفتح: بس بصمتها (sha256) تنقارن بالـ manifest قبل وبعد.
- كل تصحيح ينسجل بـ manifest.json تحت revisions: المعرّف، القيم القديمة، الجديدة، والسبب.
- ما في أي اتصال بالموديل.
"""
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from masar import config  # noqa: E402
import label_corrections as lc  # noqa: E402

DATA = ROOT / "data" / "complaints"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def frozen_ok(manifest):
    """بصمات الضبط والاختبار (الرسائل والإجابات) تطابق الـ manifest. ما نقرا محتواها."""
    bad = []
    for sp in ("calib", "test"):
        m = manifest["splits"][sp]
        if sha(DATA / f"{sp}.csv") != m["sha256"]:
            bad.append(f"{sp}.csv")
        if sha(DATA / "labels" / f"{sp}_labels.csv") != m["labels_sha256"]:
            bad.append(f"{sp}_labels.csv")
    return bad


def main():
    mpath = DATA / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    if manifest.get("data_version", 1) >= lc.DATA_VERSION:
        sys.exit(f"نسخة البيانات {manifest['data_version']} موجودة أصلاً؛ ما نعيد التطبيق.")
    if bad := frozen_ok(manifest):
        sys.exit(f"بصمات تغيرت قبل التطبيق: {bad}")
    dev, lpath = DATA / "dev.csv", DATA / "labels" / "dev_labels.csv"
    if sha(dev) != manifest["splits"]["dev"]["sha256"] or sha(lpath) != manifest["splits"]["dev"]["labels_sha256"]:
        sys.exit("ملفات التطوير ما تطابق الـ manifest؛ ما نطبق تصحيحات على ملف مو معروف.")
    dev_sha = sha(dev)

    with lpath.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames)
        rows = list(reader)
    by_id = {r["complaint_id"]: r for r in rows}
    missing = [i for i in lc.DEV_V5 if i not in by_id]
    if missing:
        sys.exit(f"معرّفات مو موجودة: {missing}")

    changes = []
    out_rows = []
    for r in rows:
        corr = lc.DEV_V5.get(r["complaint_id"])
        if not corr:
            out_rows.append({**lc.NEW_COLUMNS, **r})
            continue
        if r["category"] != corr["expect"]:
            sys.exit(f"{r['complaint_id']}: الفئة {r['category']} مو {corr['expect']} المتوقعة")
        new = lc.apply(r, corr, config)
        old = {**lc.NEW_COLUMNS, **r}
        diff = [k for k in new if new[k] != old[k]]
        if not diff:
            sys.exit(f"{r['complaint_id']}: التصحيح ما غيّر شي")
        changes.append({"id": r["complaint_id"], "from": {k: old[k] for k in diff}, "to": {k: new[k] for k in diff},
                        "reason": corr["reason"]})
        out_rows.append(new)

    fields += [c for c in lc.NEW_COLUMNS if c not in fields]
    with lpath.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    cats = {}
    for r in out_rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    manifest.setdefault("revisions", []).append({
        "data_version": lc.DATA_VERSION,
        "date": datetime.now().isoformat(timespec="seconds"),
        "split": "dev",
        "messages_changed": [],
        "labels_changed": True,
        "method": "manual review of message text and meaning (scripts/label_corrections.py)",
        "reason": "الإجابة الصحيحة تستند لنص الرسالة ومعناها، مو لتفاصيل السيناريو اللي ما ظهرت بالرسالة. "
                  "الغموض «أخرى» ويا مراجعة، الشكوى المالية الغامضة متوسطة كحد أدنى، العملية غير المصرح بها خطر وأمن الحسابات، "
                  "الإيداع بدون وكيل مو خلاف وكيل، اليوم بدون شهر بعد يوم الاستلام يبقى فارغ. القرار مستقل عن جواب الموديل.",
        "new_columns": list(lc.NEW_COLUMNS),
        "labels_sha256_before": manifest["splits"]["dev"]["labels_sha256"],
        "labels_sha256_after": sha(lpath),
        "label_changes": changes,
    })
    manifest["data_version"] = lc.DATA_VERSION
    manifest["splits"]["dev"]["labels_sha256"] = sha(lpath)
    manifest["splits"]["dev"]["by_category"] = cats
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if sha(dev) != dev_sha:
        sys.exit("dev.csv تغير! هذا ما لازم يصير.")
    if bad := frozen_ok(manifest):
        sys.exit(f"بصمات تغيرت بعد التطبيق: {bad}")
    print(json.dumps({"data_version": lc.DATA_VERSION, "labels_changed": len(changes), "dev_by_category": cats},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
