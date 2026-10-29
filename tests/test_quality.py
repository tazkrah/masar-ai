"""اختبارات جودة التبرير والمسودة، وتقرير الخام مقابل بعد الحماية. بدون اتصال بالموديل.

الرسائل هنا مكتوبة يدوياً ومو مأخوذة من بيانات التطوير أو الضبط أو الاختبار.
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from masar import config, llm, pipeline, prompts

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

ANS = {
    "reason": "يگول التحويل ما وصل.", "category": "transfer_failed", "secondary_category": None,
    "transaction_id": None, "amount_iqd": None, "date": None,
    "security_risk": False, "flags": [], "confidence": 0.95,
}


def run_analyze(message, answer, draft, received_at="2026-11-02 10:00"):
    seen = []

    def fake(system, user, schema=None, **kw):
        seen.append((system, user))
        if schema:
            return llm.LLMResult(dict(answer), "", 1, 1, 1)
        return llm.LLMResult(None, draft, 1, 1, 1)

    with mock.patch.object(llm, "call", fake):
        out = pipeline.analyze({"message": message, "received_at": received_at})
    return out, seen


class ReasonGroundingTests(unittest.TestCase):
    def test_debit_claim_without_debit_in_message(self):
        msg = "دزيت فلوس لأخوي من الصبح وهو يگول بعده ما شاف شي"
        self.assertIn("خصم", pipeline.reason_unsupported("انخصم المبلغ والمستلم ما وصله.", msg))
        self.assertEqual(pipeline.reason_unsupported("حوّل لأخوه والمستلم يگول ما وصله.", msg), [])

    def test_debit_claim_ok_when_message_says_it(self):
        msg = "انخصمت الفلوس من رصيدي بس الطرف الثاني ما استلم"
        self.assertEqual(pipeline.reason_unsupported("انخصم المبلغ وما وصل للمستلم.", msg), [])

    def test_ambiguous_wording_not_turned_into_fact(self):
        msg = "طلبت كرت جديد والفلوس مدفوعة بس لهسه ماكو شي"
        self.assertIn("خصم", pipeline.reason_unsupported("طلب بطاقة وما وصلت رغم خصم المبلغ.", msg))
        self.assertEqual(pipeline.reason_unsupported("طلب بطاقة ويگول دافع وبعدها ما وصلت.", msg), [])

    def test_copied_example_reason(self):
        ex_reason = prompts.FEW_SHOT[0][2]["reason"]
        msg = "عندي حوالة بالأمس، رقمها 77881234، ممكن تشوفوها"
        self.assertIn("منسوخ من مثال", pipeline.reason_unsupported(ex_reason, msg))

    def test_numbers_not_in_message(self):
        self.assertTrue(pipeline.reason_unsupported("حوّل 300 ألف وما وصلت.", "حولت فلوس وما وصلت"))
        self.assertEqual(pipeline.reason_unsupported("الزبون حول وما وصلت.", "حولت ٣٠٠ الف وما وصلت"), [])

    def test_unsupported_reason_goes_to_review(self):
        msg = "حولت لصديقي البارحة وهو يگول ما وصله"
        out, _ = run_analyze(msg, {**ANS, "reason": "انخصم المبلغ من رصيد المرسل والمستلم يگول ما وصله."},
                             "وصلتنا شكوى التحويل، والحالة تحتاج متابعة من الموظف المختص. ممكن تزودنا برقم العملية؟")
        self.assertIn("reason_unsupported", out["review_reasons"])
        self.assertEqual(out["needs_review"], 1)


class DraftActionTests(unittest.TestCase):
    def test_claimed_actions_flagged(self):
        for d in ("تم إيقاف بطاقتك بنجاح.", "رفعنا طلب إيقاف للبطاقة.", "تمت مراجعة العملية وراح نبلغك.",
                  "تواصلنا ويا الوكيل بخصوص المبلغ."):
            self.assertTrue(pipeline.unsupported_actions_in_draft(d), d)

    def test_referral_claims_flagged(self):
        """تحديد القسم داخل مسار مو إحالة فعلية: ما نگول وصل لقسم أو الموظف راح يراجعه."""
        for d in ("وصل طلبك لقسم البطاقات كحالة مستعجلة.", "الموظف المختص راح يراجعه.",
                  "شكواك تحولت للقسم المختص.", "فريقنا راح يتابع الموضوع.", "سيتم التواصل معك.",
                  "الطلب انرسل إلى قسم البطاقات.", "القسم المختص رح يراجع الحالة."):
            self.assertTrue(pipeline.unsupported_actions_in_draft(d), d)

    def test_honest_wording_ok(self):
        for d in ("وصلنا طلبك بخصوص البطاقة المفقودة. الحالة تحتاج متابعة عاجلة من الموظف المختص.",
                  "وصلتنا شكوى التحويل، والحالة تحتاج متابعة من الموظف المختص. لا تعيد نفس التحويل."):
            self.assertEqual(pipeline.unsupported_actions_in_draft(d), [], d)

    def test_lost_card_claim_goes_to_review(self):
        msg = "انسرقت محفظتي وبيها البطاقة، وقفوها رجاءً"
        ans = {**ANS, "category": "card", "security_risk": True, "reason": "يگول البطاقة انسرقت ويطلب إيقافها."}
        out, _ = run_analyze(msg, ans, "تم إيقاف البطاقة فوراً حفاظاً على أمانك.")
        self.assertIn("draft_unsupported_action", out["review_reasons"])
        self.assertEqual(out["priority"], "high")


class DraftRequestTests(unittest.TestCase):
    def test_device_and_secrets_flagged(self):
        for d in ("يرجى تزويدنا برقم الجهاز ونسخة النظام.", "دزلنا رمز التحقق حتى نتأكد.",
                  "نحتاج كلمة المرور مالتك.", "اكتبلنا رقم البطاقة الكامل.",
                  "إذا عندك تفاصيل مثل نسخة النظام أو رقم الجهاز، يرجى مشاركتها."):
            self.assertTrue(pipeline.sensitive_requests(d), d)

    def test_safety_reminder_not_flagged(self):
        for d in ("ما راح نطلب منك الرمز السري أو رمز التحقق أبداً.", "لا تشارك رمز التحقق ويا أي أحد.",
                  "موظفينا ما يطلبون كلمة المرور."):
            self.assertEqual(pipeline.sensitive_requests(d), [], d)

    def test_card_digits_for_undelivered_card(self):
        msg = "صارلي أسبوعين طالب بطاقة وما وصلتني لحد الآن"
        self.assertTrue(pipeline.irrelevant_requests("يرجى تزويدنا بآخر 4 أرقام من البطاقة.", msg))
        self.assertEqual(pipeline.irrelevant_requests("إذا عندك رقم الطلب، ممكن تزودنا بيه؟", msg), [])

    def test_card_digits_ok_for_existing_card(self):
        msg = "الصراف سحب بطاقتي اليوم بالمول"
        self.assertEqual(pipeline.irrelevant_requests("ممكن تزودنا بآخر 4 أرقام من البطاقة؟", msg), [])

    def test_app_problem_draft_asking_device_goes_to_review(self):
        msg = "التطبيق يعلگ بشاشة البداية وما يفتح"
        ans = {**ANS, "category": "other", "reason": "التطبيق ما يفتح."}
        out, _ = run_analyze(msg, ans, "شكراً لرسالتك. يرجى تزويدنا برقم الجهاز حتى نراجع المشكلة.")
        self.assertIn("draft_sensitive_request", out["review_reasons"])


class MultipleIssuesTests(unittest.TestCase):
    MSG = "سويت حوالة قبل يومين وما وصلت، وفوگاها ما أگدر أدخل للتطبيق لأن الرمز ما يجيني"
    ANS2 = {**ANS, "secondary_category": "account_access", "flags": ["multiple_issues"], "date": "2026-10-31",
            "reason": "حوالة ما وصلت ومشكلة بالدخول للتطبيق."}

    def test_draft_prompt_gets_both_issues(self):
        _, seen = run_analyze(self.MSG, self.ANS2, "وصلت شكوى التحويل ومشكلة الدخول، والحالة تحتاج مراجعة من الموظف المختص.")
        system, user = seen[1]
        self.assertIn("## account_access", system)  # قسم الدليل للمشكلة الثانية
        self.assertIn("## transfer_failed", system)
        self.assertIn("مشكلة ثانية: الدخول للحساب", user)

    def test_draft_missing_second_issue_flagged(self):
        out, _ = run_analyze(self.MSG, self.ANS2, "وصلت شكوى التحويل. ممكن تزودنا برقم العملية والمبلغ؟")
        self.assertIn("draft_missing_issue", out["review_reasons"])
        self.assertEqual(out["secondary_category"], "account_access")

    def test_draft_with_both_issues_ok(self):
        out, _ = run_analyze(self.MSG, self.ANS2,
                             "وصلت شكوى التحويل ومشكلة الدخول للحساب، والثنتين تحتاج مراجعة من الموظف المختص. ممكن رقم العملية؟")
        self.assertNotIn("draft_missing_issue", out["review_reasons"])
        self.assertIn("multiple_issues", out["review_reasons"])


class DraftPromptTests(unittest.TestCase):
    def test_system_facts_and_rules_in_prompt(self):
        system, user = prompts.draft_prompts("شلون أجدد الكرت؟", "card", {"transaction_id": None, "amount": None,
                                                                         "date": None}, [])
        self.assertIn("ما انعمل أي إجراء", system)
        self.assertIn("ما انوقفت بطاقة", system)
        self.assertIn("الدليل ما يجاوبه", system)
        self.assertIn("رقم الجهاز", system)
        self.assertIn("ما يرسل الطلب لأي قسم أو موظف", system)
        self.assertNotIn("القسم المختص:", user)  # اسم القسم ما يوصل للمسودة حتى ما تدّعي إحالة
        for p in ("تم إيقاف", "رفعنا طلب"):
            self.assertIn(p, system)

    def test_guide_rules(self):
        guide = prompts.load_guide()
        self.assertIn("تم إيقاف", prompts.unsupported_actions(guide))
        _, card = prompts.guide_section("card", guide)
        self.assertIn("رقم الطلب", card)
        self.assertIn("متابعة عاجلة من الموظف المختص", card)
        self.assertNotIn("وصل لقسم البطاقات", guide)
        self.assertIn("تجديد", card)
        self.assertNotIn("يوقف البطاقة من التطبيق", card)  # ما نفترض ميزة بالتطبيق
        general, _ = prompts.guide_section("other", guide)
        self.assertIn("ما ندّعي", general)

    def test_long_draft_flagged(self):
        self.assertIn("draft_too_long",
                      pipeline.draft_review_reasons("كلام " * 200, "رسالة", None, "other"))


class ApprovalTests(unittest.TestCase):
    """الاعتماد يرفض المسودة (الأصلية أو المعدّلة أو المعاد كتابتها) إذا بيها ادعاء إجراء أو طلب حساس."""

    def setUp(self):
        import tempfile
        from masar import db, ingest
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init(self.path)
        rows, _ = ingest.parse_csv("complaint_id,received_at,message\nP1,2026-11-02 10:00,ضاعت بطاقتي اليوم\n".encode())
        with db.connect(self.path) as conn:
            db.create_batch(conn, "t.csv", rows, [])
        self.cid = rows[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self, text):
        with self.db.connect(self.path) as conn:
            self.db.save_review(conn, self.cid, "approved", text)

    def decision(self):
        with self.db.connect(self.path) as conn:
            r = conn.execute("SELECT decision, final_draft FROM reviews WHERE complaint_id = ?", (self.cid,)).fetchone()
        return r and tuple(r)

    def test_blocked_drafts_not_approved(self):
        for text in ("تم إيقاف بطاقتك.", "وصل طلبك لقسم البطاقات والموظف راح يراجعه.",
                     "يرجى تزويدنا برمز التحقق.", "دزلنا رقم البطاقة الكامل.", "نضمن رجوع المبلغ.", "  "):
            with self.assertRaises(self.db.DraftBlocked, msg=text):
                self.approve(text)
        self.assertIsNone(self.decision())

    def test_edited_text_is_what_gets_checked(self):
        good = "وصلنا طلبك بخصوص البطاقة المفقودة. الحالة تحتاج متابعة عاجلة من الموظف المختص."
        self.approve(good)
        self.assertEqual(self.decision(), ("approved", good))
        with self.assertRaises(self.db.DraftBlocked):  # تعديل بعد الاعتماد بيه ادعاء: ما ينحفظ
            self.approve(good + " تم إيقاف البطاقة.")
        self.assertEqual(self.decision(), ("approved", good))

    def test_followup_allowed_with_bad_draft(self):
        with self.db.connect(self.path) as conn:
            self.db.save_review(conn, self.cid, "needs_followup", "تم إيقاف بطاقتك.")
        self.assertEqual(self.decision()[0], "needs_followup")

    def test_regenerated_draft_is_checked(self):
        from masar import db
        with db.connect(self.path) as conn:
            conn.execute("UPDATE results SET status='done', category='card' WHERE complaint_id = ?", (self.cid,))
        with mock.patch.object(llm, "call", lambda *a, **k: llm.LLMResult(None, "تم حظر البطاقة وراح نتابع.", 1, 1, 1)):
            draft, found = pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertTrue(any("ادعاء إجراء" in f for f in found))
        with mock.patch.object(llm, "call", lambda *a, **k: llm.LLMResult(
                None, "وصلنا طلبك بخصوص البطاقة المفقودة. الحالة تحتاج متابعة عاجلة من الموظف المختص.", 1, 1, 1)):
            _, found = pipeline.regenerate_draft(self.cid, db_path=self.path)
        self.assertEqual(found, [])


class ReportTests(unittest.TestCase):
    LABELS = {
        "A": {"category": "transfer_failed", "secondary_category": "", "priority": "medium",
              "department": "العمليات والتحويلات", "transaction_id": "", "amount": "", "date": "",
              "security_risk": "0", "expect_review": "0", "hard_type": "", "family_id": "F1"},
        "B": {"category": "card", "secondary_category": "", "priority": "medium", "department": "البطاقات",
              "transaction_id": "", "amount": "", "date": "", "security_risk": "0", "expect_review": "1",
              "hard_type": "unclear_message", "family_id": "F2"},
    }

    def row(self, sid, cat, dept, raw, reasons, date=None):
        return {"source_id": sid, "status": "done", "category": cat, "priority": "medium", "department": dept,
                "transaction_id": None, "amount": None, "date": date, "confidence": 0.9, "review_reasons": reasons,
                "message": "x", "reason": "", "latency_ms": 1, "input_tokens": 1, "output_tokens": 1,
                "model": "m", "raw_output": raw, "error": None}

    def test_raw_vs_guarded_and_absent_classes(self):
        rows = [
            # الموديل طلّع تاريخ مو مسنود، والحماية مسحته
            self.row("A", "transfer_failed", "العمليات والتحويلات",
                     {**ANS, "date": "2026-11-02"}, ["unsupported_date"]),
            # الموديل واثق وما أشّر، والحماية بس هي اللي حوّلت للمراجعة
            self.row("B", "card", "البطاقات", {**ANS, "category": "card"}, ["draft_irrelevant_request"]),
        ]
        rep = evaluate.score(rows, self.LABELS, 0.75)
        rg = rep["raw_vs_guarded"]
        self.assertEqual(rg["field_exact"]["raw"]["date"], 0.5)
        self.assertEqual(rg["field_exact"]["guarded"]["date"], 1.0)
        self.assertEqual(rg["fields_changed_by_guard"]["date"], 1)
        self.assertEqual(rg["review_rate"], {"raw": 0.0, "guarded": 1.0})
        self.assertEqual(rg["hard_cases_sent_to_review"], {"raw": "0/1", "guarded": "1/1"})
        self.assertEqual(rg["guard_review_reasons"], {"unsupported_date": 1, "draft_irrelevant_request": 1})

        self.assertEqual(set(rep["classes_absent_from_sample"]),
                         {"wrong_recipient", "account_access", "agent_dispute", "other"})
        self.assertEqual(rep["category_macro_f1_present_classes"], 1.0)
        self.assertAlmostEqual(rep["category_macro_f1_all_classes"], 2 / 6)
        self.assertIsNone(rep["class_coverage"]["other"]["f1"])
        self.assertEqual(rep["class_coverage"]["card"], {"gold": 1, "predicted": 1, "f1": 1.0})
        self.assertTrue(rep["class_coverage_note"])

    def test_old_rows_without_raw(self):
        rows = [self.row("A", "transfer_failed", "العمليات والتحويلات", None, [])]
        rg = evaluate.score(rows, self.LABELS, 0.75)["raw_vs_guarded"]
        self.assertEqual(rg["rows_without_raw"], 1)
        self.assertNotIn("field_exact", rg)


class StorageTests(unittest.TestCase):
    def test_raw_and_secondary_saved(self):
        import tempfile
        from masar import db, ingest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            db.init(path)
            rows, _ = ingest.parse_csv(f"complaint_id,received_at,message\nQ1,2026-11-02 10:00,{MultipleIssuesTests.MSG}\n".encode())
            with db.connect(path) as conn:
                batch, _ = db.create_batch(conn, "t.csv", rows, [])
                ids = db.batch_ids_to_process(conn, batch)

            def fake(system, user, schema=None, **kw):
                if schema:
                    return llm.LLMResult(dict(MultipleIssuesTests.ANS2), "", 1, 1, 1)
                return llm.LLMResult(None, "وصلت شكوى التحويل ومشكلة الدخول، والحالة تحتاج مراجعة من الموظف المختص.", 1, 1, 1)

            with mock.patch.object(llm, "call", fake):
                pipeline.process(ids, db_path=path, workers=1)
            with db.connect(path) as conn:
                r = db.batch_rows(conn, batch)[0]
        self.assertEqual(r["secondary_category"], "account_access")
        self.assertEqual(r["raw_output"]["date"], "2026-10-31")
        self.assertEqual(r["prompt_version"], config.PROMPT_VERSION)


if __name__ == "__main__":
    unittest.main()
