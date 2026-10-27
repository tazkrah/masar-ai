"""استدعاء الموديل بدالة وحدة، حتى نگدر نبدّل المزوّد بدون ما نعيد بناء التطبيق.

المزوّد الافتراضي: Groq (الخطة المجانية) بموديل openai/gpt-oss-120b عبر واجهة OpenAI المتوافقة،
مع JSON منظّم بمخطط صارم (json_schema, strict).
بدائل: anthropic (Claude) و gemini، بتغيير MASAR_PROVIDER و MASAR_MODEL بملف .env.
"""
import json
import os
import random
import time
from dataclasses import dataclass

from . import config


class LLMError(Exception):
    """فشل نهائي بعد المحاولات. retryable يعني ممكن تنجح إعادة المحاولة لاحقاً."""

    def __init__(self, message, retryable):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResult:
    data: dict | None
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
OPENAI_COMPAT_BASE = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def key_status(provider=None):
    provider = provider or config.PROVIDER
    env = KEY_ENV.get(provider)
    return env, bool(env and os.getenv(env))


_clients = {}


def _client(provider):
    if provider in _clients:
        return _clients[provider]
    env, ok = key_status(provider)
    if not ok:
        raise LLMError(f"مفتاح {env} مو موجود. حطه بملف .env بمجلد المشروع.", retryable=False)
    if provider == "anthropic":
        import anthropic

        c = anthropic.Anthropic(api_key=os.environ[env], max_retries=0, timeout=60)
    elif provider in OPENAI_COMPAT_BASE:
        from openai import OpenAI

        c = OpenAI(base_url=OPENAI_COMPAT_BASE[provider], api_key=os.environ[env], max_retries=0, timeout=60)
    else:
        raise LLMError(f"مزوّد غير معروف: {provider}", retryable=False)
    _clients[provider] = c
    return c


def _anthropic_call(system, user, schema, max_tokens, model, temperature=0):
    import anthropic

    client = _client("anthropic")
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("المفتاح مرفوض (401). تأكد من ANTHROPIC_API_KEY.", retryable=False) from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("ما عندك صلاحية على هذا الموديل (403).", retryable=False) from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"الموديل {model} مو موجود (404).", retryable=False) from e
    except anthropic.RateLimitError as e:
        raise _Retry("وصلنا حد الاستخدام (429).", _retry_after(e)) from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"طلب مرفوض (400): {e.message}", retryable=False) from e
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 or e.status_code == 529:
            raise _Retry(f"خطأ من الخادم ({e.status_code}).", None) from e
        raise LLMError(f"خطأ ({e.status_code}): {e.message}", retryable=False) from e
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
        raise _Retry("مشكلة اتصال أو انتهى الوقت.", None) from e

    if resp.stop_reason == "refusal":
        raise LLMError("الموديل رفض يعالج الرسالة.", retryable=False)
    if resp.stop_reason == "max_tokens":
        raise _Retry("الجواب انقطع قبل ما يكمل.", None)
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


# آخر حدود استخدام رجعت من المزوّد (للعرض والتشخيص فقط)
last_rate_limits = {}
RATE_HEADERS = ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests",
                "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens")


