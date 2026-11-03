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


# يوم بدون شهر: «يوم 27»، «يوم 27 الشهر»، «27 من الشهر». مو «يوم 27/9» ولا «يوم 27 أيلول».
_MONTH_NAMES = ["كانون", "شباط", "آذار", "اذار", "نيسان", "أيار", "ايار", "حزيران", "تموز", "آب", "أيلول", "ايلول",
                "تشرين"]
_DAY_ONLY_RE = re.compile(
    r"(?:(?<!\w)[وب]?يوم\s*(\d{1,2})|(?<!\d)(\d{1,2})\s*(?:من\s+)?(?:ال|بال)شهر)"
    r"(?!\d)(?!\s*[/\-.]\s*\d)(?!\s*(?:من\s+)?(?:شهر\s*\d|" + "|".join(_MONTH_NAMES) + r"))"
)


# «يوم 27 الشهر الماضي»: الشهر محسوم بالنص
_PAST_MONTH_RE = re.compile(r"\s*(?:(?:من\s+)?(?:ال|بال)?شهر\s*)?(?:الماضي|السابق|الفايت|الفات|اللي\s*(?:فات|راح|قبله))")


def ambiguous_days(message: str, received_at: str | None) -> set[int]:
    """أيام مذكورة بدون شهر وهي بعد يوم الاستلام، فما نعرف أي شهر يقصد (ما نفترض الشهر السابق).

    اليوم اللي أصغر من يوم الاستلام أو يساويه ينحسب من نفس شهر الاستلام. بدون تاريخ استلام ما نحكم.
    """
    if not received_at:
        return set()
    try:
        today = date.fromisoformat(received_at[:10]).day
    except ValueError:
        return set()
    msg = (message or "").translate(ARABIC_DIGITS)
    days = {int(m.group(1) or m.group(2)) for m in _DAY_ONLY_RE.finditer(msg)
            if not _PAST_MONTH_RE.match(msg, m.end())}
    return {d for d in days if today < d <= 31}


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
        if not (isinstance(amount, int) and AMOUNT_MIN <= amount <= AMOUNT_MAX):
            reasons.append("invalid_amount")
        else:
            # المبلغ لازم يطابق مبلغ مكتوب بالرسالة بأي صيغة («خمسمية الف» = 500000). ما نصحح ولا نختار بداله.
            found = message_amounts(message, fields["transaction_id"])
            how = message_amount_context(message, amount, fields["transaction_id"])
            if how == "clear":
                fields["amount"] = amount
                if len(found) > 1:
                    reasons.append("multiple_amounts")  # الموظف يتأكد إن المبلغ المختار يخص المشكلة الصحيحة
            elif how == "unclear":
                reasons.append("amount_context_unclear")  # رقم مجرد ما يبين إنه مبلغ: الموظف يقرر
            else:
                reasons.append("amount_not_in_message")

    d = extracted.get("date")
    unclear_days = ambiguous_days(message, received_at)
    if unclear_days:
        reasons.append("ambiguous_date")  # حتى لو الموديل ما طلّع تاريخ: الموظف لازم يعرف إن الشهر مو محسوم
    if d:
        try:
            parsed = date.fromisoformat(d)
            upper = date.fromisoformat(received_at[:10]) if received_at else None
            if parsed.year < 2020 or (upper and parsed > upper):
                raise ValueError
            if parsed.day in unclear_days:
                pass  # الموديل اختار شهر من عنده؛ التاريخ يبقى فارغ
            elif has_time_reference(message):
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
    rf"(?<![0-9٠-٩])({_NUM})(\s*(?:ألف|الف|آلاف|الاف|مليون))?(\s*(?:دينار عراقي|دينار|د\.ع\.?|IQD))?"
)


def _amount_value(m):
    """قيمة تطابق _AMOUNT_RE: 75000، 75,000، ٧٥ الف، 2 مليون."""
    value = int(re.sub(r"[,،٬]", "", m.group(1)).translate(ARABIC_DIGITS))
    if m.group(2):
        value *= 1_000_000 if "مليون" in m.group(2) else 1000
    return value


