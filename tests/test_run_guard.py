"""قاطع الدفعة، الاستئناف، منع المعالجة المتداخلة، حماية النتائج، وسجل الطلبات. بدون اتصال بالموديل."""
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from masar import db, ingest, llm, pipeline

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate  # noqa: E402

from test_groq import fake_response, rate_limit_error  # noqa: E402
from test_pipeline import BASE, fake_call  # noqa: E402

CSV = ("complaint_id,received_at,message\n" + "".join(
    f"R{i},2026-10-05 10:00,حولت {i}0 الف البارحة وما وصلت، رقم العملية 1234{i}\n" for i in range(1, 6))).encode()

DAILY = llm.LLMError("خلص حد الاستخدام المجاني حالياً. أعد المحاولة بعد 12 دقيقة تقريباً.", retryable=True,
                     stop_batch=True, retry_after=722.3, limit_kind="tpd")


def counting(fn):
    """يلف البديل الوهمي ويعد شگد مرة انطلب الموديل."""
    calls = []

    def _call(*a, **k):
        calls.append(a[1] if len(a) > 1 else k.get("user"))
        return fn(*a, **k)
    return _call, calls


class GuardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init(self.path)
        rows, skipped = ingest.parse_csv(CSV)
        with db.connect(self.path) as conn:
            self.batch, _ = db.create_batch(conn, "t.csv", rows, skipped)
            self.ids = db.batch_ids_to_process(conn, self.batch)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with db.connect(self.path) as conn:
            return {r["complaint_id"]: r for r in db.batch_rows(conn, self.batch)}

    def runs(self):
        with db.connect(self.path) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM processing_runs ORDER BY started_at, rowid")]