def _openai_compat_call(provider, system, user, schema, max_tokens, model, temperature=0):
    import openai

    client = _client(provider)
    sys = system
    kwargs = {}
    if schema and provider == "groq":
        # JSON منظّم بمخطط صارم: الموديل مقيّد يطلع جواب يطابق المخطط
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "masar_output", "strict": True, "schema": schema}}
    elif schema:
        sys += "\n\nرجّع JSON فقط يطابق هذا المخطط:\n" + json.dumps(schema, ensure_ascii=False)
        kwargs["response_format"] = {"type": "json_object"}
    if provider == "groq" and "gpt-oss" in model:
        kwargs["reasoning_effort"] = config.REASONING_EFFORT
        kwargs["extra_body"] = {"include_reasoning": False}  # خاص بـ Groq: التفكير ما نحتاجه بالجواب
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            **kwargs,
        )
        _remember_limits(raw.headers)
        resp = raw.parse()
    except openai.RateLimitError as e:
        _remember_limits(getattr(e.response, "headers", {}))
        raise _Retry("وصلنا حد الاستخدام (429).", _retry_after(e)) from e
    except openai.AuthenticationError as e:
        raise LLMError(f"المفتاح مرفوض (401). تأكد من {KEY_ENV[provider]} بملف .env.", retryable=False) from e
    except openai.PermissionDeniedError as e:
        raise LLMError("ما عندك صلاحية على هذا الموديل (403).", retryable=False) from e
    except openai.NotFoundError as e:
        raise LLMError(f"الموديل {model} مو موجود عند المزوّد (404).", retryable=False) from e
    except openai.BadRequestError as e:
        msg = str(getattr(e, "message", e))
        if "json_validate_failed" in msg or "does not match the expected schema" in msg:
            raise _Retry("الموديل طلّع JSON ما يطابق المخطط.", None) from e
        raise LLMError(f"طلب مرفوض (400): {msg[:300]}", retryable=False) from e
    except openai.APIStatusError as e:
        if e.status_code >= 500:
            raise _Retry(f"خطأ من الخادم ({e.status_code}).", None) from e
        raise LLMError(f"خطأ ({e.status_code}).", retryable=False) from e
    except (openai.APIConnectionError, openai.APITimeoutError) as e:
        raise _Retry("مشكلة اتصال أو انتهى الوقت.", None) from e
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise _Retry("الجواب انقطع قبل ما يكمل (حد التوكنات).", None)
    text = choice.message.content or ""
    u = resp.usage
    return text, getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)


def _remember_limits(headers):
    try:
        found = {h: headers.get(h) for h in RATE_HEADERS if headers.get(h) is not None}
    except AttributeError:
        return
    if found:
        last_rate_limits.clear()
        last_rate_limits.update(found)


class _Retry(Exception):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(e):
    try:
        return float(e.response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


def call(system, user, schema=None, max_tokens=1024, provider=None, model=None,
         max_attempts=5, base_delay=2.0, max_delay=60.0, sleep=time.sleep, temperature=0) -> LLMResult:
    """استدعاء واحد مع إعادة محاولة بفواصل تزيد تدريجياً للأخطاء المؤقتة فقط."""
    provider = provider or config.PROVIDER
    model = model or config.MODEL
    last = None
    for attempt in range(max_attempts):
        t0 = time.monotonic()
        try:
            if provider == "anthropic":
                text, tin, tout = _anthropic_call(system, user, schema, max_tokens, model, temperature)
            else:
                text, tin, tout = _openai_compat_call(provider, system, user, schema, max_tokens, model, temperature)
        except _Retry as r:
            last = r
            if attempt == max_attempts - 1:
                break
            if r.retry_after and r.retry_after > config.MAX_RATE_LIMIT_WAIT:
                # حد يومي أو انتظار طويل: ما نعلّق البرنامج، نسجل الفشل والموظف يعيد المحاولة بعدين
                mins = max(1, round(r.retry_after / 60))
                raise LLMError(f"خلص حد الاستخدام المجاني حالياً. أعد المحاولة بعد {mins} دقيقة تقريباً.",
                               retryable=True)
            delay = r.retry_after or min(base_delay * 2**attempt + random.uniform(0, 1), max_delay)
            sleep(delay)
            continue
        latency = int((time.monotonic() - t0) * 1000)
        data = None
        if schema:
            try:
                data = json.loads(_strip_fences(text))
            except json.JSONDecodeError:
                last = _Retry("الموديل رجّع JSON مو صالح.", None)
                if attempt == max_attempts - 1:
                    break
                continue
        return LLMResult(data, text, tin, tout, latency)
    raise LLMError(f"فشل بعد {max_attempts} محاولات: {last}", retryable=True)


def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()