# أرقام بالحروف (عراقي وفصحى): «مية وخمسين الف»، «ربع مليون»، «ثلاث ايام». ما تعتمد على جداول توليد البيانات.
_UNIT_STEMS = {"ثلاث": 3, "ثلث": 3, "تلاث": 3, "تلت": 3, "اربع": 4, "أربع": 4, "خمس": 5, "ست": 6, "سبع": 7,
               "ثمان": 8, "ثمن": 8, "تسع": 9}
_NUM_WORDS = {
    **{w: ("n", 1) for w in ("واحد", "وحدة", "وحده")},
    **{w: ("n", 2) for w in ("اثنين", "اثنان", "إثنين", "ثنين")},
    **{w: ("n", 3) for w in ("ثلاث", "ثلاثة", "ثلاثه", "تلاث", "تلاثة")},
    **{w: ("n", 4) for w in ("اربع", "أربع", "اربعة", "أربعة", "اربعه")},
    **{w: ("n", 5) for w in ("خمس", "خمسة", "خمسه")},
    **{w: ("n", 6) for w in ("ستة", "سته")},  # «ست» و«ثمن» لحالهن غالباً مو أرقام (ست البيت، ثمن البطاقة)
    **{w: ("n", 7) for w in ("سبع", "سبعة", "سبعه")},
    **{w: ("n", 8) for w in ("ثمان", "ثماني", "ثمانية", "ثمانيه")},
    **{w: ("n", 9) for w in ("تسع", "تسعة", "تسعه")},
    **{w: ("n", 10) for w in ("عشر", "عشرة", "عشره")},
    **{w: ("n", 11) for w in ("احدعش", "حدعش", "إحدعش")},
    **{w: ("n", 12) for w in ("اثنعش", "طنعش", "اطنعش")},
    "عشرين": ("n", 20),
    **{w: ("n", 30) for w in ("ثلاثين", "ثلثين", "تلاثين", "تلثين")},
    **{w: ("n", 40) for w in ("اربعين", "أربعين")},
    "خمسين": ("n", 50), "ستين": ("n", 60), "سبعين": ("n", 70),
    **{w: ("n", 80) for w in ("ثمانين", "ثمنين")},
    "تسعين": ("n", 90),
    **{w: ("h", 100) for w in ("مية", "ميه", "مئة", "مائة", "ميت")},
    **{w: ("n", 200) for w in ("ميتين", "مئتين", "مائتين", "ميتان")},
    **{w: ("s", 1000) for w in ("الف", "ألف", "آلاف", "الاف", "تالاف")},
    **{w: ("d", 1000) for w in ("الفين", "ألفين")},  # المثنى: مرتين المرتبة
    **{w: ("s", 1_000_000) for w in ("مليون", "ملايين")},
    "مليونين": ("d", 1_000_000),
    **{w: ("f", 0.5) for w in ("نص", "نصف")},
    "ربع": ("f", 0.25),
}
_TEEN_RE = re.compile(r"(" + "|".join(_UNIT_STEMS) + r")[طت]عش")
_HUNDREDS_RE = re.compile(r"(" + "|".join(_UNIT_STEMS) + r")(?:مية|ميه|مئة|مائة)")


def _number_word(tok):
    """(نوع، قيمة) لكلمة رقم، مع شيل «و» أو «ب» من أولها. None إذا مو رقم."""
    for w in (tok, tok[1:] if tok[:1] in "وب" else None, tok[2:] if tok[:2] in ("وب", "بـ") else None):
        if not w:
            continue
        if w in _NUM_WORDS:
            return _NUM_WORDS[w]
        if m := _TEEN_RE.fullmatch(w):
            return "n", 10 + _UNIT_STEMS[m.group(1)]
        if m := _HUNDREDS_RE.fullmatch(w):
            return "n", 100 * _UNIT_STEMS[m.group(1)]
    return None


def _word_numbers(text):
    """قيم الأرقام المكتوبة بالحروف. كل سلسلة كلمات أرقام متتالية = قيمة وحدة («مليون ونص» = 1500000)."""
    vals = []
    total = cur = last_scale = 0
    active = after_digits = False

    def flush():
        v = total + cur
        if active and v >= 1 and v == int(v):
            vals.append(int(v))

    for tok in re.findall(r"[0-9]+|[^\W\d_]+", text):
        hit = None if tok.isdigit() else _number_word(tok)
        if hit and hit[0] in "sd" and after_digits:  # «75 ألف» محسوبة من _AMOUNT_RE
            hit = None
        if not hit:
            flush()
            total = cur = last_scale = 0
            active, after_digits = False, tok.isdigit()
            continue
        kind, v = hit
        if kind == "n":
            cur += v
        elif kind == "h":
            cur = cur * 100 if 0 < cur < 10 else cur + 100
        elif kind in "sd":
            total += (cur or (2 if kind == "d" else 1)) * v
            cur, last_scale = 0, v
        elif kind == "f":
            if last_scale and not cur:  # «مليون ونص»
                total += v * last_scale
            else:  # «نص مليون»، «ربع مليون»
                cur += v
        active, after_digits = True, False
    flush()
    return vals


