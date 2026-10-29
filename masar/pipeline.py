"""معالجة الشكاوى: فهم واستخراج (موديل) -> فحص وقواعد (كود) -> مسودة (موديل + دليل) -> فحص المسودة.

ما في أي إرسال للزبون هنا. كل النتائج تنحفظ وتنتظر مراجعة الموظف.
"""
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from . import config, db, llm, prompts
from .ingest import ARABIC_DIGITS, clip

MODEL_FLAGS = {"multiple_issues", "unclear_message", "similar_names", "possible_injection"}


def _digits_only(s):
    return re.sub(r"\D", "", (s or "").translate(ARABIC_DIGITS))


# أي إشارة لوقت أو تاريخ بنص الرسالة. إذا ماكو ولا وحدة، التاريخ المستخرج مو مسنود.
_TIME_WORDS = ["اليوم", "البارحة", "البارح", "امبارح", "مبارح", "امس", "أمس", "الصبح", "الصباح", "العصر", "الظهر",
               "المسا", "المساء", "الليل", "الليلة", "البارحة بالليل", "هسه", "هسة", "الحين", "قبل يوم", "قبل يومين",
               "قبل ثلاث", "قبل كم يوم", "قبل اسبوع", "قبل أسبوع", "الأسبوع", "الاسبوع", "الشهر", "بتاريخ", "تاريخ",
               "كانون", "شباط", "آذار", "اذار", "نيسان", "أيار", "ايار", "حزيران", "تموز", "آب", "أيلول", "ايلول",
               "تشرين", "الجمعة", "السبت", "الأحد", "الاحد", "الاثنين", "الثلاثاء", "الأربعاء", "الاربعاء",
               "الخميس", "today", "yesterday"]
_TIME_RE = re.compile(r"(?<!\w)(?:[وبفل]|بال|وال)?(?:" + "|".join(map(re.escape, _TIME_WORDS)) + r")(?!\w)", re.I)
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*[/\-]\s*\d{1,2}|يوم\s*\d{1,2}")


def has_time_reference(message: str) -> bool:
    msg = message.translate(ARABIC_DIGITS)
    return bool(_TIME_RE.search(msg) or _DATE_RE.search(msg))


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
            if has_time_reference(message):
                fields["date"] = parsed.isoformat()
            else:
                # غالباً الموديل حط تاريخ الاستلام. ما نخليه يوصل للمسودة.
                reasons.append("unsupported_date")
        except (ValueError, TypeError):
            reasons.append("invalid_date")
    return fields, reasons


_DRAFT_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def draft_unsupported_dates(draft: str, message: str, date_field: str | None):
    """تواريخ بصيغة YYYY-MM-DD بالمسودة مو موجودة بالحقول المسنودة ولا بنص الرسالة."""
    msg = message.translate(ARABIC_DIGITS)
    return [d for d in _DRAFT_DATE_RE.findall((draft or "").translate(ARABIC_DIGITS))
            if d != date_field and d not in msg]


_NUM = r"[0-9٠-٩]+(?:[,،٬][0-9٠-٩]{3})*"  # الفاصلة تنحسب بس إذا بعدها 3 أرقام
_AMOUNT_RE = re.compile(
    rf"(?<![0-9٠-٩])({_NUM})(\s*(?:ألف|الف|آلاف|الاف))?(\s*(?:دينار عراقي|دينار|د\.ع\.?|IQD))?"
)


def format_amounts_in_draft(draft: str, amount: int | None) -> str:
    """أي كتابة لنفس المبلغ بالمسودة (75000، 75,000 د.ع، ٧٥ الف دينار) تصير بالشكل الموحد.

    الأرقام الثانية (مثل رقم العملية) ما تتغير لأن قيمتها ما تساوي المبلغ.
    """
    if not draft or amount is None:
        return draft
    target = config.format_amount(amount)

    def repl(m):
        try:
            value = int(re.sub(r"[,،٬]", "", m.group(1)).translate(ARABIC_DIGITS))
        except ValueError:
            return m.group(0)
        if m.group(2):
            value *= 1000
        if value != amount:
            return m.group(0)
        trail = " " if m.group(0).endswith(" ") else ""
        return target + trail

    return _AMOUNT_RE.sub(repl, draft)


def check_draft(draft: str):
    found = [p for p in prompts.forbidden_phrases() if p in draft]
    return found


