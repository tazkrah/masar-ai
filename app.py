"""شاشة الموظف — مسار.

تشغيل:  ./run.sh
"""
import html
import math
import re
from collections import Counter
from datetime import datetime, timedelta

import streamlit as st

from masar import config, db, ingest, llm, pipeline

st.set_page_config(page_title="مسار — شكاوى الزبائن", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")
db.init()

# اتجاه الصفحة كلها من اليمين، حتى العناصر اللي Streamlit يرسمها خارج التطبيق (القوائم المنسدلة، النوافذ).
st.html("<script>document.documentElement.setAttribute('dir','rtl');document.documentElement.setAttribute('lang','ar');</script>",
        unsafe_allow_javascript=True)

# الصفحة نفسها ما تتمرر: القائمة ومحتوى التفاصيل يتمررون كل واحد بروحه، وأزرار القرار ثابتة تحت.
CSS = """
<link href="https://fonts.googleapis.com/css2?family=Almarai:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root { --teal:#0E7C86; --teal-soft:#E7F2F3; --ink:#1C2530; --muted:#5B6773; --line:#E1E7E8; --soft:#F2F6F6;
        --amber:#9A6200; --amber-bg:#FBF4E6; --amber-line:#EED9AE; --red:#B42318; --red-bg:#FCEEEC; --red-line:#F2CBC6;
        --r: 6px; --list-h: calc(100vh - 200px); --body-h: calc(100vh - 210px); }
html, body, .stApp, .stMarkdown, button, input, textarea, select, label, [data-testid="stWidgetLabel"] p {
  font-family: 'Almarai', sans-serif !important; }
.stApp, .stApp *, [data-baseweb="popover"], [data-baseweb="popover"] *, [data-testid="stDialog"] * { direction: rtl; }
.stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, [data-baseweb="popover"] p, [data-testid="stDialog"] p { text-align: right; }
bdi { direction: ltr; unicode-bidi: isolate; }
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stToolbar"] { display: none; }
.block-container { padding: .8rem 1.5rem .6rem; max-width: 1400px; }
[data-testid="stElementContainer"]:has(> .stHtml), [data-testid="stElementContainer"]:has(style) { display: none; }
p, li { margin-bottom: 0; }
.stMarkdown [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
.stButton button, [data-testid="stPopover"] button { border-radius: var(--r); font-weight: 700; min-height: 2.25rem; padding: .3rem .9rem; }
/* الشريط العلوي */
.st-key-topbar { border-bottom: 1px solid var(--line); padding-bottom: .6rem; margin-bottom: .2rem; }
.brand { display:flex; align-items:baseline; gap:.6rem; white-space: nowrap; }
.brand .name { color: var(--teal); font-size: 1.55rem; font-weight: 800; line-height: 1; }
.brand .sum { color: var(--muted); font-size: .85rem; }
.brand .sum b { color: var(--ink); }
.st-key-status button { border-color: var(--line); background: #fff; width: 100%; justify-content: flex-start; }
.st-key-status button p { font-size: .86rem; }
.pop-title { font-weight: 800; color: var(--ink); margin-bottom: .3rem; }
.pop-text { font-size: .88rem; color: var(--ink); line-height: 1.75; }
.pop-text .muted, .pop-text.muted { color: var(--muted); }
/* القائمة */
.st-key-w_flt [data-testid="stButtonGroup"] > div { flex-wrap: wrap; overflow: visible; gap: .35rem; padding: 1px; }
.st-key-w_flt button { border-radius: 999px; padding: .15rem .7rem; min-height: 0; white-space: nowrap; }
.st-key-w_flt button p { font-size: .82rem; }
/* ارتفاع القائمة ومحتوى التفاصيل حسب الشاشة، حتى أزرار القرار تبقى ظاهرة بدون تمرير الصفحة */
.st-key-list { height: var(--list-h); overflow-y: auto; flex: 0 0 auto; }
[class*="st-key-dbody-"] { height: var(--body-h); overflow-y: auto; flex: 0 0 auto; }
.st-key-list > *, [class*="st-key-dbody-"] > * { flex-shrink: 0; }  /* المحتوى يتمرر، ما ينضغط فوق بعضه */
.st-key-list { border: 1px solid var(--line); border-radius: var(--r); }
.st-key-list [data-testid="stVerticalBlock"] { gap: 0; }
[class*="st-key-row-"] button { justify-content: flex-start; width: 100%; min-height: 0; border: none; border-radius: 0;
  border-bottom: 1px solid var(--line); padding: .5rem .8rem; background: #fff; color: var(--ink); font-weight: 400; }
[class*="st-key-row-"] button:hover { background: var(--soft); color: var(--ink); }
[class*="st-key-row-"] button:focus:not(:active) { border-color: var(--line); color: var(--ink); }
[class*="st-key-row-"] button p { font-size: .87rem; line-height: 1.6; text-align: right; }
[class*="st-key-row-"] button p strong { font-size: .78rem; }
[class*="st-key-row-sel-"] button, [class*="st-key-row-sel-"] button:hover { background: var(--teal-soft); box-shadow: inset -3px 0 0 var(--teal); }
.st-key-pager { padding-top: .35rem; }
.st-key-pager button { min-height: 0; padding: .1rem .6rem; }
.pager { text-align:center; font-size:.82rem; color: var(--muted); }
.empty { color: var(--muted); font-size: .9rem; padding: 1.2rem .8rem; }
/* التفاصيل: ترويسة، محتوى يتمرر، أزرار ثابتة */
.st-key-detail { border: 1px solid var(--line); border-radius: var(--r); gap: 0; }
.st-key-dhead { padding: .7rem 1.1rem .6rem; border-bottom: 1px solid var(--line); }
[class*="st-key-dbody-"] { padding: .2rem 1.1rem .8rem; }
.st-key-dfoot { padding: .6rem 1.1rem; border-top: 1px solid var(--line); background: #fff; border-radius: 0 0 var(--r) var(--r); }
.d-head { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap; }
.d-id { font-size: 1.15rem; font-weight: 800; color: var(--ink); }
.d-meta { color: var(--muted); font-size: .84rem; }
.st { font-size: .85rem; font-weight: 700; white-space: nowrap; }
.st::before { content:""; display:inline-block; width:7px; height:7px; border-radius:50%; margin-left:.4rem; background: currentColor; }
.st.flag { color: var(--amber); } .st.review { color: var(--teal); } .st.failed { color: var(--red); }
.st.pending, .st.followup, .st.waiting { color: var(--muted); } .st.approved { color: var(--teal); }
.st small { font-weight: 400; color: var(--muted); font-size: .8rem; }
.line { margin-top:.7rem; font-size:.87rem; border-radius: var(--r); padding:.4rem .75rem; border:1px solid; }
.line.flag { color:#4A3500; background: var(--amber-bg); border-color: var(--amber-line); }
.line.failed { color:#5C150F; background: var(--red-bg); border-color: var(--red-line); }
.line.quiet { color: var(--muted); border-color: var(--line); }
.sec { font-size: .78rem; color: var(--muted); font-weight: 700; margin: .9rem 0 .35rem; }
.msg { background: var(--soft); border-right: 3px solid var(--teal); padding: .6rem .9rem; border-radius: var(--r);
       font-size: 1rem; line-height: 1.8; color: var(--ink); white-space: pre-wrap; }
.facts { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .5rem 1rem; font-size: .9rem; }
.facts .k { color: var(--muted); font-size: .76rem; display:block; }
.facts .v { color: var(--ink); font-weight: 700; }
.facts .none { color: var(--muted); font-weight: 400; }
.facts .high { color: var(--red); }
.why { color: var(--muted); font-size: .85rem; margin-top: .5rem; line-height: 1.65; }
.unsaved { color: var(--amber); font-size: .8rem; margin-top: .25rem; }
.foot-msg { font-size: .85rem; color: var(--muted); line-height: 1.5; }
.foot-msg.ok { color: var(--teal); font-weight: 700; }
.foot-msg.err { color: var(--red); font-weight: 700; }
[data-testid="stExpander"] { margin-top: .6rem; }
[data-testid="stExpander"] details { border-radius: var(--r); }
[data-testid="stExpander"] summary p { font-size: .84rem; color: var(--muted); }
[data-testid="stExpander"] summary svg, [data-testid="stExpander"] summary [data-testid="stIconMaterial"] { transform: scaleX(-1); }
textarea { line-height: 1.75 !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

CAT = config.CATEGORIES
PRI = config.PRIORITIES
REASONS = config.REVIEW_REASON_LABELS
MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
          "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]
PAGE_SIZE = 25

# حالة كل شكوى بالشاشة، بترتيب العرض بالقائمة.
# "waiting" = فشلت لأن خدمة الموديل نفسها متوقفة (حد الاستخدام، المفتاح، الاتصال)، مو بسبب الشكوى.
STATES = {
    "flag": "تحتاج مراجعة",
    "review": "جاهزة للمراجعة",
    "failed": "فيها خطأ",
    "waiting": "تنتظر رجوع المعالجة",
    "pending": "بانتظار المعالجة",
    "followup": "للمتابعة",
    "approved": "معتمدة",
}
STATE_MD = {"flag": "orange", "review": "primary", "failed": "red", "waiting": "gray", "pending": "gray",
            "followup": "gray", "approved": "primary"}
FILTERS = {
    "todo": ("للمراجعة", ("flag", "review")),
    "followup": ("للمتابعة", ("followup",)),
    "approved": ("معتمدة", ("approved",)),
    "failed": ("فيها خطأ", ("failed",)),
    "waiting": ("لم تُعالج", ("pending", "waiting")),
    "all": ("الكل", tuple(STATES)),
}
ORDER = list(STATES)
PRI_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}
# أخطاء تخص خدمة الموديل كلها، مو شكوى معيّنة (نصوصها من masar/llm.py)
SERVICE_ERRORS = ("حد الاستخدام", "مفتاح", "المفتاح مرفوض", "(401)", "(403)", "(404)", "مزوّد غير معروف", "فشل بعد")


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def md_esc(s):
    return re.sub(r"([\\`*_\[\]#<>|~:$])", r"\\\1", s)


def fmt_time(s, with_day=True):
    """2026-10-09 22:40 أو ISO بتوقيت UTC -> 9 تشرين الأول، 22:40 بالتوقيت المحلي."""
    if not s:
        return ""
    try:
        d = datetime.fromisoformat(s) if isinstance(s, str) else s
    except ValueError:
        return s
    if d.tzinfo:
        d = d.astimezone()
    return f"{d.day} {MONTHS[d.month - 1]}، {d:%H:%M}" if with_day else f"{d:%H:%M}"


def short_error(e):
    """أول جملة من رسالة الخطأ، بدون تفاصيل المزوّد ولا وقت الانتظار (يصير قديم بسرعة)."""
    first = (e or "سبب غير معروف").split(" (")[0].split(". ")[0].strip()
    return first if first.endswith((".", "؟")) else first + "."


def is_service_error(e):
    return any(p in (e or "") for p in SERVICE_ERRORS)


def retry_at(r):
    """إذا المزوّد ذكر وقت انتظار، نحسب موعد المحاولة من وقت آخر محاولة."""
    m = re.search(r"بعد (\d+) دقيقة", r["error"] or "")
    if not m or not r["updated_at"]:
        return None
    return datetime.fromisoformat(r["updated_at"]) + timedelta(minutes=int(m.group(1)))


def state(r):
    if r["status"] == "pending":
        return "pending"
    if r["status"] == "failed":
        return "waiting" if is_service_error(r["error"]) else "failed"
    if r["decision"] == "approved":
        return "approved"
    if r["decision"] == "needs_followup":
        return "followup"
    return "flag" if r["needs_review"] else "review"


def sort_key(r):
    return ORDER.index(r["state"]), PRI_ORDER[r["priority"]], r["row_number"]


def label_id(r):
    return r["source_id"] or r["complaint_id"]


ss = st.session_state
# البحث والفلتر والصفحة والاختيار محفوظة بمفاتيح خارج الحقول، حتى ما تضيع بين التحديثات.
ss.setdefault("q", "")
ss.setdefault("flt", None)
ss.setdefault("page", 0)
ss.setdefault("sel", None)
# تعديلات الموظف غير المحفوظة لكل شكوى: {cid: {"draft", "cat", "note"}}. تبقى لو انتقل لشكوى ثانية ورجع.
ss.setdefault("edits", {})

connected = llm.key_status()[1]
key_env = llm.key_status()[0]

with db.connect() as conn:
    batches = db.list_batches(conn)


def note_once(text, kind="ok"):
    """رسالة قصيرة تظهر مرة وحدة بمكان ثابت (جنب أزرار القرار)."""
    ss["note_once"] = (text, kind)


def select(cid):
    ss["sel"] = cid


def set_batch():
    ss.update(batch=ss["w_batch"], q="", flt=None, page=0, sel=None)


def set_q():
    ss.update(q=ss["w_q"], page=0, sel=None)


def set_flt():
    ss.update(flt=ss["w_flt"] or ss["flt"], page=0, sel=None)


# ------------------------------------------------------------------ رفع ملف
@st.dialog("رفع ملف شكاوى")
def upload_dialog():
    st.markdown('<div class="pop-text muted" style="margin-bottom:.6rem">ملف CSV بيه عمود <bdi>message</bdi> للرسالة. '
                'الأعمدة <bdi>complaint_id</bdi> و<bdi>received_at</bdi> و<bdi>channel</bdi> اختيارية.</div>',
                unsafe_allow_html=True)
    up = st.file_uploader("ملف CSV", type=["csv"], label_visibility="collapsed")
    if up is None:
        return
    try:
        rows, skipped = ingest.parse_csv(up.getvalue())
    except ingest.CSVError as e:
        st.markdown(f'<div class="line failed">ما نگدر نقرأ الملف: {esc(e)}</div>', unsafe_allow_html=True)
        return
    st.markdown(f'<div class="pop-text" style="margin:.4rem 0">{len(rows)} شكوى جاهزة للحفظ'
                + (f'، و{len(skipped)} سطر راح ينترك' if skipped else '') + '.</div>', unsafe_allow_html=True)
    if skipped:
        with st.expander("الأسطر المتروكة"):
            for s in skipped:
                st.write(f"السطر {s['row']}: {s['reason']}")
    if st.button("حفظ الدفعة", type="primary", width="stretch", disabled=not rows):
        with db.connect() as conn:
            bid, new = db.create_batch(conn, up.name, rows, skipped)
        ss.update(batch=bid, q="", flt=None, page=0, sel=None)
        old = len(rows) - new
        note_once(f"انحفظت الدفعة: {new} شكوى جديدة" + (f"، و{old} موجودة من قبل" if old else "") + ".")
        st.rerun()


if not batches:
    st.markdown('<div class="brand"><span class="name">مسار</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="empty">ما اكو دفعات بعد. ارفع ملف الشكاوى حتى تبدأ.</div>', unsafe_allow_html=True)
    if st.button("رفع ملف", type="primary", icon=":material/upload:"):
        upload_dialog()
    st.stop()

ids = [b["id"] for b in batches]
if ss.get("batch") not in ids:
    ss["batch"] = ids[0]
batch_id = ss["batch"]
with db.connect() as conn:
    rows = db.batch_rows(conn, batch_id)
by_id = {r["complaint_id"]: r for r in rows}
for r in rows:
    r["state"] = state(r)
by_state = Counter(r["state"] for r in rows)
pending = [r for r in rows if r["state"] == "pending"]
waiting = [r for r in rows if r["state"] == "waiting"]
failed = [r for r in rows if r["state"] == "failed"]
to_review = sorted((r for r in rows if r["state"] in ("flag", "review")), key=sort_key)
decided = by_state["approved"] + by_state["followup"]


def run(ids, verb):
    """يعالج الشكاوى ويعرض التقدم. كل شكوى تنحفظ أول ما تخلص، فالتوقف بالنص ما يضيع شي."""
    bar = progress_area.progress(0.0, text=f"{verb}… 0 من {len(ids)}")
    seen = Counter()

    def cb(i, n, cid, status):
        seen[status] += 1
        bar.progress(i / n, text=f"{verb}… {i} من {n}" + (f" · تعذّرت {seen['failed']}" if seen["failed"] else ""))

    s = pipeline.process(ids, on_progress=cb)
    note_once(f"خلصت المعالجة: {s['done']} نجحت" + (f"، و{s['failed']} ما انعالجت" if s["failed"] else "") + ".",
              "ok" if s["done"] else "err")
    st.rerun()


def queue_run(ids, verb):
    """الأزرار تسجّل الطلب بس، والمعالجة تشتغل تحت الشريط العلوي حتى يبين شريط التقدم بمكانه."""
    ss["run"] = (ids, verb)


# ------------------------------------------------------------------ حالة المعالجة (مكان صغير بالأعلى)
def status_widget():
    if not connected:
        label, color = "الموديل غير متصل", "red"
    elif waiting:
        label, color = "المعالجة متوقفة مؤقتاً", "orange"
    elif pending:
        label, color = f"{len(pending)} بانتظار المعالجة", "gray"
    elif failed:
        label, color = f"{len(failed)} شكوى فيها خطأ", "red"
    else:
        label, color = "المعالجة مكتملة", "primary"
    with st.popover(f":{color}[●] {label}", width="stretch"):
        if not connected:
            st.markdown(f'<div class="pop-title">الموديل غير متصل</div><div class="pop-text">أضف <bdi>{esc(key_env)}</bdi> '
                        'بملف <bdi>.env</bdi> بمجلد المشروع وأعد تشغيل البرنامج. المراجعة والاعتماد يشتغلون بدون اتصال.</div>',
                        unsafe_allow_html=True)
        if waiting:
            last = max(waiting, key=lambda r: r["updated_at"])
            at = retry_at(last)
            st.markdown('<div class="pop-title">المعالجة متوقفة مؤقتاً</div><div class="pop-text">'
                        f'{esc(short_error(last["error"]))}<br>'
                        f'<span class="muted">آخر محاولة {esc(fmt_time(last["updated_at"]))}'
                        + (f' · تگدر تعيد المحاولة بعد {esc(fmt_time(at, with_day=False))} تقريباً' if at else '')
                        + f'.<br>{len(waiting)} شكوى ما انعالجت بسببها. إعادة المحاولة تعالجها بس، والمراجعات تبقى مثل ما هي.</span></div>',
                        unsafe_allow_html=True)
            st.button(f"إعادة المحاولة ({len(waiting)})", type="primary", disabled=not connected, width="stretch",
                      on_click=queue_run, args=([r["complaint_id"] for r in waiting], "إعادة المحاولة"))
            with st.expander("نص الخطأ الكامل"):
                st.caption(md_esc(last["error"] or ""))
        if pending:
            st.markdown(f'<div class="pop-title" style="margin-top:.6rem">{len(pending)} شكوى بانتظار المعالجة</div>'
                        '<div class="pop-text muted">كل شكوى تنحفظ أول ما تخلص، فتگدر توقف وترجع تكمّل.</div>',
                        unsafe_allow_html=True)
            st.button("ابدأ المعالجة", type="primary", disabled=not connected, width="stretch",
                      on_click=queue_run, args=([r["complaint_id"] for r in pending], "جاري المعالجة"))
        if failed:
            st.markdown(f'<div class="pop-title" style="margin-top:.6rem">{len(failed)} شكوى فيها خطأ</div>'
                        '<div class="pop-text muted">خطأ يخص الشكوى نفسها. افتحها من فلتر «فيها خطأ» حتى تشوف السبب.</div>',
                        unsafe_allow_html=True)
        if connected and not (waiting or pending or failed):
            st.markdown(f'<div class="pop-text">كل الشكاوى بهالدفعة انعالجت.<br><span class="muted">الموديل: '
                        f'<bdi>{esc(config.MODEL)}</bdi></span></div>', unsafe_allow_html=True)


with st.container(key="topbar"):
    c_brand, c_status, c_batch, c_up = st.columns([2.6, 1.5, 2.1, 0.8], vertical_alignment="center")
    c_brand.markdown(f'<div class="brand"><span class="name">مسار</span><span class="sum"><b>{len(rows)}</b> شكوى · '
                     f'راجعت <b>{decided}</b> · بانتظار مراجعتك <b>{len(to_review)}</b></span></div>',
                     unsafe_allow_html=True)
    with c_status, st.container(key="status"):
        status_widget()
    ss["w_batch"] = ss["batch"]
    names = {b["id"]: f"{b['filename']} · {fmt_time(b['uploaded_at'])}" for b in batches}
    c_batch.selectbox("الدفعة", ids, format_func=names.get, key="w_batch", on_change=set_batch, label_visibility="collapsed")
    if c_up.button("رفع ملف", width="stretch", icon=":material/upload:"):
        upload_dialog()
progress_area = st.empty()
if "run" in ss:
    run(*ss.pop("run"))


# ------------------------------------------------------------------ تعديلات غير محفوظة
def saved_values(r):
    return {"draft": r["final_draft"] or r["draft"] or "", "cat": r["corrected_category"] or r["category"],
            "note": r["note"] or ""}


def current(r):
    return {**saved_values(r), **ss["edits"].get(r["complaint_id"], {})}


def is_dirty(r):
    return r["status"] == "done" and current(r) != saved_values(r)


def keep_edit(cid, field):
    ss["edits"].setdefault(cid, {})[field] = ss[f"w_{field}_{cid}"]


# ------------------------------------------------------------------ الفلاتر والقائمة
fcount = {k: sum(by_state[s] for s in v[1]) for k, v in FILTERS.items()}
options = [k for k in FILTERS if fcount[k] or k in ("todo", "all")]
if ss["flt"] not in options:
    ss["flt"] = "todo" if fcount["todo"] else "all"
items = [r for r in rows if r["state"] in FILTERS[ss["flt"]][1]]
if ss["q"]:
    q = ss["q"]
    items = [r for r in items if q in r["message"] or q in (r["transaction_id"] or "") or q in (r["source_id"] or "")]
items.sort(key=sort_key)
if ss["sel"] not in by_id:
    ss["sel"] = items[0]["complaint_id"] if items else rows[0]["complaint_id"]

list_col, detail_col = st.columns([1, 1.9], gap="medium")

with list_col:
    ss["w_q"] = ss["q"]
    st.text_input("بحث", key="w_q", on_change=set_q, placeholder="بحث بالنص أو رقم العملية أو المعرّف",
                  label_visibility="collapsed", icon=":material/search:")
    ss["w_flt"] = ss["flt"]
    st.pills("الحالة", options, format_func=lambda k: f"{FILTERS[k][0]} {fcount[k]}", key="w_flt",
             on_change=set_flt, label_visibility="collapsed")

    pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = min(ss["page"], pages - 1)
    if pages > 1:  # أزرار الصفحات تحت القائمة، فالقائمة تقصر بقدرها حتى ما تطلع برا الشاشة
        st.markdown("<style>:root { --list-h: calc(100vh - 242px); }</style>", unsafe_allow_html=True)
    with st.container(key="list"):
        if not items:
            st.markdown('<div class="empty">ما اكو شكاوى تطابق البحث أو الفلتر.</div>', unsafe_allow_html=True)
        for r in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            cid, s = r["complaint_id"], r["state"]
            meta = [CAT[current(r)["cat"]]] if r["status"] == "done" else []
            if r["priority"] == "high":
                meta.append("أولوية عالية")
            line1 = f"**:{STATE_MD[s]}[{STATES[s]}]**"
            if meta:
                line1 += f" :gray[· {md_esc(' · '.join(meta))}]"
            if is_dirty(r):
                line1 += " :orange[· تعديل غير محفوظ]"
            preview = r["message"].replace("\n", " ")
            preview = preview[:72] + ("…" if len(preview) > 72 else "")
            st.button(f"{line1}  \n:gray[{md_esc(label_id(r))}] · {md_esc(preview)}",
                      key=f"row-{'sel-' if cid == ss['sel'] else ''}{cid}", on_click=select, args=(cid,), width="stretch")
    if pages > 1:
        with st.container(key="pager"):
            p1, p2, p3 = st.columns([1, 1.6, 1], vertical_alignment="center")
            if p1.button("السابق", disabled=page == 0, width="stretch"):
                ss["page"] = page - 1
                st.rerun()
            p2.markdown(f'<div class="pager">{page + 1} من {pages} · {len(items)} شكوى</div>', unsafe_allow_html=True)
            if p3.button("التالي", disabled=page == pages - 1, width="stretch"):
                ss["page"] = page + 1
                st.rerun()


# ================================================================== التفاصيل
r = by_id[ss["sel"]]
cid, s = r["complaint_id"], r["state"]


def header():
    meta = [x for x in (r["channel"], fmt_time(r["received_at"]), f"السطر {r['row_number']} بالملف") if x]
    when = f' <small>· {esc(fmt_time(r["reviewed_at"]))}</small>' if s in ("approved", "followup") else ""
    st.markdown(f'<div class="d-head"><span class="d-id"><bdi>{esc(label_id(r))}</bdi></span>'
                f'<span class="st {s}">{STATES[s]}{when}</span><span class="d-meta">{esc(" · ".join(meta))}</span></div>',
                unsafe_allow_html=True)


def body():
    if s == "flag":
        st.markdown('<div class="line flag"><b>انتبه قبل الاعتماد:</b> '
                    + "، ".join(esc(REASONS.get(x, x)) for x in r["review_reasons"]) + '.</div>', unsafe_allow_html=True)
    elif s == "failed":
        st.markdown(f'<div class="line failed"><b>خطأ بهذي الشكوى:</b> {esc(short_error(r["error"]))}</div>',
                    unsafe_allow_html=True)
    elif s in ("waiting", "pending"):
        st.markdown('<div class="line quiet">ما انعالجت بعد. حالة المعالجة بالأعلى.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sec">رسالة الزبون</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="msg">{esc(r["message"])}</div>', unsafe_allow_html=True)

    if r["status"] == "done":
        cur = current(r)
        missing = set(r["missing_fields"])

        def fact(k, v, field=None, cls="", ltr=False):
            if v in (None, ""):
                v = '<span class="none">' + ("ما مذكور، المسودة تطلبه" if field in missing else "ما مذكور") + '</span>'
            else:
                v = f'<span class="v {cls}">' + (f"<bdi>{esc(v)}</bdi>" if ltr else esc(v)) + '</span>'
            return f'<div><span class="k">{k}</span>{v}</div>'

        st.markdown('<div class="sec">معلومات المعالجة</div>', unsafe_allow_html=True)
        st.markdown('<div class="facts">'
                    + fact("نوع المشكلة", CAT[cur["cat"]] + (" (صحّحته)" if cur["cat"] != r["category"] else ""))
                    + fact("الأولوية", PRI[r["priority"]], cls="high" if r["priority"] == "high" else "")
                    + fact("القسم", config.ROUTING[cur["cat"]])
                    + fact("الثقة", f"{r['confidence'] * 100:.0f}%", ltr=True)
                    + fact("رقم العملية", r["transaction_id"], "transaction_id", ltr=True)
                    + fact("المبلغ", config.format_amount(r["amount"]), "amount")
                    + fact("تاريخ المشكلة", r["date"], "date", ltr=True)
                    + '</div>'
                    + (f'<div class="why"><b>سبب التصنيف:</b> {esc(r["reason"])}</div>' if r["reason"] else ''),
                    unsafe_allow_html=True)

        st.markdown('<div class="sec">مسودة الرد</div>', unsafe_allow_html=True)
        ss[f"w_draft_{cid}"] = cur["draft"]
        st.text_area("مسودة الرد", key=f"w_draft_{cid}", height=140, label_visibility="collapsed",
                     on_change=keep_edit, args=(cid, "draft"))
        if cur != saved_values(r):
            st.markdown('<div class="unsaved">فيها تعديلات ما انحفظت بعد. تبقى لو فتحت شكوى ثانية ورجعت.</div>',
                        unsafe_allow_html=True)
        cat_keys = list(CAT)
        with st.expander("تصحيح النوع أو إضافة ملاحظة", expanded=bool(r["corrected_category"] or r["note"])):
            ss[f"w_cat_{cid}"] = cur["cat"]
            st.selectbox("نوع المشكلة", cat_keys, format_func=CAT.get, key=f"w_cat_{cid}", on_change=keep_edit, args=(cid, "cat"))
            ss[f"w_note_{cid}"] = cur["note"]
            st.text_input("ملاحظة داخلية", key=f"w_note_{cid}", on_change=keep_edit, args=(cid, "note"))

    with st.expander("تفاصيل تقنية"):
        tech = [f"المعرّف الداخلي: {cid}", f"المحاولات: {r['attempts']}", f"آخر تحديث: {fmt_time(r['updated_at'])}"]
        if r["model"]:
            tech += [f"الموديل: {r['model']} · نسخة التعليمات {r['prompt_version']}",
                     f"الزمن: {r['latency_ms']} ms · توكنات: {r['input_tokens']} دخول، {r['output_tokens']} خروج"]
        if r["review_reasons"]:
            tech.append("رموز المراجعة: " + "، ".join(r["review_reasons"]))
        if r["error"]:
            tech.append("نص الخطأ: " + r["error"])
        st.caption("  \n".join(md_esc(t) for t in tech))


def footer():
    """أزرار القرار ثابتة تحت التفاصيل، وجنبها رسالة قصيرة وحدة (نجاح أو خطأ)."""
    msg = ss.pop("note_once", None)
    if err := ss.pop(f"err_{cid}", None):
        msg = (err, "err")
    approve = follow = rewrite = False
    if r["status"] == "done":
        b1, b2, b3, m = st.columns([1.15, 1, 1, 2.6], vertical_alignment="center")
        approve = b1.button("اعتماد الرد" if s != "approved" else "حفظ التعديل", type="primary", width="stretch",
                            key=f"ok_{cid}", help="يحفظ الرد جاهز للإرسال اليدوي. مسار ما يرسل أي شي للزبون.")
        follow = b2.button("للمتابعة", width="stretch", key=f"fu_{cid}", disabled=s == "followup" and not is_dirty(r))
        rewrite = b3.button("إعادة كتابة", width="stretch", disabled=not connected, key=f"re_{cid}",
                            help="يكتب مسودة جديدة حسب النوع المختار")
    elif s == "failed":
        b1, m = st.columns([1.6, 3.15], vertical_alignment="center")
        msg = msg or ("تعيد معالجة هذي الشكوى بس. الباقي يبقى مثل ما هو.", "muted")
        b1.button("إعادة المحاولة لهذي الشكوى", type="primary", disabled=not connected, width="stretch", key=f"retry_{cid}",
                  on_click=queue_run, args=([cid], "إعادة المحاولة"))
    else:
        m = st.container()
        msg = msg or ("القرار يصير متاح بعد ما تنعالج الشكوى.", "muted")
    if msg is None:
        msg = ("الاعتماد ما يرسل شي للزبون؛ الإرسال يدوي من نظام الدعم.", "muted")
    m.markdown(f'<div class="foot-msg {msg[1]}">{msg[0]}</div>', unsafe_allow_html=True)

    def save(decision):
        v = current(r)
        with db.connect() as conn:
            db.save_review(conn, cid, decision, v["draft"], v["cat"] if v["cat"] != r["category"] else None, v["note"] or None)
        ss["edits"].pop(cid, None)
        ids_ = [x["complaint_id"] for x in to_review]
        after = ids_[ids_.index(cid) + 1:] + ids_[:ids_.index(cid)] if cid in ids_ else ids_
        ss["sel"] = after[0] if after else cid

    if approve:
        text = current(r)["draft"]
        found = pipeline.check_draft(text)
        if not text.strip():
            ss[f"err_{cid}"] = "المسودة فارغة."
        elif found:
            ss[f"err_{cid}"] = "المسودة بيها عبارة ممنوعة بدليل الدعم: " + "، ".join(found) + ". عدّلها وأعد الاعتماد."
        else:
            save("approved")
            note_once(f"✓ انعتمد رد <bdi>{esc(label_id(r))}</bdi>.")
        st.rerun()
    if follow:
        save("needs_followup")
        note_once(f"✓ انحفظت <bdi>{esc(label_id(r))}</bdi> للمتابعة.")
        st.rerun()
    if rewrite:
        v = current(r)
        if v["cat"] != r["category"]:
            with db.connect() as conn:
                db.save_review(conn, cid, r["decision"] or "needs_followup", v["draft"], v["cat"], v["note"] or None)
        with st.spinner("جاري كتابة مسودة جديدة…"):
            try:
                draft, found = pipeline.regenerate_draft(cid)
                ss["edits"].setdefault(cid, {})["draft"] = draft
                if found:
                    ss[f"err_{cid}"] = "المسودة الجديدة بيها عبارة ممنوعة، عدّلها قبل الاعتماد."
            except llm.LLMError as e:
                ss[f"err_{cid}"] = f"تعذّرت إعادة الكتابة: {short_error(str(e))}"
        st.rerun()


with detail_col, st.container(key="detail"):
    with st.container(key="dhead"):
        header()
    with st.container(key=f"dbody-{cid}"):  # مفتاح لكل شكوى: كل شكوى تنفتح من أعلاها
        body()
    with st.container(key="dfoot"):
        footer()