def number_values(text):
    """كل الأرقام بالنص كقيم: أرقام عادية، «٧٥ الف» = 75000، «مية وخمسين الف» = 150000، «ثلاث» = 3.

    يرجع [(النص الأصلي أو القيمة، القيمة)] حتى نگدر نعرض الرقم مثل ما انكتب.
    """
    t = (text or "").translate(ARABIC_DIGITS)
    out = []
    for m in _AMOUNT_RE.finditer(t):
        out.append((m.group(0).strip(), _amount_value(m)))
    out += [(str(v), v) for v in _word_numbers(t)]
    return out


def _message_number_values(message):
    """القيم اللي الرسالة تسندها: كل رقم مثل ما مكتوب (25 من «25 ألف» أو «يوم 25») وقيمته الكاملة."""
    t = (message or "").translate(ARABIC_DIGITS)
    vals = {int(n) for n in re.findall(r"\d+", re.sub(r"(?<=\d)[,،٬](?=\d{3})", "", t))}
    return vals | {v for _, v in number_values(t)}


AMOUNT_MIN, AMOUNT_MAX = 250, 100_000_000

# أرقام مو مبالغ: تواريخ، يوم الشهر، وأرقام العمليات (بعد # أو «رقم…»). تنشال قبل البحث عن المبالغ.
_SEP = r"[-‑–/.]"
_NOT_AMOUNT_RES = [
    re.compile(rf"\d{{4}}\s*{_SEP}\s*\d{{1,2}}\s*{_SEP}\s*\d{{1,2}}"),
    re.compile(rf"\d{{1,2}}\s*{_SEP}\s*\d{{1,2}}\s*{_SEP}\s*\d{{4}}"),
    re.compile(r"(?<![\d,،٬.])\d{1,2}\s*[-‑–/]\s*\d{1,2}(?![\d,،٬])"),
    re.compile(r"[وب]?يوم\s*\d{1,2}(?!\d)"),
]
_TX_NUMBER_RE = re.compile(r"(?:#|رقم\w*\s+(?:(?:ال)?(?:عملية|معاملة|تحويل|تحويلة|حوالة|طلب)\w*\s+)?(?:(?:هو|هي|:)\s*)?#?)"
                           r"\s*(\d[\d \-]{2,}\d)")
# كسر عشري ويا مرتبة: «1.5 مليون»، «2.5 مليون»
_DECIMAL_SCALE_RE = re.compile(r"(?<![\d,،٬])(\d+)[.٫](\d+)\s*(مليون|الف|ألف)")

# سياق الرقم المجرد (بلا ألف أو مليون أو دينار):
# مو مالي: بعده وحدة وقت أو عدد («300 يوم»، «4 مرات»)، أو قبله كلمة تعريف («العملية 8822»، «المنتهية بـ 4521»).
_NONMONEY_AFTER_RE = re.compile(
    r"\s*(?:يوم|ايام|أيام|ساعة|ساعات|ساعه|دقيقة|دقيقه|دقايق|دقائق|ثانية|ثواني|مرة|مره|مرات|شهر|اشهر|أشهر|شهور|"
    r"اسبوع|أسبوع|اسابيع|أسابيع|سنة|سنه|سنين|محاولة|محاولات|ارقام|أرقام|خانة|خانات)(?!\w)")
_NONMONEY_BEFORE_RE = re.compile(
    r"(?:^|[^\w])[وب]?(?:ال)?(?:عملية|عمليه|معاملة|معامله|حوالة|حواله|طلب|مرجع|ايصال|إيصال|وصل|كود|رمز|"
    r"منتهية|منتهيه|تنتهي|ينتهي|آخر|اخر|ساعة|ساعه|(?:ارقام|أرقام|رقم)\w*)\s*(?:بـ?\s*)?[:#]?\s*$")
