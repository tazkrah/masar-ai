"""فحص ملف CSV وإعطاء كل شكوى معرّف ثابت."""
import csv
import hashlib
import io
import re
import unicodedata

from . import config

MESSAGE_COLUMNS = ("message", "text", "الرسالة", "الشكوى")
ID_COLUMNS = ("complaint_id", "id", "رقم_الشكوى")
DATE_COLUMNS = ("received_at", "date_received", "تاريخ_الاستلام")
CHANNEL_COLUMNS = ("channel", "القناة")

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class CSVError(ValueError):
    pass


def normalize_text(s: str) -> str:
    """توحيد بسيط للمقارنة فقط (مو للعرض): مسافات، أرقام، تطويل."""
    s = unicodedata.normalize("NFKC", s).translate(ARABIC_DIGITS)
    s = s.replace("ـ", "")  # ـ
    return re.sub(r"\s+", " ", s).strip()


def stable_id(message: str, source_id: str | None, received_at: str | None) -> str:
    """إذا الملف بيه رقم شكوى نستخدمه. إذا لا، بصمة من النص ووقت الاستلام."""
    if source_id:
        key = "src:" + source_id.strip()
    else:
        key = "txt:" + normalize_text(message) + "|" + (received_at or "").strip()
    return "C-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10].upper()


def _pick(header, options):
    lowered = {h.strip().lower(): h for h in header}
    for o in options:
        if o.lower() in lowered:
            return lowered[o.lower()]
    return None


def parse_csv(data: bytes):
    """يرجع (rows, skipped). يرمي CSVError إذا الملف كله ما ينقرى."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1256")  # ملفات إكسل العربية القديمة
        except UnicodeDecodeError as e:
            raise CSVError("ترميز الملف مو مفهوم. احفظه بصيغة CSV UTF-8 وجرّب مرة ثانية.") from e

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CSVError("الملف فارغ أو ما بيه عناوين أعمدة.")
    msg_col = _pick(reader.fieldnames, MESSAGE_COLUMNS)
    if not msg_col:
        raise CSVError(
            "ما لگينا عمود الرسالة. لازم يكون اسمه واحد من: " + "، ".join(MESSAGE_COLUMNS)
        )
    id_col = _pick(reader.fieldnames, ID_COLUMNS)
    date_col = _pick(reader.fieldnames, DATE_COLUMNS)
    chan_col = _pick(reader.fieldnames, CHANNEL_COLUMNS)

    rows, skipped, seen = [], [], set()
    for i, rec in enumerate(reader, start=2):  # السطر 1 هو العناوين
        message = (rec.get(msg_col) or "").strip()
        source_id = (rec.get(id_col) or "").strip() if id_col else None
        received_at = (rec.get(date_col) or "").strip() if date_col else None
        if not message:
            skipped.append({"row": i, "reason": "رسالة فارغة"})
            continue
        cid = stable_id(message, source_id or None, received_at)
        if cid in seen:
            skipped.append({"row": i, "reason": "مكررة بنفس الملف"})
            continue
        seen.add(cid)
        rows.append({
            "id": cid,
            "source_id": source_id or None,
            "message": message,
            "received_at": received_at or None,
            "channel": (rec.get(chan_col) or "").strip() or None if chan_col else None,
            "row_number": i,
        })
    if not rows:
        raise CSVError("ما بالملف ولا شكوى صالحة.")
    return rows, skipped


def clip(message: str) -> tuple[str, bool]:
    if len(message) <= config.MAX_MESSAGE_CHARS:
        return message, False
    return message[: config.MAX_MESSAGE_CHARS], True
