"""تصحيحات الإجابات الصحيحة لرسائل محددة بعد المراجعة اليدوية (نسخ البيانات 5 و 6، مجموعة التطوير فقط).

القرار: الإجابة الصحيحة تستند لنص الرسالة ومعناها، مو لتفاصيل السيناريو اللي ما ظهرت بالرسالة.
كل تصحيح يحدد القيم الجديدة وسببه. الأولوية والقسم ما ينكتبون هنا: ينحسبون بـ config.priority_for و
config.department_for من الفئة والمبلغ والخطر و money_issue و unauthorized، حتى يبقون نفس قواعد النظام.

expect: الفئة القديمة المتوقعة، حتى التصحيح ما ينطبق على رسالة غلط إذا تغير الملف.
نصوص الرسائل والتقسيم ما تتغير. مجموعتي الضبط والاختبار ما انفتحت ولا تنطبق عليها هالتصحيحات.
"""

DATA_VERSION = 6

# أعمدة جديدة بملف الإجابات لكل نسخة. القيمة الافتراضية للرسائل غير المصححة (دالة = تنحسب من الإجابة).
NEW_COLUMNS = {
    5: {"accepted_swap": "0", "review_note": ""},
    # amount_category: المشكلة اللي يخصها المبلغ. الافتراضي: الفئة الأساسية إذا اكو مبلغ.
    6: {"amount_category": lambda l: l["category"] if l.get("amount") else ""},
}


def column_defaults(label, upto=DATA_VERSION):
    out = {}
    for v in sorted(NEW_COLUMNS):
        if v <= upto:
            for k, d in NEW_COLUMNS[v].items():
                out[k] = d({**label, **out}) if callable(d) else d
    return out

_UNCLEAR = {"category": "other", "secondary_category": "", "expect_review": 1, "hard_type": "unclear_message"}


def _unclear(expect, note, money=True, **extra):
    return {"expect": expect, "set": {**_UNCLEAR, "review_note": note, **extra}, "money_issue": money,
            "reason": "النص ما يكفي لتحديد الفئة: " + note}


_DATE_NOTE = "اليوم مذكور بدون شهر وهو بعد يوم الاستلام، فالشهر مو محسوم"


def _date(expect):
    return {"expect": expect, "set": {"date": "", "expect_review": 1, "hard_type": "ambiguous_date",
                                      "review_note": _DATE_NOTE},
            "reason": _DATE_NOTE + ". ما نفترض الشهر السابق."}


_DEPOSIT_MIXED = {
    "expect": "agent_dispute",
    "set": {"category": "transfer_failed", "secondary_category": "other", "expect_review": 1,
            "hard_type": "multiple_issues",
            "review_note": "التحويل الفاشل واضح؛ الإيداع ما نزل بدون ذكر وكيل أو محل، فنوعه غامض"},
    "reason": "الإيداع ما يبين إنه عند وكيل، فما نثبت «خلاف مع الوكيل». التحويل الفاشل واضح فهو الأساسي، "
              "والإيداع مشكلة ثانية غامضة (أخرى) ويا مراجعة.",
}

_SWAP = {
    "expect": "agent_dispute",
    "set": {"accepted_swap": "1"},
    "reason": "المشكلتين واضحة بالنص (إيداع عند الوكيل ما نزل + تحويل فشل) وبالاثنين فلوس، فقاعدة «الأهم = اللي بيها فلوس "
              "أو خطر أكثر» ما تحسم الترتيب. نقبل agent_dispute أو transfer_failed كأساسية.",
}