# مالي: قبله أو بعده بكم كلمة فعل أو اسم فلوس («حولت لصاحبي 85000»، «250000 انخصم مرتين»).
_MONEY_CUE_RE = re.compile(r"مبلغ|قيم|رصيد|فلوس|عمول|رسوم|خصم|خصي|حول|تحويل|دز|سحب|يداع|ودع|دفع|شحن|رجع|كاش|شراء|اشتر|صرف")
_MONEY_BEFORE, _MONEY_AFTER = 3, 2
_TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0640]")


def _non_amount_spans(t, txid=None):
    spans = [m.span() for rx in _NOT_AMOUNT_RES for m in rx.finditer(t)]
    spans += [m.span(1) for m in _TX_NUMBER_RE.finditer(t)]
    d = _digits_only(txid)
    if d:  # رقم العملية المسنود، حتى لو مكتوب بمسافات
        spans += [m.span() for m in re.finditer(r"(?<!\d)" + r"[ \-]?".join(d) + r"(?!\d)", t)]
    return spans


def _mask_non_amounts(text, txid=None):
    """النص بعد تبديل التواريخ وأرقام العمليات بمسافات (نفس الطول)، حتى ما تنحسب مبالغ."""
    t = (text or "").translate(ARABIC_DIGITS)
    for a, b in _non_amount_spans(t, txid):
        t = t[:a] + " " * (b - a) + t[b:]
    return t


def _bare_context(t, start, end):
    """سياق رقم مجرد: «nonmoney» أو «money» أو «unknown» (المعنى المالي مبهم)."""
    if _NONMONEY_AFTER_RE.match(t, end) or _NONMONEY_BEFORE_RE.search(t[:start]):
        return "nonmoney"
    words = lambda x: re.findall(r"[^\W\d_]+", _TASHKEEL_RE.sub("", x))
    near = words(t[:start])[-_MONEY_BEFORE:] + words(t[end:])[:_MONEY_AFTER]
    return "money" if any(_MONEY_CUE_RE.search(w) for w in near) else "unknown"


def _amount_mentions(text, txid=None, keep_small=False):
    """الأرقام اللي ممكن تكون مبالغ بالنص: [(النص، القيمة، السياق)].

    السياق: «shaped» (بيه ألف/مليون/دينار أو مكتوب بالحروف)، أو سياق الرقم المجرد من _bare_context.
    بدون التواريخ وأرقام العمليات. القيم برا مدى المبالغ تنشال، إلا keep_small للأرقام اللي ويا ألف/مليون/دينار
    (بالمسودة «5 دينار» أو «3 دينار» بدل «3 مليون» غلط واضح).
    """
    t = _mask_non_amounts(text, txid)
    out = []
    for m in _DECIMAL_SCALE_RE.finditer(t):
        scale = 1_000_000 if m.group(3) == "مليون" else 1000
        out.append((m.group(0), round(float(f"{m.group(1)}.{m.group(2)}") * scale), "shaped"))
        t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]
    for m in _AMOUNT_RE.finditer(t):
        ctx = "shaped" if (m.group(2) or m.group(3)) else _bare_context(t, m.start(), m.end())
        out.append((m.group(0).strip(), _amount_value(m), ctx))
    out += [(str(v), v, "shaped") for v in _word_numbers(t)]
    return [x for x in out if AMOUNT_MIN <= x[1] <= AMOUNT_MAX or (keep_small and x[2] == "shaped" and x[1] >= 1)]


def message_amounts(message, txid=None) -> set[int]:
    """قيم المبالغ المكتوبة بالرسالة بأي صيغة: «٧٥ الف»، «75,000 دينار»، «مية وخمسين الف»، «مليون ونص».

    الرقم المجرد اللي سياقه مو مالي («300 يوم»، «العملية 88221100») ما ينحسب.
    """
    return {v for _, v, ctx in _amount_mentions(message, txid) if ctx != "nonmoney"}


def message_amount_context(message, amount, txid=None):
    """شلون المبلغ مكتوب بالرسالة: «clear» (ويا ألف/دينار أو بالحروف أو سياق فلوس)، «unclear» (رقم مجرد
    سياقه مبهم)، أو None إذا مو مكتوب."""
    ctxs = {ctx for _, v, ctx in _amount_mentions(message, txid) if v == amount and ctx != "nonmoney"}
    if not ctxs:
        return None
    return "clear" if ctxs & {"shaped", "money"} else "unclear"


