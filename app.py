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

CSS = """
<link href="https://fonts.googleapis.com/css2?family=Almarai:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root { --teal:#0E7C86; --teal-soft:#E7F2F3; --ink:#1C2530; --muted:#5B6773; --line:#E1E7E8; --soft:#F2F6F6;
        --amber:#9A6200; --amber-bg:#FBF4E6; --amber-line:#EED9AE; --red:#B42318; --red-bg:#FCEEEC; --red-line:#F2CBC6; }
html, body, .stApp, .stMarkdown, button, input, textarea, select, label, [data-testid="stWidgetLabel"] p {
  font-family: 'Almarai', sans-serif !important; }
.stApp, [data-testid="stDialog"] { direction: rtl; }
header[data-testid="stHeader"] { display: none; }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none; }
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1240px; }
.stButton button { border-radius: 6px; font-weight: 700; }
p, li { margin-bottom: 0; }
/* الشريط العلوي */
.st-key-topbar { border-bottom: 1px solid var(--line); padding-bottom: .9rem; margin-bottom: .4rem; }
.brand { display:flex; align-items:baseline; gap:.55rem; }
.brand .name { color: var(--teal); font-size: 1.7rem; font-weight: 800; line-height: 1; }
.brand .tag { color: var(--ink); font-size: .95rem; font-weight: 700; }
/* ملخص الدفعة */
.summary { display:flex; flex-wrap:wrap; align-items:center; gap:.2rem 1.3rem; font-size:.88rem; color: var(--muted); }
.summary b { color: var(--ink); font-variant-numeric: tabular-nums; }
.summary i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-left:.35rem; }
.track { display:flex; height:4px; border-radius:2px; overflow:hidden; background: var(--soft); margin:.5rem 0 .2rem; }
.track span { display:block; height:100%; }
/* الخطوة التالية */
[class*="st-key-next-"] { border-radius: 6px; padding: .45rem .5rem .45rem .9rem; border: 1px solid var(--line); }
.st-key-next-failed { background: var(--red-bg); border-color: var(--red-line); }
.st-key-next-pending, .st-key-next-review { background: var(--soft); }
.next { font-size: .93rem; color: var(--ink); }
.next .muted { color: var(--muted); font-size: .85rem; }
/* الجدول */
[class*="st-key-tr-"] [data-testid="stMarkdownContainer"], .st-key-thead [data-testid="stMarkdownContainer"],
[class*="st-key-next-"] [data-testid="stMarkdownContainer"], .st-key-nav [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
.st-key-tbody, .st-key-steps { gap: 0; }
.st-key-steps { gap: .45rem; margin: .7rem 0 .5rem; }
.th { font-size: .78rem; color: var(--muted); font-weight: 700; }
.st-key-thead { border-bottom: 1px solid var(--line); padding: .2rem .4rem .45rem; }
[class*="st-key-tr-"] { border-bottom: 1px solid var(--line); padding: .3rem .4rem; position: relative; }
[class*="st-key-tr-"]:hover { background: var(--soft); }
[class*="st-key-tr-"] [data-testid="stElementContainer"],
[class*="st-key-tr-"] [data-testid="stColumn"], [class*="st-key-tr-"] [data-testid="stHorizontalBlock"],
[class*="st-key-tr-"] [data-testid="stVerticalBlock"], [class*="st-key-tr-"] .stButton { position: static; }
[class*="st-key-tr-"] button { width:100%; justify-content:flex-start; padding: .35rem 0; min-height: 0; color: var(--ink); font-weight: 400; }
[class*="st-key-tr-"] button::after { content:""; position:absolute; inset:0; }  /* الصف كله ينضغط */
[class*="st-key-tr-"] button p { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: right; font-size: .92rem; }
[class*="st-key-tr-"] button:hover { color: var(--teal); }
.cell { font-size: .88rem; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cell.id { color: var(--muted); font-size: .82rem; font-variant-numeric: tabular-nums; direction: ltr; text-align: right; }
.cell.muted { color: var(--muted); }
.cell.high { color: var(--red); font-weight: 700; }
.st { font-size: .84rem; font-weight: 700; white-space: nowrap; }
.st::before { content:""; display:inline-block; width:7px; height:7px; border-radius:50%; margin-left:.4rem; background: currentColor; }
.st.flag { color: var(--amber); } .st.review { color: var(--teal); } .st.failed { color: var(--red); }
.st.pending, .st.followup { color: var(--muted); } .st.approved { color: var(--ink); font-weight: 400; }
.st.approved::before { background: var(--teal); }
.pager { text-align:center; font-size:.85rem; color: var(--muted); padding-top:.45rem; }
.empty { color: var(--muted); font-size: .9rem; padding: 1.5rem .4rem; }
/* التفاصيل */
.d-head { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap; }
.d-id { font-size: 1.25rem; font-weight: 800; color: var(--ink); }
.d-meta { color: var(--muted); font-size: .85rem; }
.flagline { margin-top:.6rem; font-size:.88rem; color:#4A3500; background: var(--amber-bg); border:1px solid var(--amber-line);
            border-radius:6px; padding:.45rem .8rem; }
.okline { margin-top:.6rem; font-size:.88rem; color:#0B4F55; background: var(--teal-soft); border:1px solid #C5E1E3;
          border-radius:6px; padding:.45rem .8rem; }
.failline { margin-top:.6rem; font-size:.88rem; color:#5C150F; background: var(--red-bg); border:1px solid var(--red-line);
            border-radius:6px; padding:.45rem .8rem; }
.quietline { margin-top:.6rem; font-size:.88rem; color: var(--muted); border:1px solid var(--line); border-radius:6px; padding:.45rem .8rem; }
.sec { font-size: .78rem; color: var(--muted); font-weight: 700; margin: .9rem 0 .35rem; }
.msg { background: var(--soft); border-right: 3px solid var(--teal); padding: .7rem .9rem; border-radius: 6px;
       font-size: 1.02rem; line-height: 1.8; color: var(--ink); white-space: pre-wrap; }
.facts { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: .55rem 1rem; font-size: .9rem; }
.facts .k { color: var(--muted); font-size: .76rem; display:block; }
.facts .v { color: var(--ink); font-weight: 700; }
.facts .none { color: var(--muted); font-weight: 400; }
.facts .high { color: var(--red); }
.why { color: var(--muted); font-size: .86rem; margin-top: .6rem; line-height: 1.65; }
.hint { color: var(--muted); font-size: .8rem; margin-top: .4rem; }
.unsaved { color: var(--amber); font-size: .8rem; }
.pos { text-align:center; color: var(--muted); font-size:.85rem; padding-top:.45rem; }
[data-testid="stExpander"] summary p { font-size: .85rem; color: var(--muted); }
[data-testid="stFileUploaderDropzoneInstructions"] { direction: rtl; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

CAT = config.CATEGORIES
PRI = config.PRIORITIES
REASONS = config.REVIEW_REASON_LABELS
MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
          "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]
PAGE_SIZE = 15

# حالة كل شكوى بالشاشة، بترتيب العرض بالجدول
STATES = {
    "flag": "تحتاج مراجعة",
    "review": "جاهزة للمراجعة",
    "failed": "تعذّرت المعالجة",
    "pending": "بانتظار المعالجة",
    "followup": "للمتابعة",
    "approved": "معتمدة",
}
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


def toast(msg):
    st.session_state["toast"] = msg


ss = st.session_state
# البحث والفلتر والصفحة محفوظة بمفاتيح خارج الحقول، حتى تبقى لما نفتح التفاصيل ونرجع.
ss.setdefault("q", "")
ss.setdefault("flt", None)
ss.setdefault("page", 0)
ss.setdefault("view", None)  # None = الجدول، أو معرّف الشكوى المفتوحة


def open_complaint(cid):
    ss["view"] = cid


def back():
    ss["view"] = None


def set_batch():
    ss["batch"] = ss["w_batch"]
    ss.update(view=None, q="", flt=None, page=0)


def set_q():
    ss.update(q=ss["w_q"], page=0)


def set_flt():
    ss.update(flt=ss["w_flt"] or "all", page=0)


connected = llm.key_status()[1]
key_env = llm.key_status()[0]
if "toast" in ss:
    st.toast(ss.pop("toast"))

with db.connect() as conn:
    batches = db.list_batches(conn)


# ------------------------------------------------------------------ رفع ملف
@st.dialog("رفع ملف شكاوى")
def upload_dialog():
    st.markdown('<div class="hint" style="margin:0 0 .6rem">ملف CSV بيه عمود <code>message</code>. '
                'الأعمدة <code>complaint_id</code> و<code>received_at</code> و<code>channel</code> اختيارية.</div>',
                unsafe_allow_html=True)
    up = st.file_uploader("ملف CSV", type=["csv"], label_visibility="collapsed")
    if up is None:
        return
    try:
        rows, skipped = ingest.parse_csv(up.getvalue())
    except ingest.CSVError as e:
        st.markdown(f'<div class="failline">ما نگدر نقرأ الملف: {esc(e)}</div>', unsafe_allow_html=True)
        return
    st.markdown(f'<div class="next" style="margin:.4rem 0">{len(rows)} شكوى جاهزة للحفظ'
                + (f'، و{len(skipped)} سطر راح ينترك' if skipped else '') + '.</div>', unsafe_allow_html=True)
    if skipped:
        with st.expander("الأسطر المتروكة"):
            for s in skipped:
                st.write(f"السطر {s['row']}: {s['reason']}")
    if st.button("حفظ الدفعة", type="primary", width="stretch", disabled=not rows):
        with db.connect() as conn:
            bid, new = db.create_batch(conn, up.name, rows, skipped)
        ss["batch"] = bid
        ss.update(view=None, q="", flt=None, page=0)
        old = len(rows) - new
        toast(f"انحفظت الدفعة: {new} شكوى جديدة" + (f"، و{old} موجودة من قبل وما تكررت" if old else "") + ".")
        st.rerun()


# ------------------------------------------------------------------ الشريط العلوي
with st.container(key="topbar"):
    c_brand, c_batch, c_up = st.columns([3, 2.2, 0.9], vertical_alignment="center")
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
batch = next(b for b in batches if b["id"] == batch_id)
with db.connect() as conn:
    rows = db.batch_rows(conn, batch_id)
for r in rows:
    r["state"] = state(r)
by_state = Counter(r["state"] for r in rows)
pending = [r for r in rows if r["state"] == "pending"]
failed = [r for r in rows if r["state"] == "failed"]
to_review = sorted((r for r in rows if r["state"] in ("flag", "review")), key=sort_key)

# الفلتر والبحث يحددون القائمة، وهي نفسها تُستخدم للتنقل داخل التفاصيل
fcount = {k: sum(by_state[s] for s in v[1]) for k, v in FILTERS.items()}
options = [k for k in FILTERS if fcount[k] or k in ("todo", "all")]
if ss["flt"] not in options:
    ss["flt"] = "todo" if fcount["todo"] else "all"
items = [r for r in rows if r["state"] in FILTERS[ss["flt"]][1]]
if ss["q"]:
    q = ss["q"]
    items = [r for r in items if q in r["message"] or q in (r["transaction_id"] or "") or q in (r["source_id"] or "")]
items.sort(key=sort_key)


def run(ids, verb):
    """يعالج الشكاوى ويعرض التقدم. كل شكوى تنحفظ أول ما تخلص، فالتوقف بالنص ما يضيع شي."""
    bar = st.progress(0.0, text=f"{verb}… 0 من {len(ids)}")
    seen = Counter()

    def cb(i, n, cid, status):
        seen[status] += 1
        bar.progress(i / n, text=f"{verb}… {i} من {n}" + (f" · تعذّرت {seen['failed']}" if seen["failed"] else ""))

    s = pipeline.process(ids, on_progress=cb)
    toast(f"خلصت المعالجة: {s['done']} نجحت" + (f"، و{s['failed']} تعذّرت" if s["failed"] else "") + ".")
    st.rerun()


def start_review():
    ss.update(flt="todo", q="", page=0, view=to_review[0]["complaint_id"])


# ================================================================== الجدول
def list_view():
    counts = {"review": by_state["review"] + by_state["flag"],
              **{k: by_state[k] for k in ("approved", "followup", "pending", "failed")}}
    total = max(1, len(rows))
    st.markdown('<div class="track">'
                + "".join(f'<span style="width:{counts[k] / total * 100:.2f}%;background:{c}"></span>' for k, _, c in SUMMARY)
                + '</div><div class="summary"><span><b>' + str(len(rows)) + '</b> شكوى</span>'
                + "".join(f'<span><i style="background:{c}"></i>{lbl} <b>{counts[k]}</b></span>' for k, lbl, c in SUMMARY if counts[k])
                + '</div>', unsafe_allow_html=True)

    # الخطوة التالية: سطر لكل شي ينتظر الموظف، بزر واحد
    no_key = f' <span class="muted">· يحتاج مفتاح الموديل ({esc(key_env)} بملف .env)</span>'
    nxt = st.container(key="steps")
    if pending:
        with nxt.container(key="next-pending"):
            a, b = st.columns([5, 1], vertical_alignment="center")
            a.markdown(f'<div class="next"><b>{len(pending)} شكوى بانتظار المعالجة.</b>'
                       + (' <span class="muted">كل شكوى تنحفظ أول ما تخلص.</span>' if connected else no_key) + '</div>',
                       unsafe_allow_html=True)
            if b.button("ابدأ المعالجة", type="primary", disabled=not connected, width="stretch"):
                run([r["complaint_id"] for r in pending], "جاري المعالجة")
    if failed:
        top = Counter(short_error(r["error"]) for r in failed).most_common(1)[0][0]
        with nxt.container(key="next-failed"):
            a, b, c = st.columns([4.4, 0.6, 1], vertical_alignment="center")
            a.markdown(f'<div class="next"><b>{len(failed)} شكوى تعذّرت معالجتها.</b> {esc(top)}'
                       + ('' if connected else no_key) + '</div>', unsafe_allow_html=True)
            with b.popover("التفاصيل", type="tertiary"):
                st.markdown(f"آخر محاولة: {fmt_time(max(r['updated_at'] for r in failed))}  \n"
                            "إعادة المحاولة تعالج الشكاوى الفاشلة بس. المراجعات والنتائج السابقة تبقى مثل ما هي.")
                st.caption("نص الخطأ من آخر شكوى فشلت: " + (max(failed, key=lambda r: r["updated_at"])["error"] or ""))
            if c.button("إعادة المحاولة", type="primary", disabled=not connected, width="stretch"):
                run([r["complaint_id"] for r in failed], "إعادة المحاولة")
    if to_review:
        with nxt.container(key="next-review"):
            a, b = st.columns([5, 1], vertical_alignment="center")
            n_flag = by_state["flag"]
            a.markdown(f'<div class="next"><b>{len(to_review)} شكوى بانتظار مراجعتك.</b>'
                       + (f' <span class="muted">منها {n_flag} تحتاج انتباه.</span>' if n_flag else '') + '</div>',
                       unsafe_allow_html=True)
            b.button("ابدأ المراجعة", type="primary", width="stretch", on_click=start_review)

    # البحث والفلتر
    f1, f2 = st.columns([3, 2], vertical_alignment="center")
    ss["w_flt"] = ss["flt"]
    f1.segmented_control("الحالة", options, format_func=lambda k: f"{FILTERS[k][0]} {fcount[k]}",
                         key="w_flt", on_change=set_flt, label_visibility="collapsed")
    ss["w_q"] = ss["q"]
    f2.text_input("بحث", key="w_q", on_change=set_q, placeholder="بحث بالنص أو رقم العملية أو المعرّف",
                  label_visibility="collapsed", icon=":material/search:")

    cols = [1.1, 5.2, 1.5, 0.9, 1.5]
    with st.container(key="thead"):
        for c, t in zip(st.columns(cols), ["المعرّف", "الرسالة", "التصنيف", "الأولوية", "الحالة"]):
            c.markdown(f'<div class="th">{t}</div>', unsafe_allow_html=True)
    if not items:
        st.markdown('<div class="empty">ما اكو شكاوى تطابق البحث أو الفلتر.</div>', unsafe_allow_html=True)
        return

    pages = math.ceil(len(items) / PAGE_SIZE)
    page = min(ss["page"], pages - 1)
    tbody = st.container(key="tbody")
    for r in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        cid = r["complaint_id"]
        done = r["status"] == "done"
        cat = CAT[r["corrected_category"] or r["category"]] if done else "—"
        with tbody.container(key=f"tr-{cid}"):
            c = st.columns(cols, vertical_alignment="center")
            c[0].markdown(f'<div class="cell id">{esc(r["source_id"] or cid)}</div>', unsafe_allow_html=True)
            c[1].button(r["message"].replace("\n", " ")[:140], key=f"open-{cid}", type="tertiary",
                        on_click=open_complaint, args=(cid,))
            c[2].markdown(f'<div class="cell{"" if done else " muted"}">{esc(cat)}</div>', unsafe_allow_html=True)
            pri = PRI[r["priority"]] if done else "—"
            c[3].markdown(f'<div class="cell {"high" if r["priority"] == "high" else "muted"}">{pri}</div>', unsafe_allow_html=True)
            c[4].markdown(f'<span class="st {r["state"]}">{STATES[r["state"]]}</span>', unsafe_allow_html=True)

    if pages > 1:
        p1, p2, p3 = st.columns([1, 2, 1])
        if p1.button("السابق", disabled=page == 0, width="stretch", icon=":material/chevron_right:"):
            ss["page"] = page - 1
            st.rerun()
        p2.markdown(f'<div class="pager">صفحة {page + 1} من {pages} · {len(items)} شكوى</div>', unsafe_allow_html=True)
        if p3.button("التالي", disabled=page == pages - 1, width="stretch", icon=":material/chevron_left:",
                     icon_position="right"):
            ss["page"] = page + 1
            st.rerun()


# ================================================================== التفاصيل
def detail_view(cid):
    r = next(x for x in rows if x["complaint_id"] == cid)
    s = r["state"]
    ids = [x["complaint_id"] for x in items]
    pos = ids.index(cid) if cid in ids else None

    with st.container(key="nav"):
        nav = st.columns([1.4, 4.6, 0.9, 1.1, 0.9], vertical_alignment="center")
    nav[0].button("رجوع للقائمة", icon=":material/arrow_forward:", on_click=back, width="stretch")
    if pos is not None:
        nav[2].button("السابق", disabled=pos == 0, on_click=open_complaint, args=(ids[pos - 1] if pos else cid,),
                      width="stretch", key="prev")
        nav[3].markdown(f'<div class="pos">{pos + 1} من {len(ids)} · {esc(FILTERS[ss["flt"]][0])}</div>',
                        unsafe_allow_html=True)
        nav[4].button("التالي", disabled=pos == len(ids) - 1, on_click=open_complaint,
                      args=(ids[pos + 1] if pos < len(ids) - 1 else cid,), width="stretch", key="next")

    meta = [x for x in (r["channel"], fmt_time(r["received_at"]), f"السطر {r['row_number']} بالملف") if x]
    st.markdown(f'<div class="d-head" style="margin-top:.8rem"><span class="d-id">{esc(r["source_id"] or cid)}</span>'
                f'<span class="st {s}">{STATES[s]}</span><span class="d-meta">{esc(" · ".join(meta))}</span></div>',
                unsafe_allow_html=True)

    if s == "flag":
        st.markdown('<div class="flagline"><b>انتبه قبل الاعتماد:</b> '
                    + "، ".join(esc(REASONS.get(x, x)) for x in r["review_reasons"]) + '.</div>', unsafe_allow_html=True)
    elif s == "failed":
        st.markdown(f'<div class="failline"><b>تعذّرت المعالجة:</b> {esc(short_error(r["error"]))} '
                    f'آخر محاولة {esc(fmt_time(r["updated_at"]))}.</div>', unsafe_allow_html=True)
    elif s == "pending":
        st.markdown('<div class="quietline">ما انعالجت بعد. ابدأ المعالجة من القائمة.</div>', unsafe_allow_html=True)
    elif s == "approved":
        st.markdown(f'<div class="okline">اعتمدت الرد {esc(fmt_time(r["reviewed_at"]))}. '
                    'الإرسال للزبون يصير يدوياً من نظام الدعم.</div>', unsafe_allow_html=True)
    elif s == "followup":
        st.markdown(f'<div class="quietline">تركتها للمتابعة {esc(fmt_time(r["reviewed_at"]))}.</div>', unsafe_allow_html=True)

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="sec">رسالة الزبون</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="msg">{esc(r["message"])}</div>', unsafe_allow_html=True)

        if r["status"] == "done":
            cat = r["corrected_category"] or r["category"]
            missing = set(r["missing_fields"])

            def fact(k, v, field=None, cls=""):
                if v in (None, ""):
                    v = '<span class="none">' + ("ما مذكور، والمسودة تطلبه" if field in missing else "ما مذكور") + '</span>'
                else:
                    v = f'<span class="v {cls}">{esc(v)}</span>'
                return f'<div><span class="k">{k}</span>{v}</div>'

            st.markdown('<div class="sec">نتيجة المعالجة</div>', unsafe_allow_html=True)
            st.markdown('<div class="facts">'
                        + fact("نوع المشكلة", CAT[cat] + (" (صحّحته)" if r["corrected_category"] else ""))
                        + fact("الأولوية", PRI[r["priority"]], cls="high" if r["priority"] == "high" else "")
                        + fact("القسم", config.ROUTING[cat])
                        + fact("رقم العملية", r["transaction_id"], "transaction_id")
                        + fact("المبلغ", config.format_amount(r["amount"]), "amount")
                        + fact("تاريخ المشكلة", r["date"], "date")
                        + '</div><div class="why">'
                        + (f'<b>سبب التصنيف:</b> {esc(r["reason"])} · ' if r["reason"] else '')
                        + f'الثقة {r["confidence"] * 100:.0f}%</div>', unsafe_allow_html=True)

        with st.expander("تفاصيل تقنية"):
            tech = [f"المعرّف الداخلي: {cid}", f"المحاولات: {r['attempts']}", f"آخر تحديث: {fmt_time(r['updated_at'])}"]
            if r["model"]:
                tech += [f"الموديل: {r['model']} · نسخة التعليمات {r['prompt_version']}",
                         f"الزمن: {r['latency_ms']} ms · توكنات: {r['input_tokens']} دخول، {r['output_tokens']} خروج"]
            if r["review_reasons"]:
                tech.append("رموز المراجعة: " + "، ".join(r["review_reasons"]))
            st.caption("  \n".join(tech))
            if r["error"]:
                st.caption("نص الخطأ: " + r["error"])

    with right:
        if r["status"] == "failed":
            st.markdown('<div class="sec">الخطوة التالية</div>', unsafe_allow_html=True)
            if st.button("إعادة المحاولة لهذي الشكوى", type="primary", disabled=not connected, key=f"retry_{cid}"):
                run([cid], "إعادة المحاولة")
            st.markdown('<div class="hint">تعيد معالجة هذي الشكوى بس. الباقي يبقى مثل ما هو.</div>', unsafe_allow_html=True)
            return
        if r["status"] != "done":
            return
        decision_panel(r)


def decision_panel(r):
    cid, s = r["complaint_id"], r["state"]
    cat = r["corrected_category"] or r["category"]
    st.markdown('<div class="sec">مسودة الرد</div>', unsafe_allow_html=True)
    key = f"draft_{cid}"
    saved = r["final_draft"] or r["draft"] or ""
    if f"new_{key}" in ss:  # مسودة جديدة من "إعادة كتابة"
        ss[key] = ss.pop(f"new_{key}")
    elif key not in ss:
        ss[key] = saved
    st.text_area("مسودة الرد", key=key, height=180, label_visibility="collapsed")
    if ss[key] != saved:
        st.markdown('<div class="unsaved">فيها تعديلات ما انحفظت بعد.</div>', unsafe_allow_html=True)

    cat_keys = list(CAT)
    with st.expander("تصحيح النوع أو إضافة ملاحظة", expanded=bool(r["corrected_category"] or r["note"])):
        new_cat = st.selectbox("نوع المشكلة", cat_keys, index=cat_keys.index(cat), format_func=CAT.get, key=f"cat_{cid}")
        note = st.text_input("ملاحظة داخلية", value=r["note"] or "", key=f"note_{cid}")

    if err := ss.pop(f"err_{cid}", None):
        st.markdown(f'<div class="failline" style="margin:0 0 .6rem">{esc(err)}</div>', unsafe_allow_html=True)

    def decide(decision, text):
        with db.connect() as conn:
            db.save_review(conn, cid, decision, text, new_cat if new_cat != r["category"] else None, note or None)
        # بعد القرار تنفتح الشكوى الجاية اللي تنتظر مراجعة، وإذا ماكو نرجع للقائمة
        ids = [x["complaint_id"] for x in to_review]
        after = ids[ids.index(cid) + 1:] + ids[:ids.index(cid)] if cid in ids else ids
        ss["view"] = after[0] if after else None

    b1, b2, b3 = st.columns([1.2, 1, 1])
    text = ss[key]
    if b1.button("اعتماد الرد" if s != "approved" else "حفظ التعديل", type="primary", width="stretch", key=f"ok_{cid}"):
        found = pipeline.check_draft(text)
        if not text.strip():
            ss[f"err_{cid}"] = "المسودة فارغة."
        elif found:
            ss[f"err_{cid}"] = "المسودة بيها عبارة ممنوعة بدليل الدعم: " + "، ".join(found) + ". عدّلها وأعد الاعتماد."
        else:
            decide("approved", text)
            toast("انعتمد الرد. ما انرسل شي للزبون.")
        st.rerun()
    if b2.button("أتركها للمتابعة", width="stretch", key=f"fu_{cid}", disabled=s == "followup"):
        decide("needs_followup", text)
        toast("انحفظت للمتابعة مع المسودة الحالية.")
        st.rerun()
    if b3.button("إعادة كتابة", width="stretch", disabled=not connected, key=f"re_{cid}",
                 help="يكتب مسودة جديدة حسب النوع المختار"):
        if new_cat != r["category"]:
            with db.connect() as conn:
                db.save_review(conn, cid, r["decision"] or "needs_followup", text, new_cat, note or None)
        with st.spinner("جاري كتابة مسودة جديدة…"):
            try:
                draft, found = pipeline.regenerate_draft(cid)
                ss[f"new_{key}"] = draft
                if found:
                    ss[f"err_{cid}"] = "المسودة الجديدة بيها عبارة ممنوعة، عدّلها قبل الاعتماد."
            except llm.LLMError as e:
                ss[f"err_{cid}"] = f"تعذّرت إعادة الكتابة: {short_error(str(e))}"
        st.rerun()
    st.markdown('<div class="hint">الاعتماد يحفظ الرد جاهز للإرسال اليدوي. مسار ما يرسل أي شي للزبون.</div>',
                unsafe_allow_html=True)


if ss["view"] and any(r["complaint_id"] == ss["view"] for r in rows):
    detail_view(ss["view"])
else:
    ss["view"] = None
    list_view()
