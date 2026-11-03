"""الواجهة تعرض الأولوية والقسم من قواعد التوجيه (مو من الفئة وحدها) والمبلغ ويا مشكلته، حتى بعد تصحيح الفئة.

يشغّل app.py بـ streamlit AppTest على قاعدة مؤقتة، والموديل بديل وهمي. رسائل مكتوبة يدوياً.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masar import config, db, ingest, llm, pipeline

ROOT = Path(__file__).resolve().parent.parent
BASE = {"reason": "x", "secondary_category": None, "transaction_id": None, "amount_issue": None, "date": None,
        "security_risk": False, "money_issue": True, "flags": [], "confidence": 0.9}
ANSWERS = {
    "U1": {**BASE, "category": "transfer_failed", "secondary_category": "other", "amount_iqd": 65000,
           "amount_issue": "secondary", "unauthorized_operation": False, "flags": ["multiple_issues"]},
    "U2": {**BASE, "category": "other", "amount_iqd": 80000, "unauthorized_operation": True},
}
MESSAGES = {"U1": "عندي إيداع ٦٥ الف ما نزل بحسابي، وكمان حولت لصاحبي وطلعلي التحويل فشل",
            "U2": "انسحب من رصيدي 80 الف وآني ما سويت شي"}


def facts(at):
    html = " ".join(m.value for m in at.markdown)
    return {k: re.sub("<[^>]+>", "", v) for k, v in re.findall(r'<span class="k">([^<]+)</span>(.*?)</div>', html)}


class AppDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            raise unittest.SkipTest("streamlit مو منصّب")
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "app.db"
        db.init(path)
        csv_text = "complaint_id,received_at,message\n" + "".join(
            f"{k},2026-11-03 10:00,{v}\n" for k, v in MESSAGES.items())
        rows, _ = ingest.parse_csv(csv_text.encode())
        with db.connect(path) as conn:
            batch, _ = db.create_batch(conn, "t.csv", rows, [])
            ids = db.batch_ids_to_process(conn, batch)

        def fake(system, user, schema=None, **kw):
            key = "U2" if "انسحب" in user else "U1"
            if schema:
                return llm.LLMResult(dict(ANSWERS[key]), "", 1, 1, 1)
            return llm.LLMResult(None, "وصلتنا شكواك. الحالة تحتاج متابعة من الموظف المختص.", 1, 1, 1)

        with mock.patch.object(llm, "call", fake):
            pipeline.process(ids, db_path=path, workers=1)
        with db.connect(path) as conn:
            cls.cids = {r["source_id"]: r["complaint_id"] for r in db.batch_rows(conn, batch)}
        cls.env = mock.patch.dict(os.environ, {"MASAR_DB": str(path)})
        cls.env.start()
        cls.db_patch = mock.patch.object(config, "DB_PATH", path)
        cls.db_patch.start()
        cls.AppTest = AppTest

    @classmethod
    def tearDownClass(cls):
        cls.db_patch.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def open(self, sid):
        at = self.AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
        at.session_state["sel"] = self.cids[sid]
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def test_unauthorized_other_shows_account_security(self):
        f = facts(self.open("U2"))
        self.assertEqual((f["الأولوية"], f["القسم"]), ("عالية", config.ROUTING["account_access"]))

    def test_unauthorized_kept_after_category_change(self):
        at = self.open("U2")
        at.selectbox(key=f"w_cat_{self.cids['U2']}").select("card").run()
        f = facts(at)
        self.assertEqual((f["الأولوية"], f["القسم"]), ("عالية", config.ROUTING["account_access"]))
        self.assertEqual(f["المبلغ"], "80,000 دينار عراقي")

    def test_amount_shown_with_its_issue(self):
        f = facts(self.open("U1"))
        self.assertEqual(f["القسم"], config.ROUTING["transfer_failed"])
        self.assertIn("يخص مشكلة ثانية بالرسالة", f["المبلغ"])

    def test_category_change_applies_rules(self):
        at = self.open("U1")
        at.selectbox(key=f"w_cat_{self.cids['U1']}").select("wrong_recipient").run()
        f = facts(at)
        self.assertEqual((f["الأولوية"], f["القسم"]), ("عالية", config.ROUTING["wrong_recipient"]))


if __name__ == "__main__":
    unittest.main()