class BreakerTests(GuardBase):
    def stop_on_second(self):
        n = {"i": 0}
        good = fake_call(BASE)

        def _call(system, user, schema=None, **kw):
            if schema:
                n["i"] += 1
                if n["i"] == 2:
                    raise DAILY
            return good(system, user, schema=schema, **kw)
        return _call

    def test_daily_limit_stops_batch(self):
        fn, calls = counting(self.stop_on_second())
        with mock.patch.object(llm, "call", fn):
            s = pipeline.process(self.ids, db_path=self.path, workers=1)
        self.assertEqual((s["done"], s["failed"], s["not_started"]), (1, 1, 3))
        self.assertEqual(len(calls), 3)  # تصنيف+مسودة للأولى، تصنيف للثانية، وبعدها ولا طلب
        r = self.rows()
        self.assertEqual([r[c]["status"] for c in self.ids], ["done", "failed", "pending", "pending", "pending"])
        self.assertEqual([r[c]["attempts"] for c in self.ids], [1, 1, 0, 0, 0])  # اللي ما بدأت ما تزيد
        self.assertTrue(all(r[c]["run_id"] is None for c in self.ids))  # الأقفال انفكت
        run = self.runs()[-1]
        self.assertIn("حد الاستخدام", run["stop_reason"])
        resume = datetime.fromisoformat(run["resume_after"])
        self.assertAlmostEqual((resume - datetime.now(timezone.utc)).total_seconds(), 722, delta=5)
        self.assertEqual((run["requested"], run["started"], run["not_started"]), (5, 2, 3))

    def test_previous_status_kept_when_not_started(self):
        boom = mock.Mock(side_effect=llm.LLMError("فشل بعد 5 محاولات: TPM", retryable=True))
        with mock.patch.object(llm, "call", boom):
            pipeline.process(self.ids[2:], db_path=self.path, workers=1)  # 3 تفشل فشل عادي
        with mock.patch.object(llm, "call", mock.Mock(side_effect=DAILY)):
            s = pipeline.process(self.ids, db_path=self.path, workers=1)
        self.assertEqual((s["failed"], s["not_started"]), (1, 4))
        r = self.rows()
        self.assertEqual([r[c]["status"] for c in self.ids], ["failed", "pending", "failed", "failed", "failed"])
        self.assertEqual([r[c]["attempts"] for c in self.ids], [1, 0, 1, 1, 1])
        self.assertIn("TPM", r[self.ids[3]]["error"])  # خطأها القديم ما تغيّر

    def test_cooldown_blocks_new_run_without_requests(self):
        with mock.patch.object(llm, "call", mock.Mock(side_effect=DAILY)):
            pipeline.process(self.ids, db_path=self.path, workers=1)
        fn, calls = counting(fake_call(BASE))
        with mock.patch.object(llm, "call", fn):
            s = pipeline.process(self.ids, db_path=self.path, workers=1)
        self.assertEqual(calls, [])
        self.assertEqual(s["not_started"], 5)
        self.assertIn("فترة انتظار", s["stopped"])
        self.assertEqual(sum(r["attempts"] for r in self.rows().values()), 1)

    def test_resume_picks_only_unfinished(self):
        with mock.patch.object(llm, "call", self.stop_on_second()):
            pipeline.process(self.ids, db_path=self.path, workers=1)
        with db.connect(self.path) as conn:  # وقت الاستئناف وصل
            conn.execute("UPDATE processing_runs SET resume_after = '2000-01-01T00:00:00+00:00'")
            todo = db.batch_ids_to_process(conn, self.batch)
        self.assertEqual(todo, self.ids[1:])
        before = self.rows()[self.ids[0]]
        fn, calls = counting(fake_call(BASE))
        with mock.patch.object(llm, "call", fn):
            s = pipeline.process(self.ids, db_path=self.path, workers=1)  # حتى لو انرسلت كل القائمة
        self.assertEqual((s["done"], s["skipped"]), (4, 1))
        self.assertEqual(len(calls), 8)  # 4 شكاوى × (تصنيف + مسودة)، المكتملة ما انلمست
        r = self.rows()
        self.assertEqual(r[self.ids[0]], before)
        self.assertEqual([r[c]["attempts"] for c in self.ids], [1, 2, 1, 1, 1])

    def test_long_wait_from_provider_stops_batch(self):
        # رد 429 حقيقي الشكل بانتظار طويل (بدون ذكر يومي): نفس التوقف
        create = mock.MagicMock(side_effect=rate_limit_error(3600))
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            with_raw_response=types.SimpleNamespace(create=create))))
        with mock.patch.object(llm, "_client", return_value=client):
            s = pipeline.process(self.ids, db_path=self.path, workers=1, provider="groq", model="openai/gpt-oss-120b")
        self.assertEqual(create.call_count, 1)
        self.assertEqual((s["failed"], s["not_started"]), (1, 4))
        self.assertIsNotNone(s["resume_after"])

    def test_interrupted_run_starts_nothing_more(self):
        # الواجهة تنقطع بالنص (Streamlit rerun): الشكاوى الباقية ما تبدأ بالخلفية
        fn, calls = counting(fake_call(BASE))

        def progress(i, n, cid, status):
            raise KeyboardInterrupt

        with mock.patch.object(llm, "call", fn), self.assertRaises(KeyboardInterrupt):
            pipeline.process(self.ids, db_path=self.path, workers=1, on_progress=progress)
        # الأولى خلصت، واللي كانت بالنص (إذا بدأت) تكمل وتنحفظ. ما يبدأ شي بعدها.
        r = self.rows()
        done = sum(x["status"] == "done" for x in r.values())
        self.assertIn(done, (1, 2))
        self.assertEqual(len(calls), 2 * done)
        self.assertEqual(sum(x["attempts"] for x in r.values()), done)
        self.assertTrue(all(x["run_id"] is None for x in r.values()))
        self.assertIsNotNone(self.runs()[-1]["finished_at"])  # التشغيل انسجل حتى لو انقطع


