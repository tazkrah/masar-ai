"""نسخ البيانات 5 و 6 وقواعد v5: الغموض، الشكوى المالية، العملية غير المصرح بها، الإيداع، التاريخ، ترتيب المشكلتين.

بدون اتصال بالموديل. رسائل الاختبار مكتوبة يدوياً ومو من بيانات التطوير أو الضبط أو الاختبار.
مجموعتي الضبط والاختبار ما تنقرا هنا: بس بصمة ملفاتها تنقارن بالـ manifest.
"""
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from masar import config, llm, pipeline, prompts

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "complaints"
sys.path.insert(0, str(ROOT / "scripts"))
import label_corrections as lc  # noqa: E402

_spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

V4_LABELS_SHA = "ca2f6ce0f703c88724b4b5f35c33ca6bf9e13b04e60920953ee9a9f3f9f47b84"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest():
    return json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def dev():
    with (DATA / "labels" / "dev_labels.csv").open(encoding="utf-8") as fh:
        labels = list(csv.DictReader(fh))
    with (DATA / "dev.csv").open(encoding="utf-8") as fh:
        msgs = {r["complaint_id"]: r for r in csv.DictReader(fh)}
    return labels, msgs


ANS = {
    "reason": "x", "category": "other", "secondary_category": None, "transaction_id": None, "amount_iqd": None,
    "date": None, "security_risk": False, "money_issue": False, "unauthorized_operation": False, "flags": [],
    "confidence": 0.9,
}


def run(message, received_at="2026-11-03 10:00", **answer):
    def fake(system, user, schema=None, **kw):
        if schema:
            return llm.LLMResult({**ANS, **answer}, "", 1, 1, 1)
        return llm.LLMResult(None, "وصلتنا شكواك. الحالة تحتاج متابعة من الموظف المختص.", 1, 1, 1)

    with mock.patch.object(llm, "call", fake):
        return pipeline.analyze({"message": message, "received_at": received_at})


class DataVersionTests(unittest.TestCase):
    def test_messages_and_frozen_splits_unchanged(self):
        m = manifest()
        self.assertEqual(m["data_version"], lc.DATA_VERSION)
        self.assertEqual(sha(DATA / "dev.csv"), m["splits"]["dev"]["sha256"])
        self.assertEqual(sha(DATA / "labels" / "dev_labels.csv"), m["splits"]["dev"]["labels_sha256"])
        for sp in ("calib", "test"):  # البصمة بس، بدون قراءة المحتوى
            self.assertEqual(sha(DATA / f"{sp}.csv"), m["splits"][sp]["sha256"], sp)
            self.assertEqual(sha(DATA / "labels" / f"{sp}_labels.csv"), m["splits"][sp]["labels_sha256"], sp)

    def test_every_correction_logged_with_old_new_reason(self):
        revs = {r["data_version"]: r for r in manifest()["revisions"]}
        for v, corrs in lc.VERSIONS.items():
            rev = revs[v]
            self.assertEqual(rev["messages_changed"], [])
            logged = {c["id"]: c for c in rev["label_changes"]}
            self.assertEqual(set(logged), set(corrs), v)
            for cid, c in logged.items():
                self.assertTrue(c["from"] and c["to"] and c["reason"], cid)
                self.assertEqual(set(c["from"]), set(c["to"]), cid)

    def test_reverting_log_reproduces_v4_labels(self):
        """سجل التصحيحات كامل: نرجّع القيم القديمة ونشيل الأعمدة الجديدة، يطلع ملف نسخة 4 بنفس البصمة."""
        revs = [r for r in manifest()["revisions"] if r["data_version"] > 4]
        self.assertEqual(revs[0]["labels_sha256_before"], V4_LABELS_SHA)
        labels, _ = dev()
        by_id = {r["complaint_id"]: r for r in labels}
        dropped = set()
        for rev in reversed(revs):
            for c in rev["label_changes"]:
                by_id[c["id"]].update(c["from"])
            dropped |= set(rev["new_columns"])
        fields = [f for f in labels[0] if f not in dropped]
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(labels)
        self.assertEqual(hashlib.sha256(buf.getvalue().encode("utf-8")).hexdigest(), V4_LABELS_SHA)

    def test_priority_and_department_follow_rules(self):
        labels, _ = dev()
        corrs, unauthorized = lc.corrections(), lc.unauthorized_ids()
        for l in labels:
            corr = corrs.get(l["complaint_id"], {})
            amount = int(l["amount"]) if l["amount"] else None
            self.assertEqual(l["priority"], config.priority_for(l["category"], amount, l["security_risk"] == "1",
                                                                corr.get("money_issue", False)), l["complaint_id"])
            self.assertEqual(l["department"], config.department_for(l["category"], l["complaint_id"] in unauthorized),
                             l["complaint_id"])
        for cid in unauthorized:  # التصريح بعملية مو مأذونة: خطر وأولوية عالية وأمن الحسابات مهما كانت الفئة
            l = next(x for x in labels if x["complaint_id"] == cid)
            self.assertEqual((l["security_risk"], l["priority"], l["department"]),
                             ("1", "high", config.ROUTING["account_access"]), cid)

    def test_unclear_labels_reviewed_with_note(self):
        labels, _ = dev()
        for l in labels:
            if l["hard_type"] in ("unclear_message", "ambiguous_date") and l["complaint_id"] in lc.corrections():
                self.assertEqual(l["expect_review"], "1", l["complaint_id"])
                self.assertTrue(l["review_note"], l["complaint_id"])

    def test_agent_dispute_labels_show_an_agent(self):
        """فحص رجوع: كل «خلاف مع الوكيل» بالتطوير رسالته تبين وكيل أو محل (مو من السيناريو)."""
        labels, msgs = dev()
        for l in labels:
            if l["category"] == "agent_dispute":
                self.assertRegex(msgs[l["complaint_id"]]["message"], r"وكيل|محل|agent", l["complaint_id"])

    def test_accepted_swaps_documented_and_narrow(self):
        labels, _ = dev()
        swaps = {l["complaint_id"]: l for l in labels if l["accepted_swap"] == "1"}
        self.assertEqual(set(swaps), {"DEV-G08-2", "DEV-G08-4"})
        for l in swaps.values():
            self.assertEqual({l["category"], l["secondary_category"]}, {"agent_dispute", "transfer_failed"})

    def test_day_without_month_labels_match_code_rule(self):
        """كل رسالة بيها يوم بدون شهر بعد يوم الاستلام: التاريخ فارغ والمراجعة مطلوبة. والعكس."""
        labels, msgs = dev()
        for l in labels:
            m = msgs[l["complaint_id"]]
            if pipeline.ambiguous_days(m["message"], m["received_at"]):
                self.assertEqual(l["date"], "", l["complaint_id"])
                self.assertEqual(l["expect_review"], "1", l["complaint_id"])
            elif l["hard_type"] == "ambiguous_date":
                self.fail(l["complaint_id"])