# --- وعود وادعاءات إجراء: نفس القواعد لفحص المسودة ولفحص الاعتماد ---
# أفعال الإجراء والإحالة والمتابعة والرد. بادئة المستقبل لحالها (ب، ح، س) ما تكفي: لازم يجي بعدها فعل من هالقائمة،
# فكلمات مثل «بنفس» و«بنك» و«سنة» ما تنحسب.
_ACT = (r"(?:قوم|تابع|راجع|بلغ|بلّغ|وافي|رد|رجع|تواصل|تصل|اتصل|نقل|حول|حوّل|رفع|رسل|ارسل|أرسل|دز|خبر|طمن|"
        r"عالج|حل|حاول|وقف|اوقف|أوقف|شوف|تحقق|فحص|عمل\s+على|هتم|اهتم|نظر|ساعد|أكد|اكد|واكد)")
_PROMISE_RES = [
    # وعد بالمستقبل من طرفنا: سنقوم، حنوافيك، بنبلغ الفريق، راح نتابع
    re.compile(rf"(?<!\w)[وف]?(?:(?:راح|رح|سوف|ح)\s+|[سحب])ن{_ACT}"),
    # وعد عن الموظف أو القسم أو النظام: سيتواصل، بيتابعها، راح يراجع، سيتم
    re.compile(rf"(?<!\w)[وف]?(?:(?:راح|رح|سوف|ح)\s+|[سحب])ي{_ACT}"),
    re.compile(r"(?<!\w)[وف]?(?:(?:راح|رح|سوف)\s+|[سحب])[يت]تم(?!\w)"),
    # وعد بلا أداة مستقبل: نوافيك، نتواصل وياك، يتواصل معك
    re.compile(r"(?<!\w)[وف]?ن(?:وافي|رجعل|بلغ|خبر|طمن)(?:ك|كم)(?!\w)"),
    re.compile(r"(?<!\w)[وف]?[نيت](?:تواصل|تصل)\s+(?:وياك|معك|بيك|وياكم|معكم|بيكم)"),
    # وعد بوقت
    re.compile(r"ب(?:أ|ا)(?:قرب|سرع)\s+وقت|(?<!\w)[وف]?قريباً?(?!\w)|بالقريب العاجل|خلال\s+\S+\s+(?:ساعة|ساعات|يوم|أيام|ايام)"),
    # إحالة: وصل طلبك لقسم البطاقات، تحولت للقسم، انرسل إلى الموظف
    re.compile(r"(?:وصل|وصلت|تحول|تحوّل|تحولت|تحوّلت|انحول|انحوّل|انرسل|انرسلت|انحال|انحالت)"
               r"\s+(?:\S+\s+){0,2}?(?:لل|ل|الى\s+|إلى\s+)(?:قسم|القسم|موظف|الموظف|فريق|الفريق|الجهة)"),
    # ادعاء إجراء صار: تم إيقاف، تمت مراجعة، حظرنا، تواصلنا
    re.compile(r"(?<!\w)[وف]?تم\s+(?:إيقاف|ايقاف|حظر|تجميد|إلغاء|الغاء|رفع|إرسال|ارسال|مراجعة|فحص|التواصل|إبلاغ|ابلاغ|"
               r"تحويل\s+(?:طلبك|شكواك|الطلب|الشكوى))"),
    re.compile(r"(?<!\w)[وف]?تمت\s+(?:مراجعة|متابعة|إحالة|احالة)"),
    re.compile(r"(?<!\w)[وف]?(?:وقفنا|أوقفنا|اوقفنا|حظرنا|جمدنا|رفعنا|راجعنا|تابعنا|تواصلنا|اتصلنا|حولنا|حوّلنا|أرسلنا|ارسلنا|"
               r"دزينا|بلّغنا|فحصنا|نقلنا)(?!\w)"),
]
# النفي يلغي الوعد بس إذا جاي مباشرة قبله ("ما راح نتابع"، "ما بنتواصل")، مو أي نفي بنفس الجملة ("لا تقلق بنتابع").
_PROMISE_NEG = re.compile(r"(?:^|\s)(?:ما|لا|مو|ماراح|لن|ولا|وما|بدون|دون)\s*$")
# إسناد الكلام للزبون أو لطرف ثاني نقل عنه الزبون: "ذكرت إن الوكيل گال لك ..."
_ATTRIBUTION = re.compile(r"(?<!\w)(?:ذكرت|ذكرتي|ذكرتلنا|گلت|گلتي|گتلنا|قلت|كتبت|تگول|تقول|گال|قال|گالولك|قالولك|گالوا|قالوا|"
                          r"وعدك|وعدوك|تريد|تطلب|طلبت|حسب\s+كلامك|حسب\s+رسالتك)(?!\w)")