class OverlapTests(GuardBase):
    def test_two_runs_same_complaints_process_each_once(self):
        gate, entered = threading.Event(), threading.Event()
        good = fake_call(BASE)
        seen = []

        def slow(system, user, schema=None, **kw):
            if schema:
                seen.append(user)
                entered.set()
                gate.wait(5)
            return good(system, user, schema=schema, **kw)

        out = {}
        with mock.patch.object(llm, "call", slow):
            t = threading.Thread(target=lambda: out.setdefault("a", pipeline.process(self.ids[:1], db_path=self.path)))
            t.start()
            self.assertTrue(entered.wait(5))  # التشغيل الأول ماسك الشكوى هسه
            out["b"] = pipeline.process(self.ids[:1], db_path=self.path)  # تبويب ثاني
            gate.set()
            t.join(5)
        self.assertEqual(len(seen), 1)
        self.assertEqual((out["a"]["done"], out["b"]["skipped"]), (1, 1))
        self.assertEqual(self.rows()[self.ids[0]]["attempts"], 1)

    def test_failure_cannot_overwrite_done_and_review(self):
        # نفس شكل السجل الغريب: قائمة قديمة فيها شكوى انعالجت بعدين، وإعادة محاولة تفشل
        cid = self.ids[0]
        with mock.patch.object(llm, "call", fake_call(BASE)):
            pipeline.process([cid], db_path=self.path)
        with db.connect(self.path) as conn:
            db.save_review(conn, cid, "approved", "الرد المعتمد", None, "ملاحظة")
            review = dict(conn.execute("SELECT * FROM reviews WHERE complaint_id = ?", (cid,)).fetchone())
        before = self.rows()[cid]
        boom = mock.Mock(side_effect=llm.LLMError("فشل بعد 5 محاولات: TPM", retryable=True))
        with mock.patch.object(llm, "call", boom):
            s = pipeline.process([cid], db_path=self.path)
        boom.assert_not_called()
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(self.rows()[cid], before)
        with db.connect(self.path) as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM reviews WHERE complaint_id = ?", (cid,)).fetchone()), review)

    def test_finish_needs_own_lock(self):
        cid = self.ids[0]
        with db.connect(self.path) as conn:
            self.assertTrue(db.claim(conn, cid, "run-a"))
            self.assertFalse(db.claim(conn, cid, "run-b"))
            self.assertFalse(db.finish(conn, cid, "run-b", status="failed", error="x"))
            self.assertTrue(db.finish(conn, cid, "run-a", status="done", error=None))
            self.assertFalse(db.claim(conn, cid, "run-c"))  # مكتملة

    def test_stale_lock_can_be_taken(self):
        cid = self.ids[0]
        old = (datetime.now(timezone.utc) - timedelta(minutes=db.STALE_LOCK_MINUTES + 1)).isoformat(timespec="seconds")
        with db.connect(self.path) as conn:
            conn.execute("UPDATE results SET run_id = 'dead', run_started_at = ? WHERE complaint_id = ?", (old, cid))
            self.assertTrue(db.claim(conn, cid, "run-new"))


