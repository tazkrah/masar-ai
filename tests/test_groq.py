"""شكل طلب Groq ومعالجة حدود الاستخدام، بدون اتصال (العميل مستبدل ببديل وهمي)."""
import types
import unittest
from unittest import mock

import httpx2 as httpx
import openai

from masar import config, llm, prompts


def fake_response(content='{"a": 1}', finish="stop", headers=None):
    msg = types.SimpleNamespace(content=content)
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason=finish)],
                                 usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    return types.SimpleNamespace(headers=headers or {"x-ratelimit-remaining-tokens": "7000"}, parse=lambda: resp)


def rate_limit_error(retry_after):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    res = httpx.Response(429, request=req, headers={"retry-after": str(retry_after)})
    return openai.RateLimitError("rate limited", response=res, body=None)


class GroqTests(unittest.TestCase):
    def setUp(self):
        self.create = mock.MagicMock(return_value=fake_response())
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            with_raw_response=types.SimpleNamespace(create=self.create))))
        p = mock.patch.object(llm, "_client", return_value=client)
        p.start()
        self.addCleanup(p.stop)

    def test_request_uses_strict_schema_and_reasoning(self):
        res = llm.call("s", "u", schema=prompts.CLASSIFY_SCHEMA, provider="groq", model="openai/gpt-oss-120b")
        kw = self.create.call_args.kwargs
        self.assertEqual(kw["response_format"]["type"], "json_schema")
        self.assertTrue(kw["response_format"]["json_schema"]["strict"])
        self.assertEqual(kw["reasoning_effort"], config.REASONING_EFFORT)
        self.assertEqual(kw["extra_body"], {"include_reasoning": False})
        self.assertNotIn("include_reasoning", kw)  # مو باراميتر معروف بمكتبة openai
        self.assertEqual(res.data, {"a": 1})
        self.assertEqual(llm.last_rate_limits.get("x-ratelimit-remaining-tokens"), "7000")

    def test_short_rate_limit_waits_then_succeeds(self):
        self.create.side_effect = [rate_limit_error(3), fake_response()]
        sleeps = []
        res = llm.call("s", "u", schema={"type": "object"}, provider="groq", model="openai/gpt-oss-120b", sleep=sleeps.append)
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(res.data, {"a": 1})

    def test_long_rate_limit_fails_fast(self):
        self.create.side_effect = rate_limit_error(3600)
        sleeps = []
        with self.assertRaises(llm.LLMError) as cm:
            llm.call("s", "u", provider="groq", model="openai/gpt-oss-120b", sleep=sleeps.append)
        self.assertEqual(sleeps, [])
        self.assertTrue(cm.exception.retryable)
        self.assertIn("حد الاستخدام", str(cm.exception))

    def test_truncated_answer_is_retried(self):
        self.create.side_effect = [fake_response(finish="length"), fake_response()]
        res = llm.call("s", "u", schema={"type": "object"}, provider="groq", model="openai/gpt-oss-120b", sleep=lambda s: None)
        self.assertEqual(res.data, {"a": 1})


if __name__ == "__main__":
    unittest.main()