_CLAUSE_END = ".!؟?\n،,؛;"
_ATTRIBUTION_WINDOW = 6  # أقصى عدد كلمات بين الإسناد والعبارة المنقولة


_TASHKEEL = re.compile(r"[\u064B-\u0652\u0670\u0640]")  # حركات وشدة وتطويل


def _spaces(text):
    return re.sub(r"\s+", " ", _TASHKEEL.sub("", text or "")).strip()


def _quoted_from_customer(before: str, hit: str, msg: str) -> bool:
    """العبارة منقولة بوضوح عن الزبون: مكتوبة برسالته، وقبلها بنفس الجملة الفرعية إسناد قريب ("ذكرت إن…").

    علامات التنصيص لحالها ما تكفي، ولا وجود العبارة برسالة الزبون لحاله. أي غموض = وعد يحتاج تصحيح.
    """
    if not msg or _spaces(hit) not in msg:
        return False
    attributions = list(_ATTRIBUTION.finditer(before))
    if not attributions:
        return False
    between = before[attributions[-1].end():]
    return len(re.sub(r"[«»\"“”]", " ", between).split()) <= _ATTRIBUTION_WINDOW


def promises_in_draft(draft: str, message: str | None = None):
    """وعود بإحالة أو متابعة أو رد، أو ادعاء إجراء ما صار. يرجع العبارات اللي انمسكت.

    ما ينحسب بس:
    - وعد منفي مباشرة ("ما راح نتواصل وياك"). النفي ما يلغي وعد ثاني بنفس الجملة.
    - كلام منقول بوضوح عن الزبون (_quoted_from_customer). كل إسناد يغطي أول عبارة بعده بس،
      فتكرار نفس الكلام بعده كالتزام من الدعم ينمسك.
    """
    text = _TASHKEEL.sub("", draft or "")
    msg = _spaces(message)
    matches = sorted((m for rx in _PROMISE_RES for m in rx.finditer(text)), key=lambda m: m.start())
    found, used_attr, excused = [], set(), []  # excused: مدى العبارات المنفية أو المنقولة
    for m in matches:
        if any(m.start() < e and m.end() > s for s, e in excused):  # جزء من نفس العبارة ("ما راح نتواصل وياك")
            continue
        clause_start = max(text.rfind(c, 0, m.start()) for c in _CLAUSE_END) + 1
        before = text[clause_start: m.start()]
        hit = m.group(0).strip()
        if _PROMISE_NEG.search(before):
            excused.append(m.span())
            continue
        if _quoted_from_customer(before, hit, msg):
            attr_pos = clause_start + list(_ATTRIBUTION.finditer(before))[-1].start()
            if attr_pos not in used_attr:
                used_attr.add(attr_pos)
                excused.append(m.span())
                continue
        if hit not in found:
            found.append(hit)
    return found


# طلب معلومة: جملة بيها فعل طلب (مو منفي) ومعلومة من القائمة، بأي ترتيب. التذكير ("ما راح نطلب الرمز") ما ينحسب.
_ASK = (r"(?:زودنا|زوّدنا|تزويدنا|تزودنا|ترسل|ارسل|أرسل|دز|اكتب|شاركنا|مشارك|نحتاج|نطلب|ابعث|يرجى|نرجو|"
        r"ممكن\s+(?:ت|تنطي|تعطي)|انطينا|عطينا)")
_NEG = r"(?:ما|لا|مو|أبداً|ابداً|ماراح|ما راح)\s+(?:\S+\s+){0,2}$"
_SENSITIVE = [r"رقم\s*الجهاز", r"IMEI", r"نسخة\s*(?:النظام|التطبيق)", r"الرمز\s*السري", r"الرقم\s*السري", r"\bPIN\b",
              r"رمز\s*التحقق", r"\bOTP\b", r"كلمة\s*(?:المرور|السر)", r"رقم\s*البطاقة\s*(?:الكامل|كامل)", r"CVV",
              r"تاريخ\s*انتهاء\s*البطاقة", r"الرموز\s*الخلفية"]
