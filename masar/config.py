"""الإعدادات الثابتة: الفئات، جدول التوجيه، وقواعد الأولوية.

القسم والأولوية يحددهم الكود من هذا الجدول، مو الموديل. الموديل يعطي الفئة
والتفاصيل وإشارة الخطر فقط، وهنا نطبق القواعد بشكل ثابت وقابل للفحص.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# local.nosync: iCloud ما يزامن أي مجلد اسمه ينتهي بـ .nosync. المشروع على سطح المكتب
# (متزامن ويا iCloud)، والمزامنة كانت تعطّل قراءة المكتبات وتخرب قاعدة SQLite.
LOCAL_DIR = ROOT / "local.nosync"
LOCAL_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("MASAR_DB", LOCAL_DIR / "masar.db"))
GUIDE_PATH = ROOT / "data" / "support_guide.md"

# الافتراضي: Groq، الخطة المجانية. بدائل: anthropic أو gemini (MASAR_PROVIDER و MASAR_MODEL).
PROVIDER = os.getenv("MASAR_PROVIDER", "groq")
MODEL = os.getenv("MASAR_MODEL", "openai/gpt-oss-120b")
# مستوى تفكير gpt-oss: low | medium | high. الأعلى يصرف توكنات أكثر من الحد اليومي المجاني.
REASONING_EFFORT = os.getenv("MASAR_REASONING_EFFORT", "low")
# الخطة المجانية حدها بالتوكنات بالدقيقة، فنعالج شكوى وحدة بكل مرة افتراضياً.
WORKERS = int(os.getenv("MASAR_WORKERS", "1"))
# إذا المزوّد طلب انتظار أطول من هذا (ثواني)، نوقف ونسجل الفشل بدال ما نعلّق.
MAX_RATE_LIMIT_WAIT = float(os.getenv("MASAR_MAX_RATE_LIMIT_WAIT", "90"))
# حد التوكنات للجواب (يشمل تفكير gpt-oss). Groq يحجز هالحد من حدود الدقيقة واليوم،
# فنخليه قريب من الحاجة الفعلية (الفهم ~450، المسودة ~300) مع هامش.
MAX_TOKENS_CLASSIFY = 1000
MAX_TOKENS_DRAFT = 800
CONFIDENCE_THRESHOLD = float(os.getenv("MASAR_CONFIDENCE_THRESHOLD", "0.75"))
PROMPT_VERSION = "v5"  # v5: الغموض «أخرى» ويا سبب، الوكيل لازم يبين بالنص، شكوى مالية وعملية غير مصرح بها، يوم بدون شهر
# v4: دليل وتعليمات مسودة أقصر، وحذف مثال التبرير اللي يتنسخ
# v3: السبب مسنود بالرسالة، والمسودة ما تدّعي إجراء ولا تطلب معلومات بلا حاجة
# v2: الأسئلة حسب موضوعها، وتاريخ الاستلام مو تاريخ المشكلة

MAX_MESSAGE_CHARS = 2000

# الفئات الست حسب الخطة
CATEGORIES = {
    "transfer_failed": "تعثر التحويل",
    "wrong_recipient": "المستلم الخطأ",
    "account_access": "الدخول للحساب",
    "agent_dispute": "خلاف مع الوكيل",
    "card": "البطاقة",
    "other": "أخرى",
}

# جدول التوجيه: فئة -> قسم
ROUTING = {
    "transfer_failed": "العمليات والتحويلات",
    "wrong_recipient": "النزاعات والاسترداد",
    "account_access": "أمن الحسابات",
    "agent_dispute": "شبكة الوكلاء",
    "card": "البطاقات",
    "other": "خدمة الزبائن العامة",
}

PRIORITIES = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}

HIGH_AMOUNT_IQD = 1_000_000

# المعلومات اللي يحتاجها القسم حتى يتابع، لكل فئة
REQUIRED_FIELDS = {
    "transfer_failed": ["transaction_id", "amount", "date"],
    "wrong_recipient": ["transaction_id", "amount", "date"],
    "account_access": [],
    "agent_dispute": ["amount", "date"],
    "card": [],
    "other": [],
}

FIELD_LABELS = {
    "transaction_id": "رقم العملية",
    "amount": "المبلغ",
    "date": "التاريخ",
}

REVIEW_REASON_LABELS = {
    "low_confidence": "ثقة الموديل منخفضة",
    "multiple_issues": "الرسالة بيها أكثر من مشكلة",
    "unclear_message": "الرسالة مو واضحة",
    "similar_names": "أسماء أو أرقام متشابهة ممكن تلخبط",
    "possible_injection": "الرسالة تحاول تغيّر تعليمات النظام",
    "invalid_transaction_id": "رقم العملية المستخرج مو موجود بنص الرسالة",
    "invalid_amount": "المبلغ المستخرج شكله غلط",
    "invalid_date": "التاريخ المستخرج شكله غلط",
    "unsupported_date": "الموديل طلّع تاريخ والرسالة ما بيها أي تاريخ أو وقت (انشال التاريخ)",
    "ambiguous_date": "اليوم مذكور بدون شهر وهو بعد يوم الاستلام، فالشهر مو محسوم (التاريخ يبقى فارغ)",
    "money_issue_unclassified": "شكوى مالية ونوع العملية مو واضح (سحب، إيداع، تحويل، بطاقة)",
    "amount_for_second_issue": "المبلغ يخص المشكلة الثانية بالرسالة، مو الأساسية (تأكد إن المسودة تربطه بيها)",
    "amount_issue_unclear": "مو واضح المبلغ يخص أي مشكلة بالرسالة",
    "draft_unsupported_date": "المسودة بيها تاريخ مو مذكور بالرسالة",
    "draft_forbidden_phrase": "المسودة بيها وعد أو عبارة ممنوعة",
    "message_too_long": "الرسالة طويلة وانقطعت",
    "reason_unsupported": "تبرير الموديل بيه تفصيل مو مذكور بالرسالة",
    "draft_unsupported_action": "المسودة توعد بإحالة أو متابعة أو رد، أو تدّعي إجراء ما انعمل",
    "draft_sensitive_request": "المسودة تطلب معلومة حساسة أو ما إلها حاجة",
    "draft_irrelevant_request": "المسودة تطلب معلومة ما تناسب الحالة",
    "draft_not_needed_request": "المسودة تطلب هوية أو صورتها أو رقم هاتف أو بريد، وهالنسخة ما تحتاجها",
    "draft_known_info_request": "المسودة تطلب معلومة الزبون كتبها أصلاً برسالته",
    "draft_missing_issue": "الرسالة بيها أكثر من مشكلة والمسودة ما ذكرت الثانية",
    "draft_too_long": "المسودة أطول من اللازم",
}

# كلمات تدل إن المسودة ذكرت مشكلة من هالفئة (لفحص المشكلة الثانية بالرسائل المتعددة)
CATEGORY_KEYWORDS = {
    "transfer_failed": ["تحويل", "التحويل", "حوالة", "الحوالة", "حولت"],
    "wrong_recipient": ["المستلم", "مستلم", "رقم غلط", "الرقم الغلط", "بالغلط"],
    "account_access": ["الدخول", "دخول", "رمز التحقق", "الرمز", "حسابك", "الحساب"],
    "agent_dispute": ["الوكيل", "وكيل"],
    "card": ["البطاقة", "بطاقة", "بطاقتك"],
    "other": [],
}

# المسودة المطلوبة 2-4 جمل؛ أطول من هذا تنحوّل للمراجعة
MAX_DRAFT_CHARS = 500


def format_amount(amount: int | None) -> str | None:
    """الشكل الموحد للمبلغ بالمسودات والعرض: 75,000 دينار عراقي."""
    return f"{amount:,} دينار عراقي" if amount is not None else None


def priority_for(category: str, amount: int | None, security_risk: bool, money_issue: bool = False) -> str:
    """قواعد الأولوية. نفس الدالة تستخدم لحساب الإجابة الصحيحة بالبيانات.

    money_issue: شكوى عن فلوس الزبون (خصم، سحب، إيداع، تحويل، فلوس يريد يرجعها) حتى لو نوع العملية مو واضح.
    «أخرى» منخفضة بس إذا ما بيها فلوس؛ الشكوى المالية الغامضة متوسطة كحد أدنى.
    """
    if security_risk:
        return "high"
    if amount is not None and amount >= HIGH_AMOUNT_IQD:
        return "high"
    if category == "wrong_recipient":
        return "high"  # الفلوس عند شخص ثاني، كل ما نتأخر يصعب الاسترداد
    if category == "other":
        return "medium" if money_issue else "low"
    return "medium"


def department_for(category: str, unauthorized: bool = False) -> str:
    """جدول التوجيه، ويا قاعدة وحدة فوقه: عملية يصرّح الزبون إنها مو مأذونة («مو آني سويتها»، «بدون علمي»)
    تروح لأمن الحسابات مهما كانت الفئة (وأولويتها عالية لأنها خطر أمني). نسخة البيانات 6."""
    if unauthorized:
        return ROUTING["account_access"]
    return ROUTING[category]