class PriorityRoutingTests(unittest.TestCase):
    def test_other_priority(self):
        self.assertEqual(config.priority_for("other", None, False), "low")
        self.assertEqual(config.priority_for("other", None, False, money_issue=True), "medium")
        self.assertEqual(config.priority_for("other", 1_200_000, False, money_issue=True), "high")
        self.assertEqual(config.priority_for("other", None, True, money_issue=True), "high")

    def test_unauthorized_routing_any_category(self):
        for cat in config.CATEGORIES:
            self.assertEqual(config.department_for(cat, unauthorized=True), config.ROUTING["account_access"], cat)
            self.assertEqual(config.department_for(cat), config.ROUTING[cat], cat)


class AnalyzeV5Tests(unittest.TestCase):
    def test_financial_ambiguity_medium_and_reviewed(self):
        out = run("سحبت فلوس امس وما طلعتلي ولا فلس، شنو صاير؟", money_issue=True, flags=["unclear_message"],
                  reason="سحب وما طلع الكاش، بس ما مذكور إذا من صراف لو وكيل.", confidence=0.5)
        self.assertEqual((out["category"], out["priority"], out["department"]),
                         ("other", "medium", config.ROUTING["other"]))
        self.assertIn("money_issue_unclassified", out["review_reasons"])
        self.assertEqual(out["security_risk"], 0)

    def test_financial_ambiguity_reviewed_even_if_model_confident(self):
        out = run("انگص من رصيدي ٣٠ الف مرتين البارحة", money_issue=True, amount_iqd=30000)
        self.assertEqual(out["priority"], "medium")
        self.assertEqual(out["needs_review"], 1)

    def test_big_amount_ambiguity_high(self):
        out = run("حوالة مليون ونص رقمها 55120987 لو سمحتوا شوفوها", money_issue=True, amount_iqd=1_500_000,
                  transaction_id="55120987")
        self.assertEqual(out["priority"], "high")

    def test_unauthorized_is_security_high_account_security(self):
        out = run("انسحب من رصيدي 90 الف الليلة وآني ما سويت أي عملية", money_issue=True,
                  unauthorized_operation=True, security_risk=False, amount_iqd=90000, flags=["unclear_message"])
        self.assertEqual(out["security_risk"], 1)
        self.assertEqual(out["priority"], "high")
        self.assertEqual(out["department"], config.ROUTING["account_access"])

    def test_not_understanding_a_debit_is_not_a_breach(self):
        out = run("شفت خصم 15 الف وما فهمت منين اجا", money_issue=True, amount_iqd=15000)
        self.assertEqual((out["security_risk"], out["priority"]), (0, "medium"))
        self.assertEqual(out["department"], config.ROUTING["other"])

    def test_general_question_stays_low(self):
        out = run("شنو ساعات الدوام مالتكم؟")
        self.assertEqual((out["priority"], out["review_reasons"]), ("low", []))

    def test_mixed_deposit_and_failed_transfer(self):
        out = run("حطيت ٤٠ الف إيداع وما نزلت، وحاولت احول لاختي وفشل التحويل", category="transfer_failed",
                  secondary_category="other", money_issue=True, amount_iqd=40000, flags=["multiple_issues"])
        self.assertEqual((out["category"], out["secondary_category"]), ("transfer_failed", "other"))
        self.assertEqual(out["department"], config.ROUTING["transfer_failed"])
        self.assertIn("multiple_issues", out["review_reasons"])

    def test_day_without_month_after_receipt_dropped(self):
        msg = "يوم 29 الشهر حولت 40 الف لابن خالتي وما وصلت"
        out = run(msg, category="transfer_failed", date="2026-10-29", amount_iqd=40000)
        self.assertIsNone(out["date"])
        self.assertIn("ambiguous_date", out["review_reasons"])
        out = run(msg, category="transfer_failed", date=None, amount_iqd=40000)  # حتى لو الموديل ما طلّع تاريخ
        self.assertIn("ambiguous_date", out["review_reasons"])

    def test_day_without_month_before_receipt_kept(self):
        out = run("يوم 2 الشهر حولت 40 الف وما وصلت", category="transfer_failed", date="2026-11-02")
        self.assertEqual(out["date"], "2026-11-02")
        self.assertNotIn("ambiguous_date", out["review_reasons"])

    def test_explicit_month_not_ambiguous(self):
        for msg, d in (("يوم 29/10 حولت 40 الف وما وصلت", "2026-10-29"),
                       ("يوم 29 الشهر الماضي حولت 40 الف وما وصلت", "2026-10-29"),
                       ("يوم 29 تشرين الأول حولت 40 الف وما وصلت", "2026-10-29")):
            out = run(msg, category="transfer_failed", date=d)
            self.assertEqual(out["date"], d, msg)
            self.assertNotIn("ambiguous_date", out["review_reasons"], msg)

    def test_no_received_date_no_guess(self):
        self.assertEqual(pipeline.ambiguous_days("يوم 29 الشهر", None), set())