_CARD_DIGITS = r"(?:آخر|اخر|أخر)\s*(?:4|٤|أربع|اربع|اربعة|أربعة)\s*(?:أرقام|ارقام)|أرقام\s*البطاقة|ارقام\s*البطاقة|رقم\s*البطاقة"
_CARD_NOT_ARRIVED = re.compile(r"بطاق\w*.{0,60}?(?:ما\s*(?:وصل|وصلت|وصلتني|وصلني|استلم|استلمت|اجت|إجت)|لحد\s*الآن)|"
                               r"(?:ما\s*(?:وصل|وصلت|وصلتني|وصلني|استلمت|اجت))\s*(?:\S+\s+){0,3}بطاق", re.S)


def _requests(draft, pattern):
    """الجمل اللي بيها فعل طلب غير منفي ومعلومة تطابق pattern."""
    hits = []
    for sent in re.split(r"[.!؟?\n]", draft or ""):
        if not re.search(pattern, sent, re.I):
            continue
        if any(not re.search(_NEG, sent[: m.start()]) for m in re.finditer(_ASK, sent)):
            hits.append(sent.strip())
    return hits


def sensitive_requests(draft: str):
    """المسودة تطلب رقم الجهاز أو رمز أو كلمة مرور أو رقم البطاقة الكامل."""
    return _requests(draft, "|".join(_SENSITIVE))


def irrelevant_requests(draft: str, message: str):
    """طلبات ما تناسب الحالة: أرقام بطاقة لبطاقة الزبون يگول ما وصلته."""
    if _CARD_NOT_ARRIVED.search(message or ""):
        return _requests(draft, _CARD_DIGITS)
    return []


def draft_missing_issue(draft: str, secondary_category: str | None, category: str):
    """الرسالة بيها مشكلة ثانية والمسودة ما بيها أي كلمة تدل عليها."""
    if not secondary_category or secondary_category == category:
        return False
    words = config.CATEGORY_KEYWORDS.get(secondary_category) or []
    return bool(words) and not any(w in (draft or "") for w in words)


# تفاصيل بالتبرير لازم يكون إلها أصل بالرسالة (جذر الكلمة لازم ينذكر)
_REASON_CLAIMS = {
    "خصم": ["خصم", "استقطع", "قطع", "سحب", "نقص"],
    "ناجح": ["ناجح", "نجح", "تم"],
    "نجح": ["ناجح", "نجح", "تم"],
    "فاشل": ["فاشل", "فشل", "ما تم", "ما صار", "ما وصل"],
    "مرتين": ["مرتين", "مره ثانية", "مرة ثانية", "تكرر", "دبل"],
    "سرق": ["سرق"],
    "اختراق": ["اختراق", "اخترق", "هكر", "دخل"],
}


def reason_unsupported(reason: str, message: str):
    """تفاصيل بالتبرير مو مذكورة بالرسالة: أرقام، أو ادعاء (خصم، نجاح، سرقة...) بلا أصل بالرسالة.

    تشابه التبرير ويا مثال مو خطأ بحد ذاته؛ الخطأ هو المعلومة غير المسندة.
    """
    reason, msg = (reason or "").translate(ARABIC_DIGITS), (message or "").translate(ARABIC_DIGITS)
    found = []
    msg_nums = set(re.findall(r"\d+", re.sub(r"[,،٬]", "", msg)))
    for n in re.findall(r"\d+", re.sub(r"[,،٬]", "", reason)):
        if n not in msg_nums:
            found.append(f"رقم: {n}")
    for claim, roots in _REASON_CLAIMS.items():
        if claim in reason and not any(r in msg for r in roots):
            found.append(claim)
    return found


def approval_blockers(draft: str, message: str | None = None) -> list[str]:
    """أسباب تمنع اعتماد المسودة (الأصلية أو المعدّلة أو المعاد كتابتها): عبارة ممنوعة، وعد أو ادعاء إجراء، طلب معلومة حساسة.

    نفس القواعد اللي يستخدمها فحص المسودة بعد المودل (draft_review_reasons).
    """
    out = [f"عبارة ممنوعة: {p}" for p in check_draft(draft or "")]
    out += [f"وعد أو ادعاء إجراء: {p}" for p in promises_in_draft(draft, message)]
    out += [f"طلب معلومة حساسة: {s}" for s in sensitive_requests(draft)]
    return out