def _nonmoney_numbers(message, txid=None):
    """الأرقام (مثل ما مكتوبة) اللي الرسالة تستخدمها لغير الفلوس: تواريخ، أرقام عمليات، أيام، آخر أرقام البطاقة."""
    t = (message or "").translate(ARABIC_DIGITS)
    out = set()
    for a, b in _non_amount_spans(t, txid):
        span = t[a:b]
        out |= set(re.findall(r"\d+", span)) | {re.sub(r"\D", "", span)}
    for raw, value, ctx in _amount_mentions(t, txid):
        if ctx == "nonmoney":
            out.add(str(value))
    return out - {""}


def draft_unsupported_amounts(draft: str, message: str, txid=None):
    """مبالغ بالمسودة قيمتها مو مكتوبة بالرسالة كمبلغ. يرجع النصوص مثل ما انكتبت.

    الرقم ينقرا ويا وحدته: «٢٥٠ الف» بالرسالة = 250000، فـ«250» بالمسودة مو مسنود. الرقم المجرد يمر بس إذا
    سياقه بالمسودة مو مالي («خلال 300 يوم»، «العملية 88221100»)، أو إذا الرسالة تستخدم نفس الرقم لغير الفلوس
    (رقم عملية أو تاريخ) والمسودة ما تقدمه كمبلغ.
    """
    allowed = message_amounts(message, txid)
    nonmoney = _nonmoney_numbers(message, txid)
    out = []
    for raw, value, ctx in _amount_mentions(draft, txid, keep_small=True):
        if ctx == "nonmoney" or value in allowed:
            continue
        if ctx == "unknown" and str(value) in nonmoney:
            continue
        if raw not in out:
            out.append(raw)
    return out


def format_amounts_in_draft(draft: str, amount: int | None) -> str:
    """أي كتابة لنفس المبلغ بالمسودة (75000، 75,000 د.ع، ٧٥ الف دينار) تصير بالشكل الموحد.

    الأرقام الثانية (مثل رقم العملية) ما تتغير لأن قيمتها ما تساوي المبلغ.
    """
    if not draft or amount is None:
        return draft
    target = config.format_amount(amount)

    def repl(m):
        try:
            value = _amount_value(m)
        except ValueError:
            return m.group(0)
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
_ASK = (r"(?:زودنا|زوّدنا|تزويدنا|تزودنا|ترسل|ارسل|أرسل|إرسال|ارسال|دز|اكتب|شاركنا|مشارك|نحتاج|نطلب|ابعث|يرجى|نرجو|"
        r"ممكن\s+(?:ت|تنطي|تعطي)|انطينا|عطينا|ياريت|يا\s+ريت|تذكر|اذكر|تخبرنا|خبرنا)")
_NEG = r"(?:ما|لا|مو|أبداً|ابداً|ماراح|ما راح)\s+(?:\S+\s+){0,2}$"
_SENSITIVE = [r"رقم\s*الجهاز", r"IMEI", r"نسخة\s*(?:النظام|التطبيق)", r"الرمز\s*السري", r"الرقم\s*السري", r"\bPIN\b",
              r"رمز\s*التحقق", r"\bOTP\b", r"كلمة\s*(?:المرور|السر)", r"رقم\s*البطاقة\s*(?:الكامل|كامل)", r"CVV",
              r"تاريخ\s*انتهاء\s*البطاقة", r"الرموز\s*الخلفية"]
_CARD_DIGITS = r"(?:آخر|اخر|أخر)\s*(?:4|٤|أربع|اربع|اربعة|أربعة)\s*(?:أرقام|ارقام)|أرقام\s*البطاقة|ارقام\s*البطاقة|رقم\s*البطاقة"
_CARD_NOT_ARRIVED = re.compile(r"بطاق\w*.{0,60}?(?:ما\s*(?:وصل|وصلت|وصلتني|وصلني|استلم|استلمت|اجت|إجت)|لحد\s*الآن)|"
                               r"(?:ما\s*(?:وصل|وصلت|وصلتني|وصلني|استلمت|اجت))\s*(?:\S+\s+){0,3}بطاق", re.S)