class InstructionsTests(unittest.TestCase):
    def test_v5_schema_and_rules(self):
        self.assertEqual(config.PROMPT_VERSION, "v5")
        for k in ("money_issue", "unauthorized_operation"):
            self.assertIn(k, prompts.CLASSIFY_SCHEMA["required"])
        for _, _, out in prompts.FEW_SHOT:
            self.assertEqual(set(out), set(prompts.CLASSIFY_SCHEMA["required"]))
        text = prompts.CLASSIFY_SYSTEM
        self.assertIn("لازم الرسالة تبين إن العملية صارت عند وكيل أو محل", text)
        self.assertIn("ولا تفترض الشهر السابق", text)
        self.assertIn("unauthorized_operation", text)


class SwapScoringTests(unittest.TestCase):
    LABELS = {
        "S": {"category": "agent_dispute", "secondary_category": "transfer_failed", "priority": "medium",
              "department": "شبكة الوكلاء", "transaction_id": "", "amount": "", "date": "", "security_risk": "0",
              "expect_review": "1", "hard_type": "multiple_issues", "family_id": "F", "accepted_swap": "1"},
        "N": {"category": "transfer_failed", "secondary_category": "account_access", "priority": "medium",
              "department": "العمليات والتحويلات", "transaction_id": "", "amount": "", "date": "",
              "security_risk": "0", "expect_review": "1", "hard_type": "multiple_issues", "family_id": "G",
              "accepted_swap": "0"},
    }

    def row(self, sid, cat):
        return {"source_id": sid, "status": "done", "category": cat, "priority": "medium", "department": "x",
                "transaction_id": None, "amount": None, "date": None, "confidence": 0.9,
                "review_reasons": ["multiple_issues"], "message": "x", "reason": "", "latency_ms": 1,
                "input_tokens": 1, "output_tokens": 1, "model": "m", "raw_output": None, "error": None}

    def test_swap_accepted_only_where_documented(self):
        rep = evaluate.score([self.row("S", "transfer_failed"), self.row("N", "account_access")], self.LABELS, 0.75)
        self.assertEqual(rep["category_accuracy"], 0.5)  # S مقبول، N لا
        self.assertEqual(rep["category_accuracy_strict"], 0.0)
        self.assertEqual(rep["confusion"]["transfer_failed"], {"transfer_failed": 1, "account_access": 1})

    def test_old_labels_without_column_strict(self):
        labels = {"S": {k: v for k, v in self.LABELS["S"].items() if k != "accepted_swap"}}
        rep = evaluate.score([self.row("S", "transfer_failed")], labels, 0.75)
        self.assertEqual(rep["category_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
