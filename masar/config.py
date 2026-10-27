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
# حد التوكنات للجواب. gpt-oss يفكر قبل ما يجاوب، فنخلي مجال كافي.
MAX_TOKENS_CLASSIFY = 2500
MAX_TOKENS_DRAFT = 2000
CONFIDENCE_THRESHOLD = float(os.getenv("MASAR_CONFIDENCE_THRESHOLD", "0.75"))
PROMPT_VERSION = "v1"

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
    "draft_forbidden_phrase": "المسودة بيها وعد أو عبارة ممنوعة",
    "message_too_long": "الرسالة طويلة وانقطعت",
}


def format_amount(amount: int | None) -> str | None:
    """الشكل الموحد للمبلغ بالمسودات والعرض: 75,000 دينار عراقي."""
    return f"{amount:,} دينار عراقي" if amount is not None else None


def priority_for(category: str, amount: int | None, security_risk: bool) -> str:
    """قواعد الأولوية. نفس الدالة تستخدم لحساب الإجابة الصحيحة بالبيانات."""
    if security_risk:
        return "high"
    if amount is not None and amount >= HIGH_AMOUNT_IQD:
        return "high"
    if category == "wrong_recipient":
        return "high"  # الفلوس عند شخص ثاني، كل ما نتأخر يصعب الاسترداد
    if category == "other":
        return "low"
    return "medium"
