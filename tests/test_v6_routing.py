"""القسم المعروض، العملية غير المصرح بها بأي فئة، وارتباط المبلغ بمشكلته. وحفظ الحقول الجديدة بعد إعادة الفتح والتصحيح.

بدون اتصال بالموديل. الرسائل مكتوبة يدوياً ومو من بيانات التطوير أو الضبط أو الاختبار.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masar import config, db, ingest, llm, pipeline

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

ANS = {
    "reason": "x", "category": "other", "secondary_category": None, "transaction_id": None, "amount_iqd": None,
    "amount_issue": None, "date": None, "security_risk": False, "money_issue": False,
    "unauthorized_operation": False, "flags": [], "confidence": 0.9,
}
DRAFT = "وصلتنا شكواك. الحالة تحتاج متابعة من الموظف المختص."
MIXED = "عندي إيداع ٦٥ الف ما نزل بحسابي، وكمان حولت لصاحبي وطلعلي التحويل فشل"
MIXED_ANS = {**ANS, "category": "transfer_failed", "secondary_category": "other", "amount_iqd": 65000,
             "amount_issue": "secondary", "money_issue": True, "flags": ["multiple_issues"]}


def fake_llm(answer, prompts_seen=None, draft=DRAFT):
    def fake(system, user, schema=None, **kw):
        if prompts_seen is not None:
            prompts_seen.append(user)
        if schema:
            return llm.LLMResult(dict(answer), "", 1, 1, 1)
        return llm.LLMResult(None, draft, 1, 1, 1)
    return fake


def run(message, received_at="2026-11-03 10:00", seen=None, **answer):
    with mock.patch.object(llm, "call", fake_llm({**ANS, **answer}, seen)):
        return pipeline.analyze({"message": message, "received_at": received_at})


class UnauthorizedAnyCategoryTests(unittest.TestCase):
    def test_card_with_unauthorized_goes_to_account_security(self):
        out = run("بطاقتي ضاعت البارحة وبعدها شفت شراء بـ٤٥ الف آني ما سويته", category="card",
                  amount_iqd=45000, money_issue=True, unauthorized_operation=True)
        self.assertEqual((out["security_risk"], out["priority"], out["department"]),
                         (1, "high", config.ROUTING["account_access"]))

    def test_transfer_with_unauthorized(self):
        out = run("طلع من حسابي تحويل 70 الف لرقم ما أعرفه وآني ما حولت", category="transfer_failed",
                  amount_iqd=70000, money_issue=True, unauthorized_operation=True)
        self.assertEqual((out["priority"], out["department"]), ("high", config.ROUTING["account_access"]))

    def test_without_declaration_category_routing(self):
        out = run("بطاقتي انرفضت بالسوبرماركت اليوم", category="card")
        self.assertEqual(out["department"], config.ROUTING["card"])


class AmountLinkTests(unittest.TestCase):
    def test_amount_of_second_issue_marked_and_reviewed(self):
        seen = []
        out = run(MIXED, seen=seen, **{k: v for k, v in MIXED_ANS.items() if k != "reason"})
        self.assertEqual((out["amount"], out["amount_issue"]), (65000, "secondary"))
        self.assertIn("amount_for_second_issue", out["review_reasons"])
        draft_prompt = seen[-1]
        self.assertIn("65,000 دينار عراقي (يخص المشكلة الثانية بالرسالة، مو مشكلة تعثر التحويل)", draft_prompt)
        self.assertEqual(pipeline.amount_owner(out), "other")

    def test_unclear_amount_reviewed_without_link(self):
        seen = []
        out = run(MIXED, seen=seen, **{**MIXED_ANS, "amount_issue": "unclear"})
        self.assertIn("amount_issue_unclear", out["review_reasons"])
        self.assertIn("مو واضح يخص أي مشكلة", seen[-1])
        self.assertEqual(pipeline.amount_owner(out), "unclear")

    def test_bad_value_is_unclear(self):
        out = run(MIXED, **{**MIXED_ANS, "amount_issue": None})
        self.assertEqual(out["amount_issue"], "unclear")

    def test_single_issue_amount_is_primary(self):
        seen = []
        out = run("حولت 30 الف لاخوي البارحة وما وصلت", seen=seen, category="transfer_failed", amount_iqd=30000,
                  amount_issue="secondary")
        self.assertEqual(out["amount_issue"], "primary")
        self.assertNotIn("amount_for_second_issue", out["review_reasons"])
        self.assertIn("المبلغ: 30,000 دينار عراقي\n", seen[-1] + "\n")

    def test_old_results_without_field(self):
        self.assertEqual(pipeline.amount_owner({"amount": 1000, "category": "card"}), "card")
        self.assertIsNone(pipeline.amount_owner({"amount": None, "category": "card"}))


class PersistenceAndCorrectionTests(unittest.TestCase):
    """الحقول الجديدة تنحفظ بالقاعدة، وقواعدها تنطبق بعد إعادة الفتح وتصحيح الفئة وإعادة كتابة المسودة."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def process(self, message, answer):
        rows, _ = ingest.parse_csv(f"complaint_id,received_at,message\nN1,2026-11-03 10:00,{message}\n".encode())
        with db.connect(self.path) as conn:
            batch, _ = db.create_batch(conn, "t.csv", rows, [])
            ids = db.batch_ids_to_process(conn, batch)
        with mock.patch.object(llm, "call", fake_llm(answer)):
            pipeline.process(ids, db_path=self.path, workers=1)
        self.cid, self.batch = ids[0], batch
        return self.reopen()

    def reopen(self):
        with db.connect(self.path) as conn:
            return db.batch_rows(conn, self.batch)[0]

    def correct(self, category):
        with db.connect(self.path) as conn:
            db.save_review(conn, self.cid, "needs_followup", "x", category)
        return self.reopen()

    def test_fields_saved_and_unauthorized_survives_correction(self):
        r = self.process("انسحب من رصيدي 80 الف وآني ما سويت شي", {**ANS, "amount_iqd": 80000, "money_issue": True,
                                                                    "unauthorized_operation": True})
        self.assertEqual((r["money_issue"], r["unauthorized_operation"], r["amount_issue"]), (1, 1, "primary"))
        self.assertEqual((r["priority"], r["department"]), ("high", config.ROUTING["account_access"]))
        r = self.correct("card")  # الموظف يصححها بطاقة: تبقى أمن الحسابات وعالية
        self.assertEqual(r["corrected_category"], "card")
        self.assertEqual(pipeline.routing_for(r, r["corrected_category"]), ("high", config.ROUTING["account_access"]))

    def test_correction_applies_full_rules(self):
        r = self.process("انخصم مني 20 الف وما اعرف ليش", {**ANS, "amount_iqd": 20000, "money_issue": True})
        self.assertEqual((r["priority"], r["department"]), ("medium", config.ROUTING["other"]))
        self.assertEqual(pipeline.routing_for(self.correct("wrong_recipient"), "wrong_recipient"),
                         ("high", config.ROUTING["wrong_recipient"]))
        self.assertEqual(pipeline.routing_for(self.correct("card"), "card"), ("medium", config.ROUTING["card"]))
        self.assertEqual(pipeline.routing_for(self.reopen(), "other"), ("medium", config.ROUTING["other"]))

    def test_security_risk_kept_after_correction(self):
        r = self.process("واحد اتصل وأخذ مني الكود وهسه ما افتح الحساب", {**ANS, "category": "account_access",
                                                                         "security_risk": True})
        self.assertEqual(pipeline.routing_for(r, "other"), ("high", config.ROUTING["other"]))

    def test_amount_link_kept_after_reopen_and_redraft(self):
        r = self.process(MIXED, MIXED_ANS)
        self.assertEqual((r["amount_issue"], pipeline.amount_owner(r)), ("secondary", "other"))
        seen = []
        with mock.patch.object(llm, "call", fake_llm(MIXED_ANS, seen)):
            pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertIn("يخص المشكلة الثانية بالرسالة، مو مشكلة تعثر التحويل", seen[-1])
        # الموظف يخلّي الإيداع (أخرى) هو الأساسي: المبلغ يصير مبلغ المشكلة الحالية، والتحويل يصير الثانية
        self.correct("other")
        seen.clear()
        with mock.patch.object(llm, "call", fake_llm(MIXED_ANS, seen)):
            pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertIn("المبلغ: 65,000 دينار عراقي\n", seen[-1] + "\n")
        self.assertIn("ومشكلة ثانية: تعثر التحويل", seen[-1])

    def test_primary_amount_follows_corrected_category(self):
        r = self.process("انسحب من رصيدي 80 الف وآني ما سويت شي", {**ANS, "amount_iqd": 80000, "money_issue": True,
                                                                    "unauthorized_operation": True})
        self.assertEqual(pipeline.amount_owner(r, "card"), "card")
        self.correct("card")
        seen = []
        with mock.patch.object(llm, "call", fake_llm(ANS, seen)):
            pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertIn("المبلغ: 80,000 دينار عراقي\n", seen[-1] + "\n")

    def test_primary_amount_stays_with_first_issue_when_second_promoted(self):
        r = self.process(MIXED, {**MIXED_ANS, "amount_issue": "primary"})
        self.assertEqual(pipeline.amount_owner(r, "other"), "transfer_failed")

    def test_old_db_gets_new_columns(self):
        with db.connect(self.path) as conn:
            have = {r[1] for r in conn.execute("PRAGMA table_info(results)")}
        self.assertTrue({"money_issue", "unauthorized_operation", "amount_issue"} <= have)


