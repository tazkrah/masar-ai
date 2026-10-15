"""معالجة الشكاوى: فهم واستخراج (موديل) -> فحص وقواعد (كود) -> مسودة (موديل + دليل) -> فحص المسودة.

ما في أي إرسال للزبون هنا. كل النتائج تنحفظ وتنتظر مراجعة الموظف.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from . import config, db, llm, prompts
from .ingest import ARABIC_DIGITS, clip

MODEL_FLAGS = {"multiple_issues", "unclear_message", "similar_names", "possible_injection"}


def _digits_only(s):
    return re.sub(r"\D", "", (s or "").translate(ARABIC_DIGITS))


def validate(extracted: dict, message: str, received_at: str | None):
    """فحص شكل الجواب. يرجع (حقول نظيفة، أسباب مراجعة). القيمة اللي شكلها غلط تنمسح وتنعلّم."""
    reasons = []
    fields = {"transaction_id": None, "amount": None, "date": None}

    txid = extracted.get("transaction_id")
    if txid:
        norm = _digits_only(txid)
        msg = message.translate(ARABIC_DIGITS)
        runs = set(re.findall(r"\d+", msg)) | set(re.findall(r"\d+", re.sub(r"(?<=\d)[\s\-](?=\d)", "", msg)))
        if len(norm) >= 4 and norm in runs:
            fields["transaction_id"] = norm
        else:
            reasons.append("invalid_transaction_id")

    amount = extracted.get("amount_iqd")
    if amount is not None:
        if isinstance(amount, int) and 250 <= amount <= 100_000_000:
            fields["amount"] = amount
        else:
            reasons.append("invalid_amount")

    d = extracted.get("date")
    if d:
        try:
            parsed = date.fromisoformat(d)
            upper = date.fromisoformat(received_at[:10]) if received_at else None
            if parsed.year < 2020 or (upper and parsed > upper):
                raise ValueError
            fields["date"] = parsed.isoformat()
        except (ValueError, TypeError):
            reasons.append("invalid_date")
    return fields, reasons


def check_draft(draft: str):
    found = [p for p in prompts.forbidden_phrases() if p in draft]
    return found


def analyze(complaint: dict, with_draft=True, provider=None, model=None) -> dict:
    """الجزء اللي يتصل بالموديل. ما يكتب بالقاعدة (حتى يشتغل بخيوط متوازية)."""
    message, clipped = clip(complaint["message"])
    received_at = complaint.get("received_at")

    system, user = prompts.classify_messages_with_examples(message, received_at)
    res = llm.call(system, user, schema=prompts.CLASSIFY_SCHEMA, max_tokens=600, provider=provider, model=model)
    x = res.data
    if x.get("category") not in config.CATEGORIES:
        raise llm.LLMError(f"فئة غير معروفة من الموديل: {x.get('category')}", retryable=True)

    fields, reasons = validate(x, message, received_at)
    category = x["category"]
    security_risk = bool(x.get("security_risk"))
    try:
        confidence = max(0.0, min(1.0, float(x.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    flags = [f for f in (x.get("flags") or []) if f in MODEL_FLAGS]
    if x.get("secondary_category") and x["secondary_category"] != category and "multiple_issues" not in flags:
        flags.append("multiple_issues")

    if confidence < config.CONFIDENCE_THRESHOLD:
        reasons.insert(0, "low_confidence")
    reasons.extend(flags)
    if clipped:
        reasons.append("message_too_long")

    missing = [f for f in config.REQUIRED_FIELDS[category] if fields[f] is None]
    out = dict(
        status="done",
        category=category,
        priority=config.priority_for(category, fields["amount"], security_risk),
        department=config.ROUTING[category],
        transaction_id=fields["transaction_id"],
        amount=fields["amount"],
        date=fields["date"],
        security_risk=int(security_risk),
        confidence=confidence,
        reason=(x.get("reason") or "").strip(),
        model_flags=flags,
        missing_fields=missing,
        provider=provider or config.PROVIDER,
        model=model or config.MODEL,
        prompt_version=config.PROMPT_VERSION,
        error=None,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        draft=None,
    )

    if with_draft:
        dsys, duser = prompts.draft_prompts(message, category, fields, missing)
        dres = llm.call(dsys, duser, max_tokens=500, provider=provider, model=model)
        draft = dres.text.strip()
        if check_draft(draft):
            reasons.append("draft_forbidden_phrase")
        out["draft"] = draft
        out["latency_ms"] += dres.latency_ms
        if dres.input_tokens is not None and out["input_tokens"] is not None:
            out["input_tokens"] += dres.input_tokens
            out["output_tokens"] += dres.output_tokens

    out["review_reasons"] = list(dict.fromkeys(reasons))
    out["needs_review"] = int(bool(out["review_reasons"]))
    return out


def process(complaint_ids, with_draft=True, workers=4, on_progress=None, db_path=None, provider=None, model=None):
    """يعالج قائمة شكاوى. الفاشلة تنحفظ بحالة failed مع السبب، بدون تكرار سجلها."""
    with db.connect(db_path) as conn:
        complaints = [db.get_complaint(conn, cid) for cid in complaint_ids]
        for c in complaints:
            db.bump_attempts(conn, c["id"])

    done = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(analyze, c, with_draft, provider, model): c["id"] for c in complaints}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                result = fut.result()
            except llm.LLMError as e:
                result = {"status": "failed", "error": str(e)}
            except Exception as e:  # أي خطأ غير متوقع ما يوقف الدفعة
                result = {"status": "failed", "error": f"خطأ غير متوقع: {type(e).__name__}: {e}"}
            with db.connect(db_path) as conn:
                db.save_result(conn, cid, **result)
            if result["status"] == "done":
                done += 1
            else:
                failed += 1
            if on_progress:
                on_progress(done + failed, len(complaints), cid, result["status"])
    return {"done": done, "failed": failed}


def regenerate_draft(complaint_id, db_path=None):
    """يعيد كتابة المسودة فقط حسب النتيجة المحفوظة (مثلاً بعد ما الموظف صحح الفئة)."""
    with db.connect(db_path) as conn:
        c = db.get_complaint(conn, complaint_id)
        r = dict(conn.execute("SELECT * FROM results WHERE complaint_id = ?", (complaint_id,)).fetchone())
        v = conn.execute("SELECT corrected_category FROM reviews WHERE complaint_id = ?", (complaint_id,)).fetchone()
    category = (v and v[0]) or r["category"]
    fields = {"transaction_id": r["transaction_id"], "amount": r["amount"], "date": r["date"]}
    missing = [f for f in config.REQUIRED_FIELDS[category] if fields[f] is None]
    message, _ = clip(c["message"])
    dsys, duser = prompts.draft_prompts(message, category, fields, missing)
    draft = llm.call(dsys, duser, max_tokens=500).text.strip()
    return draft, check_draft(draft)
