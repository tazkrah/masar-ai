"""ديمو على نتائج معالجة محفوظة، بدون مفتاح API.

يبني قاعدة SQLite مؤقتة جديدة (برا المستودع) من ملف JSON مصدّر (runs/demo/)، ويفتح الواجهة عليها والمفاتيح فارغة،
فالمعالجة وإعادة الكتابة متوقفة وما ينرسل أي طلب للموديل. قواعد التجارب الأصلية ونتائجها ما تنقرا ولا تتغير.
القرارات اللي تسويها بالديمو تنحفظ بالقاعدة المؤقتة بس، وكل تشغيل يبدأ من جديد.

  ./run.sh demo                 # http://localhost:8501
  ./run.sh demo --build-only    # يبني القاعدة ويطبع مسارها (للفحص)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "runs" / "demo" / "dev-sample-2-v4.json"
JSON_COLS = ("model_flags", "review_reasons", "missing_fields", "raw_output")


def build(data_path, db_path):
    os.environ["MASAR_DB"] = str(db_path)
    from masar import db  # بعد ضبط MASAR_DB

    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    db.init(db_path)
    with db.connect(db_path) as conn:
        batch, _ = db.create_batch(conn, data["source"]["batch_filename"], data["complaints"], [])
        # وقت الدفعة = وقت المعالجة الأصلي، مو وقت بناء الديمو
        conn.execute("UPDATE batches SET uploaded_at = ? WHERE id = ?", (data["source"]["processed_at"], batch))
        for res in data["results"]:
            res = dict(res)
            cid = res.pop("complaint_id")
            for c in JSON_COLS:
                if isinstance(res.get(c), (list, dict)):
                    res[c] = json.dumps(res[c], ensure_ascii=False)
            cols = ", ".join(f"{k} = ?" for k in res)
            conn.execute(f"UPDATE results SET {cols} WHERE complaint_id = ?", (*res.values(), cid))
    versions = "، ".join(data["source"]["prompt_version"])
    return f"نتائج معالجة سابقة — {versions}"


def main(argv):
    data_path = Path(next((a for a in argv if not a.startswith("--")), DEFAULT))
    db_path = Path(tempfile.mkdtemp(prefix="masar-demo-")) / "demo.db"
    label = build(data_path, db_path)
    print(f"قاعدة الديمو: {db_path}")
    if "--build-only" in argv:
        return
    env = {**os.environ, "MASAR_DB": str(db_path), "MASAR_DEMO_LABEL": label,
           "GROQ_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "GEMINI_API_KEY": ""}
    streamlit = Path(sys.executable).with_name("streamlit")
    os.execve(str(streamlit), [str(streamlit), "run", str(ROOT / "app.py"),
                               "--server.port", os.getenv("PORT", "8501")], env)


if __name__ == "__main__":
    main(sys.argv[1:])
