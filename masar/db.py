"""تخزين SQLite: الدفعات، الشكاوى (سجل واحد لكل معرّف ثابت)، النتائج، والمراجعة."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    rows_total INTEGER NOT NULL,
    rows_skipped INTEGER NOT NULL DEFAULT 0,
    skipped_json TEXT NOT NULL DEFAULT '[]'
);

-- سجل واحد لكل شكوى. المعرّف ثابت، فرفع نفس الملف مرة ثانية ما يكرر السجل.
CREATE TABLE IF NOT EXISTS complaints (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    message TEXT NOT NULL,
    received_at TEXT,
    channel TEXT,
    first_batch_id INTEGER NOT NULL REFERENCES batches(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    complaint_id TEXT NOT NULL REFERENCES complaints(id),
    row_number INTEGER NOT NULL,
    PRIMARY KEY (batch_id, complaint_id)
);

-- نتيجة المعالجة الحالية لكل شكوى. إعادة المحاولة تحدّث نفس السطر.
CREATE TABLE IF NOT EXISTS results (
    complaint_id TEXT PRIMARY KEY REFERENCES complaints(id),
    status TEXT NOT NULL,              -- pending | done | failed
    category TEXT,
    priority TEXT,
    department TEXT,
    transaction_id TEXT,
    amount INTEGER,
    date TEXT,
    security_risk INTEGER,
    confidence REAL,
    reason TEXT,
    model_flags TEXT NOT NULL DEFAULT '[]',
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reasons TEXT NOT NULL DEFAULT '[]',
    missing_fields TEXT NOT NULL DEFAULT '[]',
    draft TEXT,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    updated_at TEXT NOT NULL
);

-- قرار الموظف. ما في إرسال تلقائي: الاعتماد يعني جاهز للإرسال اليدوي فقط.
CREATE TABLE IF NOT EXISTS reviews (
    complaint_id TEXT PRIMARY KEY REFERENCES complaints(id),
    decision TEXT NOT NULL,            -- approved | needs_followup
    final_draft TEXT,
    corrected_category TEXT,
    note TEXT,
    reviewed_at TEXT NOT NULL
);
"""

JSON_COLUMNS = ("model_flags", "review_reasons", "missing_fields")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path=None):
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for c in JSON_COLUMNS:
        if c in d and d[c] is not None:
            d[c] = json.loads(d[c])
    return d


def create_batch(conn, filename, rows, skipped):
    """rows: قائمة dict فيها id, source_id, message, received_at, channel, row_number.

    يرجع (batch_id, عدد الشكاوى الجديدة).
    """
    cur = conn.execute(
        "INSERT INTO batches (filename, uploaded_at, rows_total, rows_skipped, skipped_json) VALUES (?,?,?,?,?)",
        (filename, now(), len(rows) + len(skipped), len(skipped), json.dumps(skipped, ensure_ascii=False)),
    )
    batch_id = cur.lastrowid
    new = 0
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO complaints (id, source_id, message, received_at, channel, first_batch_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (r["id"], r.get("source_id"), r["message"], r.get("received_at"), r.get("channel"), batch_id, now()),
        )
        new += cur.rowcount
        conn.execute(
            "INSERT OR IGNORE INTO batch_items (batch_id, complaint_id, row_number) VALUES (?,?,?)",
            (batch_id, r["id"], r["row_number"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO results (complaint_id, status, updated_at) VALUES (?, 'pending', ?)",
            (r["id"], now()),
        )
    return batch_id, new


def save_result(conn, complaint_id, **fields):
    fields = dict(fields)
    for c in JSON_COLUMNS:
        if c in fields:
            fields[c] = json.dumps(fields[c], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE results SET {cols} WHERE complaint_id = ?", (*fields.values(), complaint_id))


def bump_attempts(conn, complaint_id):
    conn.execute("UPDATE results SET attempts = attempts + 1 WHERE complaint_id = ?", (complaint_id,))


def batch_ids_to_process(conn, batch_id, statuses=("pending", "failed")):
    q = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT bi.complaint_id FROM batch_items bi JOIN results r ON r.complaint_id = bi.complaint_id "
        f"WHERE bi.batch_id = ? AND r.status IN ({q}) ORDER BY bi.row_number",
        (batch_id, *statuses),
    ).fetchall()
    return [r[0] for r in rows]


def get_complaint(conn, complaint_id):
    return dict(conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone())


def list_batches(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM batches ORDER BY id DESC")]


def batch_rows(conn, batch_id):
    """كل شكاوى الدفعة مع نتيجتها وقرار المراجعة."""
    rows = conn.execute(
        """
        SELECT c.*, bi.row_number, r.*, v.decision, v.final_draft, v.corrected_category, v.note, v.reviewed_at
        FROM batch_items bi
        JOIN complaints c ON c.id = bi.complaint_id
        JOIN results r ON r.complaint_id = c.id
        LEFT JOIN reviews v ON v.complaint_id = c.id
        WHERE bi.batch_id = ?
        ORDER BY bi.row_number
        """,
        (batch_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def save_review(conn, complaint_id, decision, final_draft, corrected_category=None, note=None):
    conn.execute(
        "INSERT INTO reviews (complaint_id, decision, final_draft, corrected_category, note, reviewed_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(complaint_id) DO UPDATE SET decision=excluded.decision, "
        "final_draft=excluded.final_draft, corrected_category=excluded.corrected_category, "
        "note=excluded.note, reviewed_at=excluded.reviewed_at",
        (complaint_id, decision, final_draft, corrected_category, note, now()),
    )
