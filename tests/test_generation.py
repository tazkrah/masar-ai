"""فحص منطق توليد البيانات بالموديل بدون اتصال."""
import random
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_llm_dataset as g  # noqa: E402
from scenarios import S, split  # noqa: E402


class GenerationTests(unittest.TestCase):
    def test_split_is_180_60_60_by_scenario(self):
        sp = split(random.Random(1))
        counts = Counter(sp.values())
        self.assertEqual({k: v * g.PER_SCENARIO for k, v in counts.items()}, {"dev": 180, "calib": 60, "test": 60})
        hard_test = {s["hard"] for s in S if s["hard"] and sp[s["id"]] == "test"}
        self.assertEqual(hard_test, {"multiple_issues", "unclear_message", "possible_injection", "similar_names"})

    def test_verify_accepts_exact_facts(self):
        given = {"amount": "٥٠ الف", "tx": "4466 8983 4", "date": "البارحة"}
        text = "حولت ٥٠ الف البارحة وانخصمت بس ما وصلت، رقم العملية 4466 8983 4"
        self.assertIsNone(g.verify(text, given, set()))

    def test_verify_rejects_missing_or_extra(self):
        given = {"amount": "50 الف"}
        self.assertIn("مو مكتوبة", g.verify("حولت خمسين الف وما وصلت", given, set()))
        self.assertIn("أرقام زايدة", g.verify("حولت 50 الف رقم العملية 778899 وما وصلت", given, set()))
        self.assertIn("تعبير وقت", g.verify("حولت 50 الف امس وما وصلت", given, set()))
        self.assertIn("تعبير وقت", g.verify("هسه حولت 50 الف وما وصلت", given, set()))

    def test_date_expression_allowed_only_when_given(self):
        given = {"date": "قبل يومين"}
        self.assertIsNone(g.verify("الحساب انقفل قبل يومين وما اگدر ادخل", given, set()))

    def test_similarity_rejects_swapped_numbers_only(self):
        a = ("حولت 50 الف لاخوي وانخصمت من رصيدي بس ما وصلت", {"amount": "50 الف"})
        same = "حولت ٧٥ الف لاخوي وانخصمت من رصيدي بس ما وصلت"
        diff = "ليش فلوسي طلعت من الحساب ومحد استلمها؟ ٧٥ الف راحت"
        self.assertTrue(g.too_similar(same, {"amount": "٧٥ الف"}, [a]))
        self.assertFalse(g.too_similar(diff, {"amount": "٧٥ الف"}, [a]))

    def test_generate_scenario_keeps_accepted_and_saves(self):
        from unittest import mock
        from masar import llm
        scen = S[0]
        variants = [{"given": {}, "style": "قصيرة"} for _ in range(3)]
        texts = ["رسالة مقبولة من قبل عن تحويل ما وصل للاخ", None, None]
        answers = {"items": [{"n": 1, "text": "التحويل علگ والفلوس طلعت من رصيدي ومحد استلم"},
                             {"n": 2, "text": "شنو صار بالتحويل؟ صديقي يگول ما شاف شي وآني انخصمت"}]}
        saves = []
        with mock.patch.object(llm, "call", return_value=llm.LLMResult(answers, "", 1, 1, 1)) as call, \
                open("/dev/null", "w") as log:
            g.generate_scenario(scen, variants, log, set(), texts, saves.append)
        self.assertIn("اكتب 2 رسائل", call.call_args.args[1])  # بس الناقصة تنطلب
        self.assertTrue(all(texts))
        self.assertEqual(texts[0], "رسالة مقبولة من قبل عن تحويل ما وصل للاخ")
        self.assertEqual(len(saves), 1)

    def test_plan_truth_matches_given(self):
        rng = random.Random(3)
        for s in S:
            v = g.plan_variant(rng, s)
            self.assertEqual("amount" in v["given"], v["truth"]["amount"] is not None)
            self.assertEqual("tx" in v["given"], v["truth"]["transaction_id"] is not None)
            self.assertEqual("date" in v["given"], v["truth"]["date"] is not None)


if __name__ == "__main__":
    unittest.main()
