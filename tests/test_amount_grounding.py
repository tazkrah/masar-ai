"""المبلغ المستخرج والمبالغ بالمسودة لازم تطابق مبلغ مكتوب بالرسالة بأي صيغة. بدون اتصال بالموديل.

الرسائل مكتوبة يدوياً ومو من بيانات التطوير أو الضبط أو الاختبار.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masar import config, db, ingest, llm, pipeline

ANS = {
    "reason": "x", "category": "transfer_failed", "secondary_category": None, "transaction_id": None,
    "amount_iqd": None, "amount_issue": None, "date": None, "security_risk": False, "money_issue": True,
    "unauthorized_operation": False, "flags": [], "confidence": 0.9,
}
GOOD_DRAFT = "وصلتنا شكواك. الحالة تحتاج متابعة من الموظف المختص."


def run(message, draft=GOOD_DRAFT, seen=None, **answer):
    def fake(system, user, schema=None, **kw):
        if seen is not None:
            seen.append(user)
        if schema:
            return llm.LLMResult({**ANS, **answer}, "", 1, 1, 1)
        return llm.LLMResult(None, draft, 1, 1, 1)

    with mock.patch.object(llm, "call", fake):
        return pipeline.analyze({"message": message, "received_at": "2026-11-03 10:00"})


def amount_check(message, amount, txid=None):
    fields, reasons = pipeline.validate({**ANS, "amount_iqd": amount, "transaction_id": txid}, message,
                                        "2026-11-03 10:00")
    return fields["amount"], reasons


class ExtractedAmountTests(unittest.TestCase):
    def test_words_misread_cleared_and_raw_kept(self):
        seen = []
        out = run("حطيت إيداع بخمسمية الف من يومين وما نزل", seen=seen, category="other", amount_iqd=5000,
                  flags=["unclear_message"])
        self.assertIsNone(out["amount"])
        self.assertIn("amount_not_in_message", out["review_reasons"])
        self.assertEqual(out["raw_output"]["amount_iqd"], 5000)  # جواب الموديل الخام يبقى
        self.assertIsNone(out["amount_issue"])
        self.assertNotIn("5,000", seen[-1])  # المبلغ الغلط ما وصل لتعليمات المسودة
        self.assertNotIn("المبلغ:", seen[-1])

    def test_correct_forms_accepted(self):
        for msg, amount in (("حولت ٨٥ الف لاخوي وما وصلت", 85000), ("حولت 85,000 دينار وما وصلت", 85000),
                            ("حولت ٨٥٬٠٠٠ دينار عراقي", 85000), ("حولت 85000 وما وصلت", 85000),
                            ("حولت خمسة وثمانين الف وما وصلت", 85000), ("دزيت مية وعشرين الف", 120000),
                            ("حولت ربع مليون لشركة", 250000), ("حولت مليون ونص للمورد", 1_500_000),
                            ("حولت 1.5 مليون للمورد", 1_500_000), ("حولت 3 مليون للمورد", 3_000_000),
                            ("حولت ميتين الف بالغلط", 200000)):
            self.assertEqual(amount_check(msg, amount), (amount, []), msg)

    def test_wrong_value_never_corrected(self):
        """القيمة الغلط تنشال، وما نبدّلها بالمبلغ المكتوب حتى لو واحد بس."""
        for msg, amount in (("حولت ٨٥ الف وما وصلت", 85), ("حولت ٨٥ الف وما وصلت", 850000),
                            ("حولت 85 لاخوي", 85000), ("حولت فلوس لاخوي", 40000)):
            got, reasons = amount_check(msg, amount)
            self.assertIsNone(got, (msg, amount))
            self.assertTrue({"amount_not_in_message", "invalid_amount"} & set(reasons), (msg, amount))

    def test_multiple_amounts_kept_only_if_written_and_reviewed(self):
        msg = "سحبت 200 الف من الوكيل وأخذ عمولة عشرة الاف"
        self.assertEqual(amount_check(msg, 10000), (10000, ["multiple_amounts"]))
        self.assertEqual(amount_check(msg, 200000), (200000, ["multiple_amounts"]))
        got, reasons = amount_check(msg, 210000)  # ما نجمع ولا نختار
        self.assertIsNone(got)
        self.assertIn("amount_not_in_message", reasons)

    def test_transaction_id_and_dates_are_not_amounts(self):
        cases = (("رقم العملية 45120000 وما وصل التحويل", 45120000, None),
                 ("حولت بتاريخ 2026-10-05 وما وصلت", 2026, None),
                 ("التحويل #77001234 ما وصل", 77001234, None),
                 ("حوالة رقمها 3310 2288 ما وصلت", 33102288, None),
                 ("حولت يوم 25/10 وما وصلت", 2510, None),
                 ("حولت 30 الف والعملية 88221100", 88221100, "88221100"))
        for msg, amount, txid in cases:
            got, reasons = amount_check(msg, amount, txid)
            self.assertIsNone(got, msg)
            self.assertIn("amount_not_in_message", reasons, msg)
        self.assertEqual(amount_check("حولت 30 الف والعملية 88221100", 30000, "88221100"), (30000, []))
        self.assertEqual(amount_check("بتاريخ 2026-10-05 حولت 1,200,000 دينار رقم 55001234", 1_200_000), (1_200_000, []))

    def test_ungrounded_big_amount_does_not_raise_priority(self):
        out = run("حولت مبلغ لشركة وما وصل", amount_iqd=2_000_000)
        self.assertIsNone(out["amount"])
        self.assertEqual(out["priority"], "medium")

    def test_amount_link_kept_when_grounded(self):
        out = run("إيداعي بـ٦٠ الف ما نزل والتحويل الثاني فشل", category="transfer_failed", secondary_category="other",
                  amount_iqd=60000, amount_issue="secondary", flags=["multiple_issues"])
        self.assertEqual((out["amount"], out["amount_issue"]), (60000, "secondary"))
        self.assertNotIn("amount_not_in_message", out["review_reasons"])


class DraftAmountTests(unittest.TestCase):
    MSG = "عندي إيداع بخمسمية الف ما نزل والتحويل الثاني فشل، رقم العملية 66120934"

    def test_wrong_amount_in_draft_flagged(self):
        d = "وصلتنا شكواك بخصوص إيداع 5,000 دينار عراقي ما نزل. الحالة تحتاج متابعة من الموظف المختص."
        self.assertEqual(pipeline.draft_unsupported_amounts(d, self.MSG), ["5,000 دينار عراقي"])
        out = run(self.MSG, draft=d, category="other", amount_iqd=500000, transaction_id="66120934")
        self.assertIn("draft_unsupported_amount", out["review_reasons"])

    def test_correct_amount_tx_and_dates_in_draft_ok(self):
        for d in ("وصلتنا شكواك بخصوص إيداع 500,000 دينار عراقي ما نزل، والتحويل رقم 66120934 اللي فشل بتاريخ 2026-11-02.",
                  "بخصوص الإيداع بخمسمية الف، رقم العملية ٦٦١٢٠٩٣٤، يوم 2/11.",
                  "بخصوص الإيداع (500 ألف) والتحويل 66120934 بتاريخ 02‑11‑2026."):
            self.assertEqual(pipeline.draft_unsupported_amounts(d, self.MSG, "66120934"), [], d)

    def test_draft_amount_when_message_has_none(self):
        d = "وصلتنا شكواك بخصوص تحويل 40,000 دينار عراقي."
        self.assertEqual(pipeline.draft_unsupported_amounts(d, "حولت لاخوي وما وصل"), ["40,000 دينار عراقي"])
        self.assertEqual(pipeline.draft_unsupported_amounts("الحالة تحتاج متابعة.", "حولت لاخوي وما وصل"), [])

    def test_unit_changes_value(self):
        msg = "حولت ٢٥٠ الف وما وصلت"
        self.assertEqual(pipeline.draft_unsupported_amounts("بخصوص تحويل 250 دينار.", msg), ["250 دينار"])
        self.assertEqual(pipeline.draft_unsupported_amounts("بخصوص تحويل 250,000 دينار عراقي.", msg), [])

    def test_approval_blocker(self):
        self.assertTrue(any(b.startswith("مبلغ") for b in pipeline.approval_blockers(
            "بخصوص إيداع 5,000 دينار عراقي. الحالة تحتاج متابعة من الموظف المختص.", self.MSG, {})))


class ApprovalAmountTests(unittest.TestCase):
    MSG = "حطيت إيداع بخمسمية الف وما نزل لهسه"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init(self.path)
        rows, _ = ingest.parse_csv(f"complaint_id,received_at,message\nM1,2026-11-03 10:00,{self.MSG}\n".encode())
        with db.connect(self.path) as conn:
            db.create_batch(conn, "t.csv", rows, [])
            conn.execute("UPDATE results SET status='done', category='other' WHERE complaint_id = ?", (rows[0]["id"],))
        self.cid = rows[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_staff_edit_with_wrong_amount_blocked(self):
        with db.connect(self.path) as conn:
            with self.assertRaises(db.DraftBlocked) as ctx:
                db.save_review(conn, self.cid, "approved",
                               "وصلنا شكواك بخصوص إيداع 5,000 دينار عراقي. الحالة تحتاج متابعة من الموظف المختص.")
            self.assertTrue(any("5,000" in b for b in ctx.exception.blockers))
            db.save_review(conn, self.cid, "approved",
                           "وصلنا شكواك بخصوص إيداع 500,000 دينار عراقي. الحالة تحتاج متابعة من الموظف المختص.")

    def test_regenerated_draft_checked(self):
        with mock.patch.object(llm, "call", lambda *a, **k: llm.LLMResult(
                None, "بخصوص إيداع 5000 دينار، الحالة تحتاج متابعة من الموظف المختص.", 1, 1, 1)):
            _, found = pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertTrue(any(f.startswith("مبلغ") for f in found), found)


class ReasonLabelTests(unittest.TestCase):
    def test_labels_exist(self):
        for k in ("amount_not_in_message", "multiple_amounts", "draft_unsupported_amount"):
            self.assertIn(k, config.REVIEW_REASON_LABELS)


if __name__ == "__main__":
    unittest.main()