def _requests(draft, pattern, after_ask=False):
    """الجمل اللي بيها فعل طلب غير منفي ومعلومة تطابق pattern.

    after_ask: المعلومة لازم تجي بعد فعل الطلب (حتى «بخصوص المبلغ 75,000… نحتاج رقم العملية» ما تنحسب طلب للمبلغ).
    """
    hits = []
    for sent in re.split(r"[.!؟?\n]", draft or ""):
        if not re.search(pattern, sent, re.I):
            continue
        asks = [m for m in re.finditer(_ASK, sent) if not re.search(_NEG, sent[: m.start()])]
        if any(not after_ask or re.search(pattern, sent[m.end():], re.I) for m in asks):
            hits.append(sent.strip())
    return hits


def sensitive_requests(draft: str):
    """المسودة تطلب رقم الجهاز أو رمز أو كلمة مرور أو رقم البطاقة الكامل."""
    return _requests(draft, "|".join(_SENSITIVE))


# معلومات ما تحتاجها هالنسخة أبداً: الهوية أو صورتها، رقم الهاتف، البريد الإلكتروني.
_NOT_NEEDED = [r"هوي(?:ة|ه|تك)", r"(?:ال)?بطاقة\s*(?:ال)?(?:وطنية|موحدة|السكن)", r"جواز", r"مستمسك", r"صورة\s*شخصية",
               r"سيلفي", r"رقم\s*(?:ال)?(?:هاتف|موبايل|تلفون|جوال)", r"رقمك", r"(?:ال)?بريد", r"[اإأ]يميل", r"e-?mail"]

# المعلومة مذكورة كطلب، مو كتأكيد قيمة («المبلغ 75,000» أو «بتاريخ 2026-10-04» مو طلب).
_STATED = r"(?!\s*(?:\S+\s+)?[0-9٠-٩])"
_FIELD_ASK = {
    "transaction_id": rf"رقم\s*(?:ال)?(?:عملية|معاملة|تحويل|تحويلة|حوالة){_STATED}",
    "amount": rf"(?:ال)?مبلغ{_STATED}|قيمة\s*(?:ال)?(?:تحويل|عملية|سحب|إيداع|ايداع){_STATED}|(?:شكد|كم)\s*(?:حولت|سحبت|دفعت)",
    "date": rf"(?:ال)?تاريخ{_STATED}|(?:وقت|يوم)\s*(?:ال)?(?:عملية|تحويل|سحب|إيداع|ايداع){_STATED}|[إا]يمتى|امتى|متى",
}


def not_needed_requests(draft: str):
    """المسودة تطلب هوية أو صورتها، أو رقم هاتف، أو بريد إلكتروني."""
    return _requests(draft, "|".join(_NOT_NEEDED))


def grounded_fields(fields: dict | None, message: str | None):
    """الحقول المستخرجة والمسنودة برسالة الزبون.

    رقم العملية والتاريخ يمرون من validate بس إذا إلهم أصل بالرسالة. المبلغ لازم قيمته تنذكر بالرسالة بأي كتابة.
    """
    fields = fields or {}
    out = []
    if fields.get("transaction_id"):
        out.append("transaction_id")
    if fields.get("amount") is not None and fields["amount"] in _message_number_values(message):
        out.append("amount")
    if fields.get("date") and has_time_reference(message or ""):
        out.append("date")
    return out


def known_info_requests(draft: str, fields: dict | None, message: str | None):
    """المسودة ترجع تطلب معلومة الزبون كتبها ومثبتة بالحقول. يرجع [(الحقل، الجملة)]."""
    return [(f, sent) for f in grounded_fields(fields, message) for sent in _requests(draft, _FIELD_ASK[f], after_ask=True)]


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
    """تفاصيل بالتبرير مو مذكورة بالرسالة: أرقام (بقيمتها، مو بشكل كتابتها)، أو ادعاء (خصم، نجاح، سرقة...) بلا أصل بالرسالة.

    تشابه التبرير ويا مثال مو خطأ بحد ذاته؛ الخطأ هو المعلومة غير المسندة.
    """
    reason, msg = (reason or "").translate(ARABIC_DIGITS), (message or "").translate(ARABIC_DIGITS)
    found = []
    # نفس المبلغ بأي كتابة مسنود («75000» بالتبرير و«٧٥ الف» أو «خمسة وسبعين الف» بالرسالة). أي رقم ثاني مو مسنود.
    msg_vals = _message_number_values(msg)
    for raw, value in number_values(reason):
        if value not in msg_vals and f"رقم: {raw}" not in found:
            found.append(f"رقم: {raw}")
    for claim, roots in _REASON_CLAIMS.items():
        if claim in reason and not any(r in msg for r in roots):
            found.append(claim)
    return found


