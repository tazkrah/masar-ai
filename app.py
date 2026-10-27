"""شاشة الموظف — مسار.

تشغيل:  ./run.sh
"""
import html
import math
import re
from collections import Counter
from datetime import datetime

import streamlit as st

from masar import config, db, ingest, llm, pipeline

st.set_page_config(page_title="مسار — شكاوى الزبائن", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")
db.init()

# اتجاه الصفحة كلها من اليمين، حتى العناصر اللي Streamlit يرسمها خارج التطبيق (القوائم المنسدلة، النوافذ).
st.html("<script>document.documentElement.setAttribute('dir','rtl');document.documentElement.setAttribute('lang','ar');</script>",
        unsafe_allow_javascript=True)

CSS = """
<link href="https://fonts.googleapis.com/css2?family=Almarai:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root { --teal:#0E7C86; --teal-soft:#E7F2F3; --ink:#1C2530; --muted:#5B6773; --line:#E1E7E8; --soft:#F2F6F6;
        --amber:#9A6200; --amber-bg:#FBF4E6; --amber-line:#EED9AE; --red:#B42318; --red-bg:#FCEEEC; --red-line:#F2CBC6; }
html, body, .stApp, .stMarkdown, button, input, textarea, select, label, [data-testid="stWidgetLabel"] p {
  font-family: 'Almarai', sans-serif !important; }
.stApp, .stApp *, [data-baseweb="popover"], [data-baseweb="popover"] *, [data-testid="stDialog"] * {
  direction: rtl; }
.stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, [data-testid="stDialog"] p { text-align: right; }
bdi, .ltr { direction: ltr; unicode-bidi: isolate; }
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"] { display: none; }
.block-container { padding-top: 1rem; padding-bottom: 1.2rem; max-width: 1360px; }
.stButton button { border-radius: 6px; font-weight: 700; }
p, li { margin-bottom: 0; }
.stMarkdown [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
/* الشريط العلوي */
.st-key-topbar { border-bottom: 1px solid var(--line); padding-bottom: .7rem; }
.brand { display:flex; align-items:baseline; gap:.55rem; }
.brand .name { color: var(--teal); font-size: 1.6rem; font-weight: 800; line-height: 1; }
.brand .tag { color: var(--ink); font-size: .92rem; font-weight: 700; }
/* ملخص الدفعة */
.summary { display:flex; flex-wrap:wrap; align-items:center; gap:.2rem 1.2rem; font-size:.86rem; color: var(--muted); }
.summary b { color: var(--ink); font-variant-numeric: tabular-nums; }
.summary i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-left:.35rem; }
.track { display:flex; height:4px; border-radius:2px; overflow:hidden; background: var(--soft); margin:.2rem 0 .45rem; }
.track span { display:block; height:100%; }
/* رسائل وخطوات تنتظر الموظف: مكانها ثابت تحت الملخص */
.st-key-steps { gap: .4rem; }
[class*="st-key-step-"] { border-radius: 6px; padding: .3rem .4rem .3rem .8rem; border: 1px solid var(--line); background: var(--soft); }
.st-key-step-failed { background: var(--red-bg); border-color: var(--red-line); }
.step { font-size: .9rem; color: var(--ink); }
.step .muted { color: var(--muted); font-size: .84rem; }
.flash { font-size: .88rem; border-radius: 6px; padding: .45rem .8rem; border: 1px solid #C5E1E3; background: var(--teal-soft); color: #0B4F55; }
.flash.err { border-color: var(--red-line); background: var(--red-bg); color: #5C150F; }
/* الفلاتر */
.st-key-w_flt [data-testid="stButtonGroup"] > div { flex-wrap: wrap; overflow: visible; gap: .35rem; padding: 1px; }
.st-key-w_flt button { border-radius: 999px; padding: .2rem .75rem; min-height: 0; white-space: nowrap; }
.st-key-w_flt button p { font-size: .84rem; }
/* قائمة الشكاوى */
.st-key-list { border: 1px solid var(--line); border-radius: 6px; }
.st-key-list [data-testid="stVerticalBlock"] { gap: 0; }
[class*="st-key-row-"] button { justify-content: flex-start; width: 100%; min-height: 0; border: none; border-radius: 0;
  border-bottom: 1px solid var(--line); padding: .55rem .75rem; background: #fff; color: var(--ink); font-weight: 400; }
[class*="st-key-row-"] button:hover { background: var(--soft); color: var(--ink); }
[class*="st-key-row-"] button:focus:not(:active) { border-color: var(--line); color: var(--ink); }
[class*="st-key-row-"] button p { font-size: .88rem; line-height: 1.6; text-align: right; }
[class*="st-key-row-"] button p strong { font-size: .78rem; }
[class*="st-key-row-sel-"] button { background: var(--teal-soft); box-shadow: inset -3px 0 0 var(--teal); }
[class*="st-key-row-sel-"] button:hover { background: var(--teal-soft); }
.pager { text-align:center; font-size:.82rem; color: var(--muted); }
.st-key-pager button { min-height: 0; padding: .2rem .6rem; }
.empty { color: var(--muted); font-size: .9rem; padding: 1.2rem .8rem; }
/* التفاصيل */
.st-key-detail { border: 1px solid var(--line); border-radius: 8px; padding: .9rem 1.1rem 1rem; }
.d-head { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap; }
.d-id { font-size: 1.2rem; font-weight: 800; color: var(--ink); }
.d-meta { color: var(--muted); font-size: .84rem; }
.st { font-size: .84rem; font-weight: 700; white-space: nowrap; }
.st::before { content:""; display:inline-block; width:7px; height:7px; border-radius:50%; margin-left:.4rem; background: currentColor; }
.st.flag { color: var(--amber); } .st.review { color: var(--teal); } .st.failed { color: var(--red); }
.st.pending, .st.followup { color: var(--muted); } .st.approved { color: var(--teal); }
.line { margin-top:.55rem; font-size:.87rem; border-radius:6px; padding:.4rem .75rem; border:1px solid; }
.line.flag { color:#4A3500; background: var(--amber-bg); border-color: var(--amber-line); }
.line.ok { color:#0B4F55; background: var(--teal-soft); border-color:#C5E1E3; }
.line.failed { color:#5C150F; background: var(--red-bg); border-color: var(--red-line); }
.line.quiet { color: var(--muted); border-color: var(--line); }
.sec { font-size: .78rem; color: var(--muted); font-weight: 700; margin: .8rem 0 .3rem; }
.msg { background: var(--soft); border-right: 3px solid var(--teal); padding: .6rem .85rem; border-radius: 6px;
       font-size: 1rem; line-height: 1.8; color: var(--ink); white-space: pre-wrap; }
.facts { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .45rem 1rem; font-size: .9rem; }
.facts .k { color: var(--muted); font-size: .76rem; display:block; }
.facts .v { color: var(--ink); font-weight: 700; }
.facts .none { color: var(--muted); font-weight: 400; }
.facts .high { color: var(--red); }
.why { color: var(--muted); font-size: .85rem; margin-top: .55rem; line-height: 1.65; }
.hint { color: var(--muted); font-size: .8rem; margin-top: .3rem; }
.unsaved { color: var(--amber); font-size: .8rem; }
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

# حالة كل شكوى بالشاشة، بترتيب العرض بالقائمة
STATES = {
    "flag": "تحتاج مراجعة",
    "review": "جاهزة للمراجعة",
    "failed": "تعذّرت المعالجة",
    "pending": "بانتظار المعالجة",
    "followup": "للمتابعة",
    "approved": "معتمدة",
}
STATE_MD = {"flag": "orange", "review": "primary", "failed": "red", "pending": "gray", "followup": "gray", "approved": "primary"}
SUMMARY = [("approved", "معتمدة", "#0E7C86"), ("followup", "للمتابعة", "#5BA8AE"),
           ("review", "بانتظار المراجعة", "#B9D9DC"), ("pending", "بانتظار المعالجة", "#C9D1D3"),
           ("failed", "تعذّرت", "#B42318")]
FILTERS = {
    "todo": ("للمراجعة", ("flag", "review")),
    "followup": ("للمتابعة", ("followup",)),
    "approved": ("معتمدة", ("approved",)),
    "failed": ("تعذّرت", ("failed",)),
    "pending": ("بانتظار المعالجة", ("pending",)),
    "all": ("الكل", tuple(STATES)),
}
ORDER = list(STATES)
PRI_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def md_esc(s):
    return re.sub(r"([\\`*_\[\]#<>|~:$])", r"\\\1", s)


def fmt_time(s):
    """2026-10-09 22:40 أو ISO بتوقيت UTC -> 9 تشرين الأول، 22:40 بالتوقيت المحلي."""
    if not s:
        return ""
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return s
    if d.tzinfo:
        d = d.astimezone()
    return f"{d.day} {MONTHS[d.month - 1]}، {d:%H:%M}"


def short_error(e):
    """أول جملة من رسالة الخطأ، بدون تفاصيل المزوّد ولا وقت الانتظار (يصير قديم بسرعة)."""
    first = (e or "سبب غير معروف").split(" (")[0].split(". ")[0].strip()
    return first if first.endswith((".", "؟")) else first + "."


def state(r):
    if r["status"] in ("pending", "failed"):
        return r["status"]
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
# رسالة قصيرة بمكان ثابت: {"text", "kind": ok|err, "where": top|cid}
ss.setdefault("flash", None)

connected = llm.key_status()[1]
key_env = llm.key_status()[0]

with db.connect() as conn:
    batches = db.list_batches(conn)


def flash(text, where="top", kind="ok"):
    ss["flash"] = {"text": text, "where": where, "kind": kind}


def show_flash(where):
    f = ss["flash"]
    if f and f["where"] == where:
        st.markdown(f'<div class="flash {"err" if f["kind"] == "err" else ""}">{f["text"]}</div>', unsafe_allow_html=True)


def select(cid):
    ss["flash"] = None
    ss["sel"] = cid


def set_batch():
    ss.update(batch=ss["w_batch"], q="", flt=None, page=0, sel=None, flash=None)


def set_q():
    ss.update(q=ss["w_q"], page=0, sel=None)


def set_flt():
    ss.update(flt=ss["w_flt"] or ss["flt"], page=0, sel=None)


# ------------------------------------------------------------------ رفع ملف
@st.dialog("رفع ملف شكاوى")
def upload_dialog():
    st.markdown('<div class="hint" style="margin:0 0 .6rem">ملف CSV بيه عمود <bdi>message</bdi> للرسالة. '
                'الأعمدة <bdi>complaint_id</bdi> و<bdi>received_at</bdi> و<bdi>channel</bdi> اختيارية.</div>',
                unsafe_allow_html=True)
    up = st.file_uploader("ملف CSV", type=["csv"], label_visibility="collapsed")
    if up is None:
        return
    try:
        rows, skipped = ingest.parse_csv(up.getvalue())
    except ingest.CSVError as e:
        st.markdown(f'<div class="flash err">ما نگدر نقرأ الملف: {esc(e)}</div>', unsafe_allow_html=True)
        return
    st.markdown(f'<div class="step" style="margin:.4rem 0">{len(rows)} شكوى جاهزة للحفظ'
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
        flash(f"انحفظت الدفعة <bdi>{esc(up.name)}</bdi>: {new} شكوى جديدة"
              + (f"، و{old} موجودة من قبل وما تكررت" if old else "") + ".")
        st.rerun()


# ------------------------------------------------------------------ الشريط العلوي
with st.container(key="topbar"):
    c_brand, c_batch, c_up = st.columns([3, 2.3, 0.9], vertical_alignment="center")
    c_brand.markdown('<div class="brand"><span class="name">مسار</span><span class="tag">تنظيم شكاوى الزبائن</span></div>',
                     unsafe_allow_html=True)
    if batches:
        ids = [b["id"] for b in batches]
        if ss.get("batch") not in ids:
            ss["batch"] = ids[0]
        ss["w_batch"] = ss["batch"]
        names = {b["id"]: f"{b['filename']} · {fmt_time(b['uploaded_at'])}" for b in batches}
        c_batch.selectbox("الدفعة", ids, format_func=names.get, key="w_batch", on_change=set_batch,
                          label_visibility="collapsed")
    if c_up.button("رفع ملف", type="secondary" if batches else "primary", width="stretch", icon=":material/upload:"):
        upload_dialog()

if not batches:
    st.markdown('<div class="empty">ما اكو دفعات بعد. ارفع ملف الشكاوى من زر «رفع ملف» بالأعلى حتى تبدأ.</div>',
                unsafe_allow_html=True)
    st.stop()

batch_id = ss["batch"]
with db.connect() as conn:
    rows = db.batch_rows(conn, batch_id)
by_id = {r["complaint_id"]: r for r in rows}
for r in rows:
    r["state"] = state(r)
by_state = Counter(r["state"] for r in rows)
pending = [r for r in rows if r["state"] == "pending"]
failed = [r for r in rows if r["state"] == "failed"]
to_review = sorted((r for r in rows if r["state"] in ("flag", "review")), key=sort_key)

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


# ------------------------------------------------------------------ الملخص والخطوات
counts = {"review": by_state["review"] + by_state["flag"],
          **{k: by_state[k] for k in ("approved", "followup", "pending", "failed")}}
total = max(1, len(rows))
st.markdown('<div class="track">'
            + "".join(f'<span style="width:{counts[k] / total * 100:.2f}%;background:{c}"></span>' for k, _, c in SUMMARY)
            + '</div><div class="summary"><span><b>' + str(len(rows)) + '</b> شكوى بالدفعة</span>'
            + "".join(f'<span><i style="background:{c}"></i>{lbl} <b>{counts[k]}</b></span>' for k, lbl, c in SUMMARY if counts[k])
            + '</div>', unsafe_allow_html=True)


def run(ids, verb, area):
    """يعالج الشكاوى ويعرض التقدم. كل شكوى تنحفظ أول ما تخلص، فالتوقف بالنص ما يضيع شي."""
    bar = area.progress(0.0, text=f"{verb}… 0 من {len(ids)}")
    seen = Counter()

    def cb(i, n, cid, status):
        seen[status] += 1
        bar.progress(i / n, text=f"{verb}… {i} من {n}" + (f" · تعذّرت {seen['failed']}" if seen["failed"] else ""))

    s = pipeline.process(ids, on_progress=cb)
    flash(f"خلصت المعالجة: {s['done']} نجحت" + (f"، و{s['failed']} تعذّرت" if s["failed"] else "") + ".",
          kind="err" if s["failed"] and not s["done"] else "ok")
    st.rerun()


steps = st.container(key="steps")
with steps:
    show_flash("top")
    no_key = f' <span class="muted">المعالجة تحتاج مفتاح الموديل: أضف <bdi>{esc(key_env)}</bdi> بملف <bdi>.env</bdi> وأعد التشغيل.</span>'
    if pending:
        with st.container(key="step-pending"):
            a, b = st.columns([5, 1], vertical_alignment="center")
            a.markdown(f'<div class="step"><b>{len(pending)} شكوى بانتظار المعالجة.</b>'
                       + (' <span class="muted">كل شكوى تنحفظ أول ما تخلص.</span>' if connected else no_key) + '</div>',
                       unsafe_allow_html=True)
            if b.button("ابدأ المعالجة", type="primary", disabled=not connected, width="stretch"):
                run([r["complaint_id"] for r in pending], "جاري المعالجة", steps)
    if failed:
        top = Counter(short_error(r["error"]) for r in failed).most_common(1)[0][0]
        with st.container(key="step-failed"):
            a, b, c = st.columns([4.3, 0.7, 1], vertical_alignment="center")
            a.markdown(f'<div class="step"><b>{len(failed)} شكوى تعذّرت معالجتها.</b> {esc(top)}'
                       + ('' if connected else no_key) + '</div>', unsafe_allow_html=True)
            with b.popover("التفاصيل", type="tertiary"):
                st.markdown(f"آخر محاولة: {fmt_time(max(r['updated_at'] for r in failed))}  \n"
                            "إعادة المحاولة تعالج الشكاوى الفاشلة بس. المراجعات والنتائج السابقة تبقى مثل ما هي.")
                st.caption("نص الخطأ من آخر شكوى فشلت: " + (max(failed, key=lambda r: r["updated_at"])["error"] or ""))
            if c.button("إعادة المحاولة", type="primary", disabled=not connected, width="stretch"):
                run([r["complaint_id"] for r in failed], "إعادة المحاولة", steps)

st.write("")
list_col, detail_col = st.columns([1, 1.85], gap="medium")

# ================================================================== القائمة (يمين)
with list_col:
    ss["w_q"] = ss["q"]
    st.text_input("بحث", key="w_q", on_change=set_q, placeholder="بحث بالنص أو رقم العملية أو المعرّف",
                  label_visibility="collapsed", icon=":material/search:")
    ss["w_flt"] = ss["flt"]
    st.pills("الحالة", options, format_func=lambda k: f"{FILTERS[k][0]} {fcount[k]}", key="w_flt",
             on_change=set_flt, label_visibility="collapsed")

    pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = min(ss["page"], pages - 1)
    with st.container(height=520 if len(items) > 6 else "content", key="list"):
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
            label = f"{line1}  \n:gray[{md_esc(label_id(r))}] · {md_esc(preview)}"
            st.button(label, key=f"row-{'sel-' if cid == ss['sel'] else ''}{cid}", on_click=select, args=(cid,),
                      width="stretch")
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


# ================================================================== التفاصيل (يسار)
def decision_panel(r):
    cid, s = r["complaint_id"], r["state"]
    cur, saved = current(r), saved_values(r)
    st.markdown('<div class="sec">مسودة الرد</div>', unsafe_allow_html=True)
    ss[f"w_draft_{cid}"] = cur["draft"]
    st.text_area("مسودة الرد", key=f"w_draft_{cid}", height=165, label_visibility="collapsed",
                 on_change=keep_edit, args=(cid, "draft"))
    if cur != saved:
        st.markdown('<div class="unsaved">فيها تعديلات ما انحفظت بعد. تبقى لو فتحت شكوى ثانية ورجعت.</div>',
                    unsafe_allow_html=True)

    cat_keys = list(CAT)
    with st.expander("تصحيح النوع أو إضافة ملاحظة", expanded=bool(r["corrected_category"] or r["note"])):
        ss[f"w_cat_{cid}"] = cur["cat"]
        st.selectbox("نوع المشكلة", cat_keys, format_func=CAT.get, key=f"w_cat_{cid}", on_change=keep_edit, args=(cid, "cat"))
        ss[f"w_note_{cid}"] = cur["note"]
        st.text_input("ملاحظة داخلية", key=f"w_note_{cid}", on_change=keep_edit, args=(cid, "note"))

    if err := ss.pop(f"err_{cid}", None):
        st.markdown(f'<div class="line failed" style="margin:0 0 .5rem">{esc(err)}</div>', unsafe_allow_html=True)

    def save(decision):
        v = current(r)
        with db.connect() as conn:
            db.save_review(conn, cid, decision, v["draft"], v["cat"] if v["cat"] != r["category"] else None, v["note"] or None)
        ss["edits"].pop(cid, None)

    def next_in_queue():
        ids = [x["complaint_id"] for x in to_review]
        after = ids[ids.index(cid) + 1:] + ids[:ids.index(cid)] if cid in ids else ids
        return after[0] if after else cid

    b1, b2, b3 = st.columns([1.25, 1, 1])
    if b1.button("اعتماد الرد" if s != "approved" else "حفظ التعديل", type="primary", width="stretch", key=f"ok_{cid}"):
        text = current(r)["draft"]
        found = pipeline.check_draft(text)
        if not text.strip():
            ss[f"err_{cid}"] = "المسودة فارغة."
        elif found:
            ss[f"err_{cid}"] = "المسودة بيها عبارة ممنوعة بدليل الدعم: " + "، ".join(found) + ". عدّلها وأعد الاعتماد."
        else:
            save("approved")
            nxt = next_in_queue()
            ss["sel"] = nxt
            flash(f"✓ انعتمد رد <bdi>{esc(label_id(r))}</bdi>. ما انرسل شي للزبون."
                  + (" هذي الشكوى التالية." if nxt != cid else ""), where=nxt)
        st.rerun()
    if b2.button("للمتابعة", width="stretch", key=f"fu_{cid}", disabled=s == "followup" and cur == saved):
        save("needs_followup")
        nxt = next_in_queue()
        ss["sel"] = nxt
        flash(f"✓ انحفظت <bdi>{esc(label_id(r))}</bdi> للمتابعة مع المسودة الحالية."
              + (" هذي الشكوى التالية." if nxt != cid else ""), where=nxt)
        st.rerun()
    if b3.button("إعادة كتابة", width="stretch", disabled=not connected, key=f"re_{cid}",
                 help="يكتب مسودة جديدة حسب النوع المختار"):
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
    st.markdown('<div class="hint">الاعتماد يحفظ الرد جاهز للإرسال اليدوي. مسار ما يرسل أي شي للزبون.</div>',
                unsafe_allow_html=True)


with detail_col, st.container(key="detail"):
    r = by_id[ss["sel"]]
    cid, s = r["complaint_id"], r["state"]
    show_flash(cid)
    meta = [x for x in (r["channel"], fmt_time(r["received_at"]), f"السطر {r['row_number']} بالملف") if x]
    st.markdown(f'<div class="d-head"><span class="d-id"><bdi>{esc(label_id(r))}</bdi></span>'
                f'<span class="st {s}">{STATES[s]}</span><span class="d-meta">{esc(" · ".join(meta))}</span></div>',
                unsafe_allow_html=True)

    if s == "flag":
        st.markdown('<div class="line flag"><b>انتبه قبل الاعتماد:</b> '
                    + "، ".join(esc(REASONS.get(x, x)) for x in r["review_reasons"]) + '.</div>', unsafe_allow_html=True)
    elif s == "failed":
        st.markdown(f'<div class="line failed"><b>تعذّرت المعالجة:</b> {esc(short_error(r["error"]))} '
                    f'آخر محاولة {esc(fmt_time(r["updated_at"]))}.</div>', unsafe_allow_html=True)
    elif s == "pending":
        st.markdown('<div class="line quiet">ما انعالجت بعد. ابدأ المعالجة من الأعلى.</div>', unsafe_allow_html=True)
    elif s == "approved":
        st.markdown(f'<div class="line ok">اعتمدت الرد {esc(fmt_time(r["reviewed_at"]))}. '
                    'الإرسال للزبون يصير يدوياً من نظام الدعم.</div>', unsafe_allow_html=True)
    elif s == "followup":
        st.markdown(f'<div class="line quiet">تركتها للمتابعة {esc(fmt_time(r["reviewed_at"]))}.</div>', unsafe_allow_html=True)

    right, left = st.columns([1, 1.1], gap="medium")
    with right:
        st.markdown('<div class="sec">رسالة الزبون</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="msg">{esc(r["message"])}</div>', unsafe_allow_html=True)

        if r["status"] == "done":
            cat = current(r)["cat"]
            missing = set(r["missing_fields"])

            def fact(k, v, field=None, cls="", ltr=False):
                if v in (None, ""):
                    v = '<span class="none">' + ("ما مذكور، والمسودة تطلبه" if field in missing else "ما مذكور") + '</span>'
                else:
                    v = f'<span class="v {cls}">' + (f"<bdi>{esc(v)}</bdi>" if ltr else esc(v)) + '</span>'
                return f'<div><span class="k">{k}</span>{v}</div>'

            st.markdown('<div class="sec">نتيجة المعالجة</div>', unsafe_allow_html=True)
            st.markdown('<div class="facts">'
                        + fact("نوع المشكلة", CAT[cat] + (" (صحّحته)" if cat != r["category"] else ""))
                        + fact("الأولوية", PRI[r["priority"]], cls="high" if r["priority"] == "high" else "")
                        + fact("القسم", config.ROUTING[cat])
                        + fact("رقم العملية", r["transaction_id"], "transaction_id", ltr=True)
                        + fact("المبلغ", config.format_amount(r["amount"]), "amount")
                        + fact("تاريخ المشكلة", r["date"], "date", ltr=True)
                        + '</div><div class="why">'
                        + (f'<b>سبب التصنيف:</b> {esc(r["reason"])} · ' if r["reason"] else '')
                        + f'الثقة <bdi>{r["confidence"] * 100:.0f}%</bdi></div>', unsafe_allow_html=True)

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

    with left:
        if r["status"] == "done":
            decision_panel(r)
        elif r["status"] == "failed":
            st.markdown('<div class="sec">الخطوة التالية</div>', unsafe_allow_html=True)
            if st.button("إعادة المحاولة لهذي الشكوى", type="primary", disabled=not connected, key=f"retry_{cid}"):
                run([cid], "إعادة المحاولة", steps)
            st.markdown('<div class="hint">تعيد معالجة هذي الشكوى بس. الباقي يبقى مثل ما هو.</div>', unsafe_allow_html=True)