DEV_V5 = {
    # سحب أو عمولة سحب بدون ذكر وكيل أو صراف
    "DEV-G02-2": _unclear("agent_dispute", "عمولة على سحب بدون ذكر وين صار السحب (وكيل أو صراف)"),
    "DEV-G03-3": _unclear("agent_dispute", "سحب ما وصل كاشه بدون ذكر وكيل أو صراف"),
    "DEV-G03-4": _unclear("agent_dispute", "سحب ما وصل كاشه بدون ذكر وكيل أو صراف"),
    "DEV-G02-4": _unclear("agent_dispute", "عملية ما نجحت بدون ذكر نوعها ولا وين صارت"),
    # إيداع بدون ذكر وكيل
    "DEV-G01-2": _unclear("agent_dispute", "إيداع ما نزل بدون ذكر وين انعمل (وكيل أو غيره)"),
    "DEV-G01-4": _unclear("agent_dispute", "إيداع ما نزل بدون ذكر وين انعمل (وكيل أو غيره)"),
    # خصم مكرر أو مجهول بدون نوع العملية. عدم فهم الخصم لحاله مو خطر أمني.
    "DEV-T04-2": _unclear("transfer_failed", "خصم مكرر بدون ذكر نوع العملية (تحويل، شراء، سحب)"),
    "DEV-T04-3": _unclear("transfer_failed", "خصم مكرر بدون ذكر نوع العملية (تحويل، شراء، سحب)"),
    "DEV-T04-4": _unclear("transfer_failed", "خصم مكرر بدون ذكر نوع العملية (تحويل، شراء، سحب)"),
    "DEV-T04-5": _unclear("transfer_failed", "خصم مكرر بدون ذكر نوع العملية (تحويل، شراء، سحب)"),
    "DEV-K08-4": _unclear("card", "خصم ما يعرف سببه بدون ذكر بطاقة أو رسوم أو نوع العملية"),
    # تحويل مذكور بدون المشكلة
    "DEV-T08-2": _unclear("transfer_failed", "يذكر حوالة ورقمها ويطلب مراجعة بدون ما يگول شنو المشكلة"),
    "DEV-W08-3": _unclear("wrong_recipient", "يذكر تحويل ورقمه ومستعجل بدون ما يگول شنو المشكلة"),
    "DEV-W08-4": _unclear("wrong_recipient", "يذكر تحويل ورقمه ومستعجل بدون ما يگول شنو المشكلة"),
    "DEV-W05-1": _unclear("wrong_recipient", "يطلب رجوع فلوس تحويل بدون ذكر شنو صار (فشل أو مستلم غلط)"),
    "DEV-W05-5": _unclear("wrong_recipient", "يطلب رجوع فلوس بدون أي تفصيل عن العملية"),
    # عملية يصرّح إنه ما سواها: خطر أمني وأمن الحسابات، والفئة غامضة لأن ما يبين شلون صارت
    "DEV-A08-1": _unclear("account_access", "سحب يصرّح إنه مو هو سواه، بدون ذكر شلون صار (دخول للحساب أو بطاقة)",
                          unauthorized=True, security_risk=1),
    "DEV-A08-4": _unclear("account_access", "سحب يصرّح إنه صار بدون علمه، بدون ذكر شلون صار (دخول للحساب أو بطاقة)",
                          unauthorized=True, security_risk=1),
    # شكوى مالية غامضة كانت «أخرى» منخفضة: الفئة تبقى، الأولوية متوسطة كحد أدنى
    "DEV-A10-1": {"expect": "other", "set": {"review_note": "رصيده ما يطابق التحويلات بدون تحديد عملية"},
                  "money_issue": True,
                  "reason": "شكوى مالية غامضة (الرصيد ما يطابق التحويلات): أولوية متوسطة كحد أدنى بدل منخفضة."},
    # النص يگول التحويل ما وصل، ماكو ذكر لمستلم غلط
    "DEV-W08-2": {"expect": "wrong_recipient", "set": {"category": "transfer_failed"},
                  "reason": "النص يگول «التحويل ما وصل» وما يذكر رقم أو حساب غلط: تعثر تحويل، والأولوية عالية بسبب المبلغ."},
    # إيداع بدون وكيل + تحويل فاشل
    "DEV-G08-1": _DEPOSIT_MIXED,
    "DEV-G08-3": _DEPOSIT_MIXED,
    "DEV-G08-5": _DEPOSIT_MIXED,
    # إيداع عند الوكيل + تحويل فاشل: الترتيبين مقبولين
    "DEV-G08-2": _SWAP,
    "DEV-G08-4": _SWAP,
    # يوم بدون شهر بعد يوم الاستلام
    "DEV-G07-3": _date("agent_dispute"),
    "DEV-K04-1": _date("card"),
    "DEV-T08-5": _date("transfer_failed"),
    "DEV-W01-4": _date("wrong_recipient"),
    "DEV-W07-3": _date("wrong_recipient"),
}


