"""الديمو: يبني قاعدة مؤقتة من النتائج المحفوظة المصدّرة ويعرضها بدون API، والعرض يحط التحذيرات بمكانها.

يستخدم ملف runs/demo/ المصدّر من تجربة v4 على مجموعة التطوير. أي استدعاء للموديل يفشل الاختبار.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masar import config, db, llm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "runs" / "demo" / "dev-sample-2-v4.json"


def load_demo_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DemoExportTests(unittest.TestCase):
    def test_export_is_dev_v4_with_source(self):
        data = json.loads(DATA.read_text(encoding="utf-8"))
        src = data["source"]
        self.assertEqual((src["split"], src["prompt_version"], src["data_version_at_run"]), ("dev", ["v4"], 4))
        self.assertTrue(all(c["source_id"].startswith("DEV-") for c in data["complaints"]))
        self.assertEqual(len(data["complaints"]), 20)
        report = ROOT / src["run_report"]
        import hashlib
        self.assertEqual(hashlib.sha256(report.read_bytes()).hexdigest(), src["run_report_sha256"])

    def test_export_has_no_fields_added_after_the_run(self):
        res = json.loads(DATA.read_text(encoding="utf-8"))["results"]
        for k in ("money_issue", "unauthorized_operation", "amount_issue"):
            self.assertNotIn(k, res[0])


class DemoAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            raise unittest.SkipTest("streamlit مو منصّب")
        cls.AppTest = AppTest
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "demo.db"
        demo = load_demo_module()
        cls.label = demo.build(DATA, cls.path)
        with db.connect(cls.path) as conn:
            cls.ids = dict(conn.execute("SELECT source_id, id FROM complaints"))
        cls.patches = [mock.patch.dict(os.environ, {"MASAR_DB": str(cls.path), "GROQ_API_KEY": ""}),
                       mock.patch.object(config, "DB_PATH", cls.path),
                       mock.patch.object(config, "DEMO_LABEL", cls.label),
                       mock.patch.object(llm, "call", side_effect=AssertionError("الديمو استدعى الموديل"))]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def open(self, sid):
        at = self.AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
        at.session_state["sel"] = self.ids[sid]
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        html = " ".join(m.value for m in at.markdown)
        return at, html

    def test_label_and_saved_values_shown(self):
        self.assertEqual(self.label, "نتائج معالجة سابقة — v4")
        _, html = self.open("DEV-K03-2")
        self.assertIn('<span class="demo-tag">نتائج معالجة سابقة — v4</span>', html)
        facts = dict(re.findall(r'<span class="k">([^<]+)</span><span class="v[^"]*">(?:<bdi>)?([^<]+)', html))
        self.assertEqual((facts["رقم العملية"], facts["المبلغ"], facts["تاريخ المشكلة"]),
                         ("5642035702", "40,000 دينار عراقي", "2026-10-04"))
        self.assertNotIn("line flag", html)

    def test_reason_warning_is_small_next_to_reason(self):
        at, html = self.open("DEV-A04-3")  # سببها الوحيد: التبرير بيه تفصيل مو بالرسالة
        self.assertNotIn("انتبه قبل الاعتماد", html)
        self.assertIn("سبب التصنيف", html)
        self.assertEqual([p.proto.popover.label for p in at.get("popover")].count("راجع سبب التصنيف"), 1)

    def test_other_reasons_stay_on_top(self):
        _, html = self.open("DEV-G08-5")
        top = re.search(r'<div class="line flag"><b>انتبه قبل الاعتماد:</b>(.*?)</div>', html).group(1)
        self.assertIn(config.REVIEW_REASON_LABELS["multiple_issues"], top)
        self.assertNotIn(config.REVIEW_REASON_LABELS["reason_unsupported"], top)
        self.assertNotIn(config.REVIEW_REASON_LABELS["draft_missing_issue"], top)
        self.assertIn("انتبه بالمسودة:", html)

    def test_draft_blocker_next_to_draft_and_approval_refused(self):
        at, html = self.open("DEV-T10-1")
        self.assertIn('<div class="line flag draft"><b>يمنع الاعتماد:</b>', html)
        at.button(key=f"ok_{self.ids['DEV-T10-1']}").click().run()
        self.assertIn("ما تنعتمد قبل التصحيح", " ".join(m.value for m in at.markdown))
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM reviews").fetchone()[0], 0)

    def test_processing_and_rewrite_disabled(self):
        at, _ = self.open("DEV-K03-2")
        self.assertTrue(at.button(key=f"re_{self.ids['DEV-K03-2']}").disabled)


if __name__ == "__main__":
    unittest.main()