class EvaluateV6Tests(unittest.TestCase):
    def row(self, **kw):
        base = {"source_id": "A", "status": "done", "category": "card", "priority": "high",
                "department": config.ROUTING["account_access"], "transaction_id": None, "amount": 45000, "date": None,
                "confidence": 0.9, "review_reasons": [], "message": "x", "reason": "", "latency_ms": 1,
                "input_tokens": 1, "output_tokens": 1, "model": "m", "raw_output": None, "error": None,
                "unauthorized_operation": 1, "amount_issue": "primary", "secondary_category": None}
        return {**base, **kw}

    def label(self, **kw):
        base = {"category": "card", "secondary_category": "", "priority": "high", "department": "البطاقات",
                "transaction_id": "", "amount": "45000", "date": "", "security_risk": "1", "expect_review": "0",
                "hard_type": "", "family_id": "F"}
        return {**base, **kw}

    def test_unauthorized_conflict_with_older_label_reported(self):
        rep = evaluate.score([self.row()], {"A": self.label()}, 0.75)
        self.assertEqual(rep["unauthorized_routing_conflicts"],
                         [{"id": "A", "label_department": "البطاقات", "department": config.ROUTING["account_access"]}])
        self.assertTrue(rep["unauthorized_routing_note"])

    def test_amount_link_scored(self):
        lab = self.label(category="transfer_failed", secondary_category="other", amount_category="other")
        ok = self.row(category="transfer_failed", secondary_category="other", amount_issue="secondary")
        bad = self.row(category="transfer_failed", secondary_category="other", amount_issue="primary")
        self.assertEqual(evaluate.score([ok], {"A": lab}, 0.75)["amount_issue_link"], "1/1")
        self.assertEqual(evaluate.score([bad], {"A": lab}, 0.75)["amount_issue_link"], "0/1")


if __name__ == "__main__":
    unittest.main()
