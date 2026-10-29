"""اختيار عينة التطوير وتشغيل معرّفات محددة. بدون اتصال بالموديل."""
import csv
import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sel = _load("select_dev_sample")
evaluate = _load("evaluate")
LABELS = list(csv.DictReader((ROOT / "data" / "complaints" / "labels" / "dev_labels.csv").open(encoding="utf-8")))


class SelectTests(unittest.TestCase):
    def test_quotas_exclusion_and_determinism(self):
        a, b = sel.select(LABELS), sel.select(LABELS)
        self.assertEqual(a, b)  # نفس البذرة = نفس العينة
        self.assertEqual(len(a), 20)
        self.assertEqual(len({p["id"] for p in a}), 20)
        self.assertFalse({p["id"] for p in a} & set(sel.EXCLUDE))
        self.assertEqual(Counter((p["category"], p["kind"]) for p in a), Counter(sel.QUOTAS))
        self.assertEqual({p["category"] for p in a}, {"transfer_failed", "wrong_recipient", "account_access",
                                                      "agent_dispute", "card", "other"})
        by_id = {l["complaint_id"]: l for l in LABELS}
        for p in a:  # النوع مطابق للإجابة الصحيحة
            self.assertEqual(by_id[p["id"]]["hard_type"] or "clear", p["kind"])
        self.assertEqual(len({p["family"] for p in a}), 20)  # عائلة وحدة لكل شكوى
        prev = {by_id[i]["family_id"] for i in sel.EXCLUDE}
        seen = [p for p in a if p["family"] in prev]
        self.assertTrue(all(p["family_seen_before"] for p in seen))
        # ما نرجع لعائلة سابقة إلا إذا هالطبقة ما بيها غيرها
        for p in seen:
            others = {l["family_id"] for l in LABELS if l["category"] == p["category"]
                      and (l["hard_type"] or "clear") == p["kind"]} - prev
            self.assertEqual(others - {q["family"] for q in a}, set(), p)

    def test_select_ids_keeps_order_and_rejects_unknown(self):
        rows = [{"source_id": f"DEV-{i}"} for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.json"
            f.write_text(json.dumps({"split": "dev", "ids": ["DEV-3", "DEV-1"]}))
            self.assertEqual([r["source_id"] for r in evaluate.select_ids(rows, f, "dev")], ["DEV-3", "DEV-1"])
            f.write_text(json.dumps({"split": "dev", "ids": ["DEV-9"]}))
            with self.assertRaises(SystemExit):
                evaluate.select_ids(rows, f, "dev")
            f.write_text(json.dumps({"split": "calib", "ids": ["DEV-1"]}))
            with self.assertRaises(SystemExit):
                evaluate.select_ids(rows, f, "dev")


if __name__ == "__main__":
    unittest.main()