def approval_blockers(draft: str, message: str | None = None, fields: dict | None = None) -> list[str]:
    """أسباب تمنع اعتماد المسودة (الأصلية أو المعدّلة أو المعاد كتابتها): عبارة ممنوعة، وعد أو ادعاء إجراء،
    طلب معلومة حساسة أو ما نحتاجها، أو طلب معلومة الزبون كتبها أصلاً (fields: الحقول المحفوظة للشكوى)،
    أو مبلغ قيمته مو مكتوبة بالرسالة.

    نفس القواعد اللي يستخدمها فحص المسودة بعد المودل (draft_review_reasons).
    """
    out = [f"عبارة ممنوعة: {p}" for p in check_draft(draft or "")]
    out += [f"وعد أو ادعاء إجراء: {p}" for p in promises_in_draft(draft, message)]
    out += [f"طلب معلومة حساسة: {s}" for s in sensitive_requests(draft)]
    out += [f"طلب معلومة ما نحتاجها: {s}" for s in not_needed_requests(draft)]
    out += [f"طلب {config.FIELD_LABELS[f]} وهو مذكور برسالة الزبون: {s}" for f, s in known_info_requests(draft, fields, message)]
    if message is not None:
        out += [f"مبلغ مو مذكور برسالة الزبون: {a}"
                for a in draft_unsupported_amounts(draft, message, (fields or {}).get("transaction_id"))]
    return out