class CallLogTests(GuardBase):
    def test_every_http_request_logged_with_stage(self):
        # تصنيف: 429 قصير ثم نجاح. مسودة: انقطاع (length) ثم نجاح.
        create = mock.MagicMock(side_effect=[
            rate_limit_error(1), fake_response(content='{"category": "transfer_failed"}'),
            fake_response(finish="length"), fake_response(content="مسودة")])
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            with_raw_response=types.SimpleNamespace(create=create))))
        answer = {**BASE}
        real_call = llm.call

        def call(*a, **k):
            res = real_call(*a, sleep=lambda s: None, **k)
            if k.get("schema"):
                res.data = dict(answer)
            return res

        with mock.patch.object(llm, "_client", return_value=client), mock.patch.object(llm, "call", call):
            s = pipeline.process(self.ids[:1], db_path=self.path, provider="groq", model="openai/gpt-oss-120b")
        self.assertEqual(s["done"], 1)
        with db.connect(self.path) as conn:
            log = [dict(r) for r in conn.execute("SELECT * FROM llm_calls ORDER BY id")]
        self.assertEqual([(r["stage"], r["request_no"], r["outcome"]) for r in log],
                         [("classify", 1, "rate_limited"), ("classify", 2, "ok"), ("draft", 1, "truncated"), ("draft", 2, "ok")])
        self.assertEqual((log[0]["input_tokens"], log[0]["http_status"]), (None, 429))  # غير معروف، مو صفر
        self.assertEqual((log[2]["input_tokens"], log[2]["output_tokens"]), (10, 5))  # المقطوع صرف توكنات
        self.assertTrue(all(r["run_id"] == s["run_id"] and r["complaint_id"] == self.ids[0] for r in log))
        r = self.rows()[self.ids[0]]
        self.assertEqual(r["attempts"], 1)  # محاولة معالجة وحدة، 4 طلبات HTTP
        with db.connect(self.path) as conn:
            u = db.usage_summary(conn, s["run_id"])
        self.assertEqual(sum(x["n"] for x in u), 4)
        self.assertEqual(sum(x["unknown"] for x in u), 1)

    def test_cached_tokens_and_limit_headers_logged(self):
        ok = fake_response(headers={"x-ratelimit-remaining-tokens": "6100", "x-ratelimit-limit-requests": "1000",
                                    "content-type": "application/json"})
        ok.parse().usage.prompt_tokens_details = types.SimpleNamespace(cached_tokens=640)
        create = mock.MagicMock(side_effect=[rate_limit_error(1), ok])
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            with_raw_response=types.SimpleNamespace(create=create))))
        seen = []
        with mock.patch.object(llm, "_client", return_value=client):
            llm.call("s", "u", provider="groq", model="openai/gpt-oss-120b", sleep=lambda s: None, on_request=seen.append)
        self.assertEqual((seen[0]["cached_tokens"], seen[0]["rate_headers"]), (None, {"retry-after": "1"}))
        self.assertEqual(seen[1]["cached_tokens"], 640)
        self.assertEqual(seen[1]["rate_headers"], {"x-ratelimit-remaining-tokens": "6100", "x-ratelimit-limit-requests": "1000"})
        with db.connect(self.path) as conn:
            db.log_calls(conn, [{**r, "stage": "classify"} for r in seen])
            rows = [dict(r) for r in conn.execute("SELECT cached_tokens, rate_headers FROM llm_calls ORDER BY id")]
        self.assertEqual(rows[1]["cached_tokens"], 640)
        self.assertIn("6100", rows[1]["rate_headers"])

    def test_unknown_draft_tokens_make_total_unknown(self):
        def call(system, user, schema=None, **kw):
            if schema:
                return llm.LLMResult(dict(BASE), "", 100, 50, 5)
            return llm.LLMResult(None, "مسودة", None, None, 5)

        with mock.patch.object(llm, "call", call):
            pipeline.process(self.ids[:1], db_path=self.path)
        r = self.rows()[self.ids[0]]
        self.assertEqual((r["input_tokens"], r["output_tokens"]), (None, None))

    def test_failed_complaint_still_logs_its_requests(self):
        create = mock.MagicMock(side_effect=rate_limit_error(3600))
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            with_raw_response=types.SimpleNamespace(create=create))))
        with mock.patch.object(llm, "_client", return_value=client):
            pipeline.process(self.ids[:1], db_path=self.path, provider="groq", model="openai/gpt-oss-120b")
        with db.connect(self.path) as conn:
            log = [dict(r) for r in conn.execute("SELECT * FROM llm_calls")]
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["outcome"], log[0]["retry_after"], log[0]["input_tokens"]), ("rate_limited", 3600.0, None))


