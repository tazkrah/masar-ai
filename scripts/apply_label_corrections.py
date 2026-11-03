"""يطبق تصحيحات الإجابات (scripts/label_corrections.py) على dev_labels.csv وينشئ نسخة البيانات التالية (وحدة بكل تشغيل).

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


REASONS = {
    5: "الإجابة الصحيحة تستند لنص الرسالة ومعناها، مو لتفاصيل السيناريو اللي ما ظهرت بالرسالة. "
       "الغموض «أخرى» ويا مراجعة، الشكوى المالية الغامضة متوسطة كحد أدنى، العملية غير المصرح بها خطر وأمن الحسابات، "
       "الإيداع بدون وكيل مو خلاف وكيل، اليوم بدون شهر بعد يوم الاستلام يبقى فارغ. القرار مستقل عن جواب الموديل.",
    6: "تغيير سياسة: العملية المصرّح إنها مو مأذونة تروح لأمن الحسابات بأولوية عالية مهما كانت الفئة (بالنسخة 5 كانت بس "
       "للفئة الغامضة). وعمود amount_category يحدد المشكلة اللي يخصها المبلغ، حتى مبلغ الإيداع ما ينحسب مبلغ التحويل. "
       "ملفات الضبط والاختبار ما تغيرت، فإجاباتها تتبع السياسة القديمة؛ التقييم يعرض أي تعارض بوضوح.",
}


def main():
    mpath = DATA / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    target = manifest.get("data_version", 1) + 1
    if target not in lc.VERSIONS:
        sys.exit(f"نسخة البيانات {manifest.get('data_version')} هي الأحدث؛ ماكو تصحيحات للنسخة {target}.")
    if bad := frozen_ok(manifest):
        sys.exit(f"بصمات تغيرت قبل التطبيق: {bad}")
    dev, lpath = DATA / "dev.csv", DATA / "labels" / "dev_labels.csv"
    if sha(dev) != manifest["splits"]["dev"]["sha256"] or sha(lpath) != manifest["splits"]["dev"]["labels_sha256"]:
        sys.exit("ملفات التطوير ما تطابق الـ manifest؛ ما نطبق تصحيحات على ملف مو معروف.")
    dev_sha, before_sha = sha(dev), sha(lpath)

    with lpath.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames)
        rows = list(reader)
    by_id = {r["complaint_id"]: r for r in rows}
    todo = lc.VERSIONS[target]
    missing = [i for i in todo if i not in by_id]
    if missing:
        sys.exit(f"معرّفات مو موجودة: {missing}")
    merged = lc.corrections(target)

    changes, out_rows = [], []
    for r in rows:
        old = {**lc.column_defaults(r, target), **r}  # الأعمدة الجديدة بقيمها الافتراضية
        if r["complaint_id"] not in todo:
            out_rows.append(old)
            continue
        expect = todo[r["complaint_id"]]["expect"]
        if r["category"] != expect:
            sys.exit(f"{r['complaint_id']}: الفئة {r['category']} مو {expect} المتوقعة")
        new = lc.apply(r, merged[r["complaint_id"]], config, target)
        diff = [k for k in new if new[k] != old[k]]
        if not diff:
            sys.exit(f"{r['complaint_id']}: التصحيح ما غيّر شي")
        changes.append({"id": r["complaint_id"], "from": {k: old[k] for k in diff}, "to": {k: new[k] for k in diff},
                        "reason": todo[r["complaint_id"]]["reason"]})
        out_rows.append(new)

    new_cols = [c for c in lc.NEW_COLUMNS.get(target, {}) if c not in fields]
    fields += new_cols
    with lpath.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    cats = {}
    for r in out_rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    manifest.setdefault("revisions", []).append({
        "data_version": target,
        "date": datetime.now().isoformat(timespec="seconds"),
        "split": "dev",
        "messages_changed": [],
        "labels_changed": True,
        "method": "manual review of message text and meaning (scripts/label_corrections.py)",
        "reason": REASONS[target],
        "new_columns": new_cols,
        "labels_sha256_before": before_sha,
        "labels_sha256_after": sha(lpath),
        "label_changes": changes,
    })
    manifest["data_version"] = target
    manifest["splits"]["dev"]["labels_sha256"] = sha(lpath)
    manifest["splits"]["dev"]["by_category"] = cats
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if sha(dev) != dev_sha:
        sys.exit("dev.csv تغير! هذا ما لازم يصير.")
    if bad := frozen_ok(manifest):
        sys.exit(f"بصمات تغيرت بعد التطبيق: {bad}")
    print(json.dumps({"data_version": target, "labels_changed": len(changes), "new_columns": new_cols,
                      "dev_by_category": cats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