def draft_review_reasons(draft, message, date_field, category, secondary_category=None):
    """كل فحوصات المسودة بمكان واحد. يرجع أسباب المراجعة."""
    checks = [
        ("draft_forbidden_phrase", check_draft(draft)),
        ("draft_unsupported_date", draft_unsupported_dates(draft, message, date_field)),
        ("draft_unsupported_action", promises_in_draft(draft, message)),
        ("draft_sensitive_request", sensitive_requests(draft)),
        ("draft_irrelevant_request", irrelevant_requests(draft, message)),
        ("draft_missing_issue", draft_missing_issue(draft, secondary_category, category)),
        ("draft_too_long", len(draft or "") > config.MAX_DRAFT_CHARS),
    ]
    return [name for name, hit in checks if hit]


def _logger(calls, stage):
    """يرجع دالة on_request تضيف كل طلب HTTP لقائمة calls مع المرحلة."""
    if calls is None:
        return None
    return lambda rec: calls.append({**rec, "stage": stage})


def _sum_tokens(*vals):
    """مجموع التوكنات، أو None إذا أي جزء غير معروف (ما نعتبره صفر)."""
    return None if any(v is None for v in vals) else sum(vals)


def analyze(complaint: dict, with_draft=True, provider=None, model=None, calls=None) -> dict:
    """الجزء اللي يتصل بالموديل. ما يكتب بالقاعدة (حتى يشتغل بخيوط متوازية).

    calls: قائمة (اختيارية) تنضاف لها سجلات كل طلب HTTP، حتى لو الشكوى فشلت بالنص.
    """
    message, clipped = clip(complaint["message"])
    received_at = complaint.get("received_at")

    system, user = prompts.classify_messages_with_examples(message, received_at)
    res = llm.call(system, user, schema=prompts.CLASSIFY_SCHEMA, max_tokens=config.MAX_TOKENS_CLASSIFY, provider=provider,
                   model=model, on_request=_logger(calls, "classify"))
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
    secondary = x.get("secondary_category") if x.get("secondary_category") in config.CATEGORIES else None
    if secondary == category:
        secondary = None
    if secondary and "multiple_issues" not in flags:
        flags.append("multiple_issues")

    if confidence < config.CONFIDENCE_THRESHOLD:
        reasons.insert(0, "low_confidence")
    reasons.extend(flags)
    if clipped:
        reasons.append("message_too_long")
    reason = (x.get("reason") or "").strip()
    if reason_unsupported(reason, message):
        reasons.append("reason_unsupported")

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
        secondary_category=secondary,
        reason=reason,
        model_flags=flags,
        raw_output=x,
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
        dsys, duser = prompts.draft_prompts(message, category, fields, missing, secondary)
        dres = llm.call(dsys, duser, max_tokens=config.MAX_TOKENS_DRAFT, provider=provider, model=model,
                        on_request=_logger(calls, "draft"))
        draft = format_amounts_in_draft(dres.text.strip(), fields["amount"])
        reasons.extend(draft_review_reasons(draft, message, fields["date"], category, secondary))
        out["draft"] = draft
        out["latency_ms"] += dres.latency_ms
        out["input_tokens"] = _sum_tokens(out["input_tokens"], dres.input_tokens)
        out["output_tokens"] = _sum_tokens(out["output_tokens"], dres.output_tokens)

    out["review_reasons"] = list(dict.fromkeys(reasons))
    out["needs_review"] = int(bool(out["review_reasons"]))
    return out