# نسخة 6: العملية غير المصرح بها تروح لأمن الحسابات مهما كانت الفئة، والمبلغ مرتبط بمشكلته.
DEV_V6 = {
    "DEV-K04-4": {"expect": "card", "set": {"unauthorized": True},
                  "reason": "يصرّح إن أحد حاول يسحب من حسابه (عملية مو مأذونة) ويا ضياع البطاقة: حسب قاعدة النسخة 6 "
                            "القسم أمن الحسابات والأولوية عالية، والفئة تبقى البطاقة."},
    **{cid: {"expect": "transfer_failed", "set": {"amount_category": "other"},
             "reason": "المبلغ بالرسالة مبلغ الإيداع (المشكلة الثانية الغامضة)، مو مبلغ التحويل الفاشل."}
       for cid in ("DEV-G08-1", "DEV-G08-3", "DEV-G08-5")},
}

VERSIONS = {5: DEV_V5, 6: DEV_V6}


def corrections(upto=DATA_VERSION):
    """كل التصحيحات لحد نسخة معينة، مدموجة لكل رسالة (الأحدث يكمّل الأقدم). expect من أول تصحيح."""
    out = {}
    for v in sorted(VERSIONS):
        if v > upto:
            break
        for cid, c in VERSIONS[v].items():
            if cid not in out:
                out[cid] = {"expect": c["expect"], "set": {}, "reason": c["reason"]}
            out[cid]["set"].update(c["set"])
            if "money_issue" in c:
                out[cid]["money_issue"] = c["money_issue"]
    return out


def unauthorized_ids(upto=DATA_VERSION):
    """رسائل التطوير اللي الزبون يصرّح بيها بعملية مو مأذونة، حسب المراجعة اليدوية."""
    return {cid for cid, c in corrections(upto).items() if c["set"].get("unauthorized")} | UNAUTHORIZED_NO_CHANGE


# تصريح بعملية مو مأذونة، وفئتها وقسمها أصلاً أمن الحسابات، فما احتاجت تصحيح.
UNAUTHORIZED_NO_CHANGE = {"DEV-A08-2", "DEV-A08-3", "DEV-A08-5"}


def apply(label: dict, corr: dict, config, upto=DATA_VERSION) -> dict:
    """يرجع الإجابة بعد التصحيح. الأولوية والقسم من قواعد النظام. corr: تصحيح مدموج (corrections())."""
    out = dict(label)
    for k, v in corr["set"].items():
        if k not in ("unauthorized",):
            out[k] = str(v)
    out = {**column_defaults(out, upto), **out}  # الافتراضي من الإجابة بعد التصحيح (مثلاً الفئة الجديدة)
    unauthorized = bool(corr["set"].get("unauthorized"))
    if unauthorized:
        out["security_risk"] = "1"  # التصريح بعملية مو مأذونة = خطر أمني
    amount = int(out["amount"]) if out["amount"] not in ("", None) else None
    out["priority"] = config.priority_for(out["category"], amount, out["security_risk"] in ("1", 1),
                                          corr.get("money_issue", False))
    out["department"] = config.department_for(out["category"], unauthorized)
    return out
