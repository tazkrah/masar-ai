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
    attempts INTEGER NOT NULL DEFAULT 0,  -- مرات بدء معالجة الشكوى (مو عدد طلبات HTTP، هذي بـ llm_calls)
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    updated_at TEXT NOT NULL,
    run_id TEXT,                       -- قفل: التشغيل اللي يعالج الشكوى هسه (NULL = محد ماسكها)
    run_started_at TEXT
);

-- كل تشغيل معالجة: شگد طلب، شگد بدأ، وليش وقف ووكت الاستئناف إذا المزوّد ذكره.
CREATE TABLE IF NOT EXISTS processing_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requested INTEGER NOT NULL,
    started INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,     -- مكتملة أو تعالجها عملية ثانية، فما انلمست
    not_started INTEGER NOT NULL DEFAULT 0, -- ما بدأت لأن الدفعة وقفت
    stop_reason TEXT,
    resume_after TEXT
);

-- كل طلب HTTP للموديل. التوكنات NULL = المزوّد ما رجّعها (غير معروفة، مو صفر).
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    complaint_id TEXT,
    stage TEXT NOT NULL,               -- classify | draft | redraft
    request_no INTEGER NOT NULL,       -- 1 = أول طلب، 2+ = إعادة داخل نفس الاستدعاء
    outcome TEXT NOT NULL,             -- ok | rate_limited | truncated | invalid_json | schema_rejected | server_error | connection_error | error
    http_status INTEGER,
    input_tokens INTEGER,              -- يشمل المقروء من الكاش
    output_tokens INTEGER,
    cached_tokens INTEGER,             -- من الإدخال، انقرت من الكاش (ما تنحسب على الحدود حسب Groq)
    rate_headers TEXT,                 -- JSON: رؤوس x-ratelimit-* و retry-after مثل ما رجعت
    usage_raw TEXT,                    -- JSON: حقل usage من الرد كما هو (أرقام فقط، بدون نص الشكوى)
    retry_after REAL,
    limit_kind TEXT,                   -- tpd | rpd | tpm | rpm
    latency_ms INTEGER,
    error TEXT,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL
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


# أعمدة انضافت بعد أول نسخة. القواعد القديمة تنحدّث بإضافتها فقط، بدون ما نغيّر أي سطر موجود.
MIGRATIONS = {"results": [("run_id", "TEXT"), ("run_started_at", "TEXT")],
              "llm_calls": [("cached_tokens", "INTEGER"), ("rate_headers", "TEXT"), ("usage_raw", "TEXT")]}


def init(path=None):
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


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


# قفل المعالجة أطول من هذا يعتبر متروك (العملية انقطعت بالنص) ويصير ينمسك من جديد.
STALE_LOCK_MINUTES = 30
CLAIMABLE = ("pending", "failed")


def claim(conn, complaint_id, run_id):
    """يمسك الشكوى لهذا التشغيل إذا ما مكتملة ومحد ثاني ماسكها. يرجع True إذا انمسكت.

    تحديث واحد ذري بـ SQLite، فتشغيلين (تبويبين أو عمليتين) ما يگدرون يمسكون نفس الشكوى.
    المحاولات تزيد هنا بس، يعني فقط للشكوى اللي بدأت معالجتها فعلاً.
    """
    stale = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - STALE_LOCK_MINUTES * 60,
                                   timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        f"UPDATE results SET run_id = ?, run_started_at = ?, attempts = attempts + 1 "
        f"WHERE complaint_id = ? AND status IN ({','.join('?' * len(CLAIMABLE))}) "
        f"AND (run_id IS NULL OR run_started_at < ?)",
        (run_id, now(), complaint_id, *CLAIMABLE, stale),
    )
    return cur.rowcount == 1


def finish(conn, complaint_id, run_id, **fields):
    """يحفظ نتيجة التشغيل ويفك القفل، بس إذا القفل بعده لهذا التشغيل والشكوى مو مكتملة.

    هيج فشل محاولة ثانية ما يگدر يكتب فوق نتيجة ناجحة. يرجع True إذا انحفظت.
    """
    fields = dict(fields, run_id=None, run_started_at=None)
    for c in JSON_COLUMNS:
        if c in fields:
            fields[c] = json.dumps(fields[c], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE results SET {cols} WHERE complaint_id = ? AND run_id = ? AND status != 'done'",
                       (*fields.values(), complaint_id, run_id))
    return cur.rowcount == 1


def release(conn, complaint_id, run_id):
    conn.execute("UPDATE results SET run_id = NULL, run_started_at = NULL WHERE complaint_id = ? AND run_id = ?",
                 (complaint_id, run_id))


def start_run(conn, run_id, requested):
    conn.execute("INSERT INTO processing_runs (id, started_at, requested) VALUES (?,?,?)", (run_id, now(), requested))


def end_run(conn, run_id, **counts):
    counts["finished_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in counts)
    conn.execute(f"UPDATE processing_runs SET {cols} WHERE id = ?", (*counts.values(), run_id))


def active_cooldown(conn):
    """آخر تشغيل وقف بسبب الحد وبعده ما وصل وقت الاستئناف: يرجع (السبب، الوقت) وإلا None."""
    r = conn.execute("SELECT stop_reason, resume_after FROM processing_runs WHERE resume_after IS NOT NULL "
                     "ORDER BY finished_at DESC LIMIT 1").fetchone()
    if r and r["resume_after"] > now():
        return r["stop_reason"], r["resume_after"]
    return None


def log_calls(conn, records):
    conn.executemany(
        "INSERT INTO llm_calls (run_id, complaint_id, stage, request_no, outcome, http_status, input_tokens, "
        "output_tokens, cached_tokens, rate_headers, usage_raw, retry_after, limit_kind, latency_ms, error, provider, "
        "model, created_at) VALUES (:run_id, :complaint_id, :stage, :request_no, :outcome, :http_status, "
        ":input_tokens, :output_tokens, :cached_tokens, :rate_headers, :usage_raw, :retry_after, :limit_kind, "
        ":latency_ms, :error, :provider, :model, :created_at)",
        [{"run_id": None, "complaint_id": None, "provider": None, "model": None, "created_at": now(),
          "cached_tokens": None, **r,
          "rate_headers": json.dumps(r["rate_headers"]) if r.get("rate_headers") else None,
          "usage_raw": json.dumps(r["usage_raw"]) if r.get("usage_raw") else None}
         for r in records],
    )


def usage_summary(conn, run_id=None):
    """ملخص الطلبات والتوكنات من llm_calls. الطلبات اللي توكناتها غير معروفة تنعد لحالها."""
    where, args = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
    rows = conn.execute(
        f"SELECT stage, outcome, COUNT(*) n, SUM(input_tokens) tin, SUM(cached_tokens) cached, "
        f"SUM(output_tokens) tout, SUM(input_tokens IS NULL OR output_tokens IS NULL) unknown, "
        f"SUM(cached_tokens IS NULL) cached_unknown FROM llm_calls {where} "
        f"GROUP BY stage, outcome ORDER BY stage, outcome", args).fetchall()
    return [dict(r) for r in rows]


def batch_ids_to_process(conn, batch_id, statuses=CLAIMABLE):
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