def _resume_at(seconds):
    if not seconds:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def process(complaint_ids, with_draft=True, workers=None, on_progress=None, db_path=None, provider=None, model=None,
            respect_cooldown=True):
    """يعالج قائمة شكاوى. الفاشلة تنحفظ بحالة failed مع السبب، بدون تكرار سجلها.

    - كل شكوى تنمسك بقفل قبل ما تبدأ (db.claim). المكتملة أو اللي يعالجها تشغيل ثاني تنتجاوز.
    - إذا المزوّد أكد الحد اليومي أو طلب انتظار طويل، الدفعة توقف: اللي ما بدأت تبقى بحالتها
      ومحاولاتها ما تزيد، وسبب التوقف ووقت الاستئناف ينحفظون بـ processing_runs.
    - إذا تشغيل سابق وقف وبعده ما وصل وقت الاستئناف، ما نرسل ولا طلب (إلا respect_cooldown=False).
    - كل طلب HTTP ينسجل بـ llm_calls مع المرحلة والتوكنات.
    """
    run_id = uuid.uuid4().hex
    provider_name, model_name = provider or config.PROVIDER, model or config.MODEL
    ids = list(dict.fromkeys(complaint_ids))
    summary = {"done": 0, "failed": 0, "skipped": 0, "not_started": 0, "stopped": None, "resume_after": None,
               "run_id": run_id}

    with db.connect(db_path) as conn:
        db.start_run(conn, run_id, len(ids))
        cooldown = db.active_cooldown(conn) if respect_cooldown else None
    if cooldown:
        summary.update(not_started=len(ids), stopped=f"لسه بفترة انتظار من تشغيل سابق: {cooldown[0]}",
                       resume_after=cooldown[1])
        with db.connect(db_path) as conn:
            db.end_run(conn, run_id, not_started=len(ids), stop_reason=summary["stopped"], resume_after=cooldown[1])
        return summary

    stop = threading.Event()
    lock = threading.Lock()

    def run_one(cid):
        """يرجع done | failed | skipped | not_started."""
        if stop.is_set():
            return "not_started", None
        with db.connect(db_path) as conn:
            if not db.claim(conn, cid, run_id):
                return "skipped", None
            complaint = db.get_complaint(conn, cid)
        calls = []
        err = None
        try:
            result = analyze(complaint, with_draft, provider, model, calls=calls)
        except llm.LLMError as e:
            err = e
            result = {"status": "failed", "error": str(e)}
        except Exception as e:  # أي خطأ غير متوقع ما يوقف الدفعة
            result = {"status": "failed", "error": f"خطأ غير متوقع: {type(e).__name__}: {e}"}
        if err is not None and err.stop_batch:
            with lock:
                if not stop.is_set():
                    stop.set()
                    summary["stopped"] = str(err)
                    summary["resume_after"] = _resume_at(err.retry_after)
        with db.connect(db_path) as conn:
            db.log_calls(conn, [{**c, "run_id": run_id, "complaint_id": cid, "provider": provider_name,
                                 "model": model_name} for c in calls])
            saved = db.finish(conn, cid, run_id, **result)
        if not saved:
            return "skipped", None
        return result["status"], cid

    pool = ThreadPoolExecutor(max_workers=max(1, workers or config.WORKERS))
    try:
        futures = [pool.submit(run_one, cid) for cid in ids]
        for i, fut in enumerate(as_completed(futures), 1):
            status, cid = fut.result()
            summary[status] += 1
            if on_progress:
                on_progress(i, len(ids), cid, status)
    finally:
        # أي توقف (مثلاً الواجهة انقطعت بالنص) يمنع بدء شكاوى جديدة بالخلفية
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        counts = {k: summary[k] for k in ("done", "failed", "skipped", "not_started")}
        with db.connect(db_path) as conn:
            db.end_run(conn, run_id, started=counts["done"] + counts["failed"], stop_reason=summary["stopped"],
                       resume_after=summary["resume_after"], **counts)
    return summary


def regenerate_draft(complaint_id, db_path=None):
    """يعيد كتابة المسودة فقط حسب النتيجة المحفوظة (مثلاً بعد ما الموظف صحح الفئة)."""
    with db.connect(db_path) as conn:
        c = db.get_complaint(conn, complaint_id)
        r = dict(conn.execute("SELECT * FROM results WHERE complaint_id = ?", (complaint_id,)).fetchone())
        v = conn.execute("SELECT corrected_category FROM reviews WHERE complaint_id = ?", (complaint_id,)).fetchone()
    category = (v and v[0]) or r["category"]
    secondary = r.get("secondary_category")
    fields = {"transaction_id": r["transaction_id"], "amount": r["amount"], "date": r["date"]}
    missing = [f for f in config.REQUIRED_FIELDS[category] if fields[f] is None]
    message, _ = clip(c["message"])
    dsys, duser = prompts.draft_prompts(message, category, fields, missing, secondary)
    calls = []
    try:
        res = llm.call(dsys, duser, max_tokens=config.MAX_TOKENS_DRAFT, on_request=_logger(calls, "redraft"))
    finally:
        with db.connect(db_path) as conn:
            db.log_calls(conn, [{**c, "complaint_id": complaint_id, "provider": config.PROVIDER, "model": config.MODEL}
                                for c in calls])
    draft = format_amounts_in_draft(res.text.strip(), r["amount"])
    return draft, approval_blockers(draft, c["message"])