class GroqLimitParsingTests(unittest.TestCase):
    MSG = ("tokens per day (TPD): Limit 200000, Used 199724, Requested 1948. "
           "Please try again in 12m2.304s. Need more tokens?")

    def test_kind_and_wait(self):
        self.assertEqual(llm._limit_kind(self.MSG), "tpd")
        self.assertEqual(llm._limit_kind("tokens per minute (TPM): Limit 8000"), "tpm")
        self.assertAlmostEqual(llm._wait_from_text(self.MSG), 722.304)
        self.assertIsNone(llm._wait_from_text("no hint"))

    def test_daily_limit_after_retries_stops_batch(self):
        def tpd(*a, **k):
            raise llm._Retry("429 TPD", 30, kind="rate_limited", http_status=429, limit_kind="tpd")

        with mock.patch.object(llm, "_anthropic_call", tpd), self.assertRaises(llm.LLMError) as cm:
            llm.call("s", "u", provider="anthropic", sleep=lambda s: None)
        self.assertTrue(cm.exception.stop_batch)

    def test_minute_limit_after_retries_does_not_stop(self):
        def tpm(*a, **k):
            raise llm._Retry("429 TPM", 10, kind="rate_limited", http_status=429, limit_kind="tpm")

        with mock.patch.object(llm, "_anthropic_call", tpm), self.assertRaises(llm.LLMError) as cm:
            llm.call("s", "u", provider="anthropic", sleep=lambda s: None)
        self.assertFalse(cm.exception.stop_batch)


# شكل القاعدة قبل الأقفال وسجل الطلبات (مثل local.nosync/masar.db الحالية)
OLD_SCHEMA = """
CREATE TABLE batches (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, uploaded_at TEXT NOT NULL,
    rows_total INTEGER NOT NULL, rows_skipped INTEGER NOT NULL DEFAULT 0, skipped_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE complaints (id TEXT PRIMARY KEY, source_id TEXT, message TEXT NOT NULL, received_at TEXT, channel TEXT,
    first_batch_id INTEGER NOT NULL REFERENCES batches(id), created_at TEXT NOT NULL);
CREATE TABLE results (complaint_id TEXT PRIMARY KEY REFERENCES complaints(id), status TEXT NOT NULL, category TEXT,
    error TEXT, attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
"""


class MigrationTests(unittest.TestCase):
    def test_old_db_gets_columns_without_touching_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            con = sqlite3.connect(path)
            con.executescript(OLD_SCHEMA)
            con.execute("INSERT INTO batches (filename, uploaded_at, rows_total) VALUES ('x', 't', 1)")
            con.execute("INSERT INTO complaints VALUES ('C1', NULL, 'm', NULL, NULL, 1, 't')")
            con.execute("INSERT INTO results (complaint_id, status, category, attempts, updated_at, error) "
                        "VALUES ('C1', 'failed', 'card', 5, 't', 'e')")
            con.commit()
            self.assertNotIn("run_id", {r[1] for r in con.execute("PRAGMA table_info(results)")})
            con.close()
            db.init(path)
            db.init(path)  # مرتين ما تخرب
            with db.connect(path) as conn:
                r = dict(conn.execute("SELECT * FROM results").fetchone())
            self.assertEqual((r["status"], r["category"], r["attempts"], r["error"], r["run_id"]), ("failed", "card", 5, "e", None))


class RetryRunPathTests(unittest.TestCase):
    def test_relative_absolute_and_symlinked_paths(self):
        with tempfile.TemporaryDirectory() as d:
            runs = evaluate.ROOT / "local.nosync" / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            target = Path(tempfile.mkstemp(suffix=".db", dir=runs)[1])
            try:
                rel = target.relative_to(evaluate.ROOT)
                self.assertEqual(evaluate.run_db_path(str(rel)), target.resolve())
                self.assertEqual(evaluate.run_db_path(str(target)), target.resolve())
                link = Path(d) / "project-link"  # مثل اختصار المشروع على سطح المكتب
                link.symlink_to(evaluate.ROOT)
                via_link = evaluate.run_db_path(str(link / rel))
                self.assertEqual(evaluate.show_path(via_link), str(rel))
            finally:
                target.unlink()
            outside = Path(d) / "x.db"
            outside.write_bytes(b"")
            self.assertEqual(evaluate.show_path(outside), str(outside))  # خارج المشروع: ما يطيح
            with self.assertRaises(SystemExit):
                evaluate.run_db_path(str(Path(d) / "missing.db"))


if __name__ == "__main__":
    unittest.main()
