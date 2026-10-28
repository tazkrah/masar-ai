"""اختبارات منطق الكود بدون اتصال بالموديل (الموديل مستبدل ببديل وهمي هنا فقط).

تشغيل:  .venv/bin/python -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masar import config, db, ingest, llm, pipeline


def fake_call(answer, draft="وصلتنا شكواك وراح يتابعها القسم المختص."):
    def _call(system, user, schema=None, **kw):
        if schema:
            return llm.LLMResult(dict(answer), "", 10, 10, 5)
        return llm.LLMResult(None, draft, 10, 10, 5)
    return _call


BASE = {
    "reason": "حوّل وما وصل", "category": "transfer_failed", "secondary_category": None,
    "transaction_id": "12345", "amount_iqd": 50000, "date": "2026-10-04",
    "security_risk": False, "flags": [], "confidence": 0.95,
}
CSV = "complaint_id,received_at,message\nX1,2026-10-05 10:00,حولت ٥٠ الف وانخصمت بس ما وصلت، رقم العملية ١٢٣٤٥\nX2,2026-10-05 11:00,عندي مشكلة\n,,\n".encode()


class IngestTests(unittest.TestCase):
    def test_parse_and_stable_id(self):
        rows, skipped = ingest.parse_csv(CSV)
        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped[0]["reason"], "رسالة فارغة")
        rows2, _ = ingest.parse_csv(CSV)
        self.assertEqual([r["id"] for r in rows], [r["id"] for r in rows2])

    def test_missing_message_column(self):
        with self.assertRaises(ingest.CSVError):
            ingest.parse_csv("a,b\n1,2\n".encode())

    def test_id_from_text_when_no_id_column(self):
        data = "message\nحولت وما وصلت\nحولت   وما وصلت\n".encode()
        rows, skipped = ingest.parse_csv(data)
        self.assertEqual(len(rows), 1)  # نفس النص بعد التوحيد = نفس الشكوى
        self.assertEqual(skipped[0]["reason"], "مكررة بنفس الملف")


class ValidateTests(unittest.TestCase):
    def test_arabic_digits_txid_ok(self):
        f, r = pipeline.validate(BASE, "حولت البارحة، رقم العملية ١٢٣٤٥", "2026-10-05")
        self.assertEqual(f["transaction_id"], "12345")
        self.assertEqual(r, [])

    def test_hallucinated_txid_cleared(self):
        f, r = pipeline.validate({**BASE, "transaction_id": "99999"}, "رقم العملية 12345", "2026-10-05")
        self.assertIsNone(f["transaction_id"])
        self.assertIn("invalid_transaction_id", r)

    def test_future_date_rejected(self):
        f, r = pipeline.validate({**BASE, "date": "2026-10-09"}, "12345", "2026-10-05 10:00")
        self.assertIsNone(f["date"])
        self.assertIn("invalid_date", r)

    def test_priority_rules(self):
        self.assertEqual(config.priority_for("transfer_failed", 50000, False), "medium")
        self.assertEqual(config.priority_for("transfer_failed", 1_500_000, False), "high")
        self.assertEqual(config.priority_for("card", None, True), "high")
        self.assertEqual(config.priority_for("other", None, False), "low")


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _upload(self):
        rows, skipped = ingest.parse_csv(CSV)
        with db.connect(self.path) as conn:
            return db.create_batch(conn, "t.csv", rows, skipped)

    def test_reupload_does_not_duplicate(self):
        b1, new1 = self._upload()
        b2, new2 = self._upload()
        self.assertEqual((new1, new2), (2, 0))
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM results").fetchone()[0], 2)

    def test_failure_then_retry_updates_same_record(self):
        batch, _ = self._upload()
        with db.connect(self.path) as conn:
            ids = db.batch_ids_to_process(conn, batch)

        def boom(*a, **k):
            raise llm.LLMError("فشل بعد 5 محاولات: وصلنا حد الاستخدام (429).", retryable=True)

        with mock.patch.object(llm, "call", boom):
            s = pipeline.process(ids, db_path=self.path, workers=1)
        self.assertEqual((s["done"], s["failed"], s["stopped"]), (0, 2, None))  # فشل مؤقت قصير: الدفعة تكمل

        with db.connect(self.path) as conn:
            failed = db.batch_ids_to_process(conn, batch, statuses=("failed",))
        with mock.patch.object(llm, "call", fake_call(BASE)):
            s = pipeline.process(failed, db_path=self.path, workers=1)
        self.assertEqual((s["done"], s["failed"]), (2, 0))
        with db.connect(self.path) as conn:
            rows = db.batch_rows(conn, batch)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM results").fetchone()[0], 2)
        self.assertTrue(all(r["attempts"] == 2 and r["status"] == "done" and r["error"] is None for r in rows))

    def test_review_reasons(self):
        batch, _ = self._upload()
        with db.connect(self.path) as conn:
            ids = db.batch_ids_to_process(conn, batch)
        ans = {**BASE, "confidence": 0.4, "flags": ["possible_injection"], "transaction_id": None}
        with mock.patch.object(llm, "call", fake_call(ans, draft="راح نرجعلك فلوسك")):
            pipeline.process(ids[:1], db_path=self.path, workers=1)
        with db.connect(self.path) as conn:
            r = db.batch_rows(conn, batch)[0]
        self.assertEqual(r["needs_review"], 1)
        for reason in ("low_confidence", "possible_injection", "draft_forbidden_phrase"):
            self.assertIn(reason, r["review_reasons"])
        self.assertIn("transaction_id", r["missing_fields"])

    def test_retry_backoff_then_success(self):
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise llm._Retry("429", None)
            return '{"a": 1}', 1, 1

        sleeps = []
        with mock.patch.object(llm, "_anthropic_call", flaky):
            res = llm.call("s", "u", schema={"type": "object"}, provider="anthropic", sleep=sleeps.append)
        self.assertEqual(res.data, {"a": 1})
        self.assertEqual(len(sleeps), 2)
        self.assertLess(sleeps[0], sleeps[1])


G07_5 = "هلا، أودعت 2,500,000 دينار عند وكيل وما نزل بالحساب. الوكيل ما يرد عليّ والموضوع قلقني، رجاءً حلّوه بسرعة."


class DateGroundingTests(unittest.TestCase):
    def test_time_reference_detection(self):
        self.assertFalse(pipeline.has_time_reference(G07_5))
        for m in ("حولت البارحة وما وصل", "صار هالشي قبل يومين", "بتاريخ ٢/١٠ انخصم", "يوم 8 الشهر حولت",
                  "التحويل بتاريخ 2026-10-11", "امس سحبت من الصراف", "الصبح انرفضت البطاقة"):
            self.assertTrue(pipeline.has_time_reference(m), m)
        self.assertFalse(pipeline.has_time_reference("رقم العملية 48213097 بمبلغ 75 الف"))
        self.assertFalse(pipeline.has_time_reference("شنو حدود السحب اليومية؟"))  # اليومية مو اليوم

    def test_g07_5_received_date_is_not_transaction_date(self):
        """حالة DEV-G07-5: الموديل حط تاريخ الاستلام والرسالة ما بيها أي تاريخ."""
        ans = {**BASE, "category": "agent_dispute", "transaction_id": None, "amount_iqd": 2500000,
               "date": "2026-10-03"}
        f, r = pipeline.validate(ans, G07_5, "2026-10-03 09:10")
        self.assertIsNone(f["date"])
        self.assertIn("unsupported_date", r)
        self.assertEqual(f["amount"], 2500000)

        prompts_seen = []

        def fake(system, user, schema=None, **kw):
            prompts_seen.append(user)
            if schema:
                return llm.LLMResult(dict(ans), "", 1, 1, 1)
            return llm.LLMResult(None, "وصلتنا شكواك بخصوص إيداع 2,500,000 دينار عراقي بتاريخ 2026-10-03.", 1, 1, 1)

        with mock.patch.object(llm, "call", fake):
            out = pipeline.analyze({"message": G07_5, "received_at": "2026-10-03 09:10"})
        self.assertIsNone(out["date"])
        self.assertNotIn("2026-10-03", prompts_seen[1])  # التاريخ ما وصل لتعليمات المسودة
        self.assertIn("unsupported_date", out["review_reasons"])
        self.assertIn("draft_unsupported_date", out["review_reasons"])  # وإذا الموديل كتبه بالمسودة، ينمسك
        self.assertEqual(out["needs_review"], 1)

    def test_supported_relative_date_kept(self):
        f, r = pipeline.validate({**BASE, "date": "2026-10-04"}, "حولت البارحة رقم العملية 12345", "2026-10-05 10:00")
        self.assertEqual(f["date"], "2026-10-04")
        self.assertEqual(r, [])

    def test_draft_date_from_message_is_ok(self):
        self.assertEqual(pipeline.draft_unsupported_dates("التحويل بتاريخ 2026-10-11", "بتاريخ 2026-10-11 حولت", None), [])
        self.assertEqual(pipeline.draft_unsupported_dates("بتاريخ 2026-10-04", "حولت البارحة", "2026-10-04"), [])


class AmountFormatTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(config.format_amount(75000), "75,000 دينار عراقي")
        self.assertIsNone(config.format_amount(None))

    def test_draft_amount_variants_unified(self):
        f = pipeline.format_amounts_in_draft
        target = "75,000 دينار عراقي"
        for raw in ("75000", "75,000 د.ع", "٧٥ الف دينار", "75 ألف", "75,000 دينار عراقي", "٧٥٬٠٠٠ دينار"):
            out = f(f"المبلغ {raw} وصلنا.", 75000)
            self.assertEqual(out, f"المبلغ {target} وصلنا.", raw)

    def test_other_numbers_untouched(self):
        out = pipeline.format_amounts_in_draft("رقم العملية 48213097 بمبلغ 75000.", 75000)
        self.assertEqual(out, "رقم العملية 48213097 بمبلغ 75,000 دينار عراقي.")
        self.assertEqual(pipeline.format_amounts_in_draft("رقم 48213097", None), "رقم 48213097")
        self.assertEqual(pipeline.format_amounts_in_draft("حولت 75000، وراح", 75000), "حولت 75,000 دينار عراقي، وراح")


class GuideTests(unittest.TestCase):
    def test_guide_sections_exist(self):
        from masar import prompts
        for cat in config.CATEGORIES:
            _, section = prompts.guide_section(cat)
            self.assertTrue(section.startswith(f"## {cat}"), cat)
        self.assertIn("نضمن", prompts.forbidden_phrases())


if __name__ == "__main__":
    unittest.main()