def draft_review_reasons(draft, message, date_field, category, secondary_category=None, fields=None):
    """كل فحوصات المسودة بمكان واحد. يرجع أسباب المراجعة."""
    checks = [
        ("draft_forbidden_phrase", check_draft(draft)),
        ("draft_unsupported_date", draft_unsupported_dates(draft, message, date_field)),
        ("draft_unsupported_action", promises_in_draft(draft, message)),
        ("draft_sensitive_request", sensitive_requests(draft)),
        ("draft_not_needed_request", not_needed_requests(draft)),
        ("draft_known_info_request", known_info_requests(draft, fields, message)),
        ("draft_unsupported_amount", draft_unsupported_amounts(draft, message, (fields or {}).get("transaction_id"))),
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
    unauthorized = bool(x.get("unauthorized_operation"))
    money_issue = bool(x.get("money_issue"))
    security_risk = bool(x.get("security_risk")) or unauthorized  # التصريح بعملية غير مصرح بها = خطر أمني
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
    # المبلغ يخص أي مشكلة. مشكلة وحدة = الأساسية. قيمة غريبة = مو واضح.
    amount_issue = None
    if fields["amount"] is not None:
        amount_issue = x.get("amount_issue") if secondary else "primary"
        if amount_issue not in ("primary", "secondary", "unclear"):
            amount_issue = "unclear"

    if confidence < config.CONFIDENCE_THRESHOLD:
        reasons.insert(0, "low_confidence")
    reasons.extend(flags)
    if category == "other" and money_issue:
        reasons.append("money_issue_unclassified")  # شكوى مالية ما انحسم نوعها: الموظف يحددها
    if amount_issue == "secondary":
        reasons.append("amount_for_second_issue")  # الموظف يتأكد إن المسودة ما تنسبه للمشكلة الأساسية
    elif amount_issue == "unclear":
        reasons.append("amount_issue_unclear")
    if clipped:
        reasons.append("message_too_long")
    reason = (x.get("reason") or "").strip()
    if reason_unsupported(reason, message):
        reasons.append("reason_unsupported")

    missing = [f for f in config.REQUIRED_FIELDS[category] if fields[f] is None]
    out = dict(
        status="done",
        category=category,
        priority=config.priority_for(category, fields["amount"], security_risk, money_issue),
        department=config.department_for(category, unauthorized),
        transaction_id=fields["transaction_id"],
        amount=fields["amount"],
        date=fields["date"],
        security_risk=int(security_risk),
        confidence=confidence,
        secondary_category=secondary,
        money_issue=int(money_issue),
        unauthorized_operation=int(unauthorized),
        amount_issue=amount_issue,
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
        dsys, duser = prompts.draft_prompts(message, category, fields, missing, secondary,
                                            amount_owner({**out, "amount_issue": amount_issue}))
        dres = llm.call(dsys, duser, max_tokens=config.MAX_TOKENS_DRAFT, provider=provider, model=model,
                        on_request=_logger(calls, "draft"))
        draft = format_amounts_in_draft(dres.text.strip(), fields["amount"])
        reasons.extend(draft_review_reasons(draft, message, fields["date"], category, secondary, fields))
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


def amount_owner(r, category=None):
    """المشكلة اللي يخصها المبلغ المحفوظ، بالنسبة للفئة الحالية (category: تصحيح الموظف، الافتراضي فئة الموديل).

    يرجع مفتاح فئة، أو "unclear"، أو None إذا ماكو مبلغ. الحقول المستخرجة تخص الرسالة كلها، وممكن تخص المشكلة
    الثانية (مثلاً مبلغ إيداع ويا تحويل فاشل). المبلغ يبقى ويا نفس المشكلة حتى لو الموظف غيّر الفئة:
    - مبلغ الأساسية يتبع الفئة المصححة، إلا إذا الموظف خلّى المشكلة الثانية أساسية، فيبقى ويا المشكلة الأولى.
    - مبلغ الثانية يبقى للثانية.
    النتائج القديمة بدون amount_issue تنحسب للأساسية.
    """
    if r.get("amount") is None:
        return None
    cat = category or r["category"]
    sec = r.get("secondary_category")
    if r.get("amount_issue") == "unclear":
        return "unclear"
    if r.get("amount_issue") == "secondary" and sec:
        return sec
    if sec and cat == sec and cat != r["category"]:
        return r["category"]
    return cat


def routing_for(r, category):
    """الأولوية والقسم لشكوى محفوظة بفئة معينة (فئة الموديل أو تصحيح الموظف)، بنفس قواعد التوجيه كاملة:
    الخطر الأمني، العملية غير المصرح بها، المبلغ الكبير، والشكوى المالية.

    بنفس فئة الموديل نرجّع المحفوظ نفسه. النتائج القديمة بدون الحقلين الجديدين تنحسب False.
    """
    if category == r.get("category") and r.get("priority") and r.get("department"):
        return r["priority"], r["department"]
    unauthorized = bool(r.get("unauthorized_operation"))
    risk = bool(r.get("security_risk")) or unauthorized
    return (config.priority_for(category, r.get("amount"), risk, bool(r.get("money_issue"))),
            config.department_for(category, unauthorized))


def regenerate_draft(complaint_id, db_path=None):
    """يعيد كتابة المسودة فقط حسب النتيجة المحفوظة (مثلاً بعد ما الموظف صحح الفئة)."""
    with db.connect(db_path) as conn:
        c = db.get_complaint(conn, complaint_id)
        r = dict(conn.execute("SELECT * FROM results WHERE complaint_id = ?", (complaint_id,)).fetchone())
        v = conn.execute("SELECT corrected_category FROM reviews WHERE complaint_id = ?", (complaint_id,)).fetchone()
    category = (v and v[0]) or r["category"]
    secondary = r.get("secondary_category")
    if secondary == category:  # الموظف خلّى المشكلة الثانية هي الأساسية: الأولى تصير الثانية
        secondary = r["category"]
    fields = {"transaction_id": r["transaction_id"], "amount": r["amount"], "date": r["date"]}
    missing = [f for f in config.REQUIRED_FIELDS[category] if fields[f] is None]
    message, _ = clip(c["message"])
    dsys, duser = prompts.draft_prompts(message, category, fields, missing, secondary, amount_owner(r, category))
    calls = []
    try:
        res = llm.call(dsys, duser, max_tokens=config.MAX_TOKENS_DRAFT, on_request=_logger(calls, "redraft"))
    finally:
        with db.connect(db_path) as conn:
            db.log_calls(conn, [{**c, "complaint_id": complaint_id, "provider": config.PROVIDER, "model": config.MODEL}
                                for c in calls])
    draft = format_amounts_in_draft(res.text.strip(), r["amount"])
    return draft, approval_blockers(draft, c["message"], fields)
