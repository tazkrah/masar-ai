"""شاشة الموظف — مسار.

تشغيل:  ./run.sh
"""
import html
import re
from collections import Counter
from datetime import datetime

import streamlit as st

from masar import config, db, ingest, llm, pipeline

st.set_page_config(page_title="مسار — شكاوى الزبائن", page_icon="◆", layout="wide")
db.init()

CSS = """
<link href="https://fonts.googleapis.com/css2?family=Almarai:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root { --teal:#0E7C86; --teal-soft:#E7F2F3; --ink:#1C2530; --muted:#5B6773; --line:#E1E7E8; --soft:#F2F6F6;
        --amber:#9A6200; --amber-bg:#FBF4E6; --amber-line:#EED9AE; --red:#B42318; --red-bg:#FCEEEC; --red-line:#F2CBC6; }
html, body, .stApp, .stMarkdown, button, input, textarea, select, label, [data-testid="stWidgetLabel"] p {
  font-family: 'Almarai', sans-serif !important; }
.stApp { direction: rtl; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { direction: rtl; border-left: 1px solid var(--line); }
[data-testid="stSidebar"] h4 { font-size: .95rem; margin: .4rem 0 .2rem; }
.block-container { padding-top: 2.4rem; max-width: 1240px; }
h1, h2, h3, h4 { color: var(--ink); font-weight: 800; letter-spacing: 0; }
.brand { display:flex; align-items:baseline; gap:.6rem; margin-bottom: 1.6rem; }
.brand .name { color: var(--teal); font-size: 1.9rem; font-weight: 800; line-height: 1; }
.brand .tag { color: var(--ink); font-size: 1rem; font-weight: 700; }
.batch-title { font-size: 1.35rem; font-weight: 800; color: var(--ink); margin: 0; }
.batch-meta { color: var(--muted); font-size: .88rem; margin: .25rem 0 .9rem; }
.track { display:flex; height: 6px; border-radius: 3px; overflow: hidden; background: var(--soft); }
.track span { display:block; height:100%; }
.legend { display:flex; flex-wrap:wrap; gap: .3rem 1.2rem; font-size: .84rem; color: var(--muted); margin: .55rem 0 1.4rem; }
.legend i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-left:.4rem; }
.legend b { color: var(--ink); margin-right: .25rem; font-variant-numeric: tabular-nums; }
.band-text { font-size: .95rem; color: var(--ink); line-height: 1.7; padding-top: .25rem; }
.band-text .sub { color: var(--muted); font-size: .86rem; display:block; }
.st-key-band-failed { border-color: var(--red-line) !important; background: var(--red-bg); }
.st-key-band-pending { background: var(--soft); border-color: var(--line) !important; }
.sec { font-size: .8rem; color: var(--muted); font-weight: 700; margin: 1.1rem 0 .45rem; }
.d-head { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.d-id { font-size: 1.15rem; font-weight: 800; color: var(--ink); }
.d-meta { color: var(--muted); font-size: .86rem; margin-top: .2rem; }
.chip { display:inline-block; font-size:.78rem; font-weight:700; padding:.15rem .6rem; border-radius:999px; }
.chip.review { background: var(--teal-soft); color: var(--teal); }
.chip.flag { background: var(--amber-bg); color: var(--amber); }
.chip.failed { background: var(--red-bg); color: var(--red); }
.chip.pending, .chip.followup { background: var(--soft); color: var(--muted); }
.chip.approved { background: var(--teal); color: #fff; }
.msg { background: var(--soft); border-right: 3px solid var(--teal); padding: .85rem 1rem; border-radius: 6px;
       font-size: 1.05rem; line-height: 1.85; color: var(--ink); white-space: pre-wrap; }
.facts { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .55rem 1.5rem; font-size: .93rem; }
.facts .k { color: var(--muted); font-size: .8rem; display:block; }
.facts .v { color: var(--ink); font-weight: 700; }
.facts .none { color: var(--muted); font-weight: 400; }
.facts .high { color: var(--red); }
.why { color: var(--muted); font-size: .88rem; margin-top: .8rem; line-height: 1.7; }
.alert { border-radius: 6px; padding: .7rem .9rem; font-size: .9rem; margin: .9rem 0 0; line-height: 1.7; border: 1px solid; }
.alert b { display:block; margin-bottom: .1rem; }
.alert ul { margin: .1rem 0 0; padding: 0; list-style: none; }
.alert li::before { content: "•"; margin-left: .45rem; }
.alert.flag { background: var(--amber-bg); border-color: var(--amber-line); color: #4A3500; }
.alert.failed { background: var(--red-bg); border-color: var(--red-line); color: #5C150F; }
.alert.done { background: var(--teal-soft); border-color: #C5E1E3; color: #0B4F55; }
.alert.quiet { background: #fff; border-color: var(--line); color: var(--muted); }
.hint { color: var(--muted); font-size: .8rem; margin-top: .35rem; }
.unsaved { color: var(--amber); font-size: .8rem; }
/* قائمة الشكاوى: كل سطر زر */
[class*="st-key-row-"] button { justify-content: flex-start; text-align: right; width: 100%; min-height: 0;
  border: none; border-bottom: 1px solid var(--line); border-radius: 0; padding: .7rem .75rem; background: transparent; }
[class*="st-key-row-"] button:hover { background: var(--soft); color: var(--ink); }
[class*="st-key-row-"] button:focus:not(:active) { border-color: var(--line); color: var(--ink); }
[class*="st-key-row-"] button p { text-align: right; font-size: .9rem; line-height: 1.6; }
[class*="st-key-row-"] button p strong { font-size: .78rem; }
[class*="st-key-row-cur-"] button { background: var(--teal-soft); box-shadow: inset -3px 0 0 var(--teal); }
[class*="st-key-row-"] [data-testid="stElementContainer"] { margin: 0; }
.st-key-list { border-top: 1px solid var(--line); gap: 0; }
.st-key-list [data-testid="stVerticalBlock"] { gap: 0; }
.stButton button { border-radius: 6px; font-weight: 700; }
[data-testid="stFileUploaderDropzoneInstructions"] { direction: rtl; }
[data-testid="stExpander"] summary p { font-size: .88rem; color: var(--muted); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

CAT = config.CATEGORIES
PRI = config.PRIORITIES
REASONS = config.REVIEW_REASON_LABELS
MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
          "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]

# حالة كل شكوى بالشاشة، بترتيب العرض بالقائمة
STATES = {
    "flag": "تحتاج مراجعة",
    "review": "جاهزة للمراجعة",
    "failed": "تعذّرت المعالجة",
    "pending": "بانتظار المعالجة",
    "followup": "للمتابعة",
    "approved": "معتمدة",
}
STATE_MD = {"flag": "orange", "review": "primary", "failed": "red", "pending": "gray", "followup": "gray", "approved": "gray"}
TRACK = [("approved", "معتمدة", "#0E7C86"), ("followup", "للمتابعة", "#5BA8AE"),
         ("review", "بانتظار المراجعة", "#B9D9DC"), ("pending", "بانتظار المعالجة", "#DDE3E4"),
         ("failed", "تعذّرت", "#B42318")]


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


def toast(msg):
    st.session_state["toast"] = msg


connected = llm.key_status()[1]
key_env = llm.key_status()[0]

st.markdown('<div class="brand"><span class="name">مسار</span><span class="tag">تنظيم شكاوى الزبائن</span></div>',
            unsafe_allow_html=True)
if "toast" in st.session_state:
    st.toast(st.session_state.pop("toast"))

with db.connect() as conn:
    batches = db.list_batches(conn)


# ------------------------------------------------------------------ رفع ملف
def upload_panel():
    n = st.session_state.setdefault("up_n", 0)
    up = st.file_uploader("ملف CSV بيه عمود الرسالة (message)", type=["csv"], key=f"up_{n}")
    if up is None:
        return
    try:
        rows, skipped = ingest.parse_csv(up.getvalue())
    except ingest.CSVError as e:
        st.markdown(f'<div class="alert failed"><b>ما نگدر نقرأ الملف</b>{esc(e)}</div>', unsafe_allow_html=True)
        return
    text = f"{len(rows)} شكوى جاهزة للحفظ"
    if skipped:
        text += f"، و{len(skipped)} سطر راح ينترك"
    st.markdown(f'<div class="hint" style="font-size:.88rem;color:var(--ink)">{text}.</div>', unsafe_allow_html=True)
    if skipped:
        with st.expander("الأسطر المتروكة"):
            for s in skipped:
                st.write(f"السطر {s['row']}: {s['reason']}")
    if st.button("حفظ الدفعة", type="primary", width="stretch", disabled=not rows):
        with db.connect() as conn:
            bid, new = db.create_batch(conn, up.name, rows, skipped)
        st.session_state["batch"] = bid
        st.session_state["up_n"] = n + 1  # يفرّغ حقل الرفع حتى ما تنحفظ نفس الدفعة مرتين
        old = len(rows) - new
        toast(f"انحفظت الدفعة: {new} شكوى جديدة" + (f"، و{old} موجودة من قبل وما تكررت" if old else "") + ".")
        st.rerun()


if not batches:
    st.markdown("#### ابدأ بملف الشكاوى")
    st.markdown('<div class="batch-meta">ملف CSV بيه عمود <code>message</code> للرسالة. '
                'الأعمدة <code>complaint_id</code> و<code>received_at</code> و<code>channel</code> اختيارية.</div>',
                unsafe_allow_html=True)
    c, _ = st.columns([2, 3])
    with c:
        upload_panel()
    st.stop()

with st.sidebar:
    st.markdown("#### الدفعات")
    labels = {b["id"]: f"{b['filename']} · {fmt_time(b['uploaded_at'])}" for b in batches}
    ids = list(labels)
    cur_b = st.session_state.get("batch", ids[0])
    st.session_state["batch"] = st.radio("الدفعات", ids, index=ids.index(cur_b) if cur_b in ids else 0,
                                         format_func=labels.get, label_visibility="collapsed")
    st.markdown("#### دفعة جديدة")
    upload_panel()
    st.divider()
    st.caption(f"الموديل: {config.MODEL} · " + ("متصل" if connected else f"غير متصل، ناقص {key_env}"))

batch_id = st.session_state["batch"]
batch = next(b for b in batches if b["id"] == batch_id)
with db.connect() as conn:
    rows = db.batch_rows(conn, batch_id)
for r in rows:
    r["state"] = state(r)
by_state = Counter(r["state"] for r in rows)
pending = [r for r in rows if r["state"] == "pending"]
failed = [r for r in rows if r["state"] == "failed"]


# ------------------------------------------------------------------ رأس الدفعة
decided = by_state["approved"] + by_state["followup"]
st.markdown(f'<p class="batch-title">{esc(batch["filename"])}</p>'
            f'<div class="batch-meta">رُفعت {esc(fmt_time(batch["uploaded_at"]))} · {len(rows)} شكوى'
            + (f' · {batch["rows_skipped"]} سطر متروك من الملف' if batch["rows_skipped"] else '')
            + f' · راجعت {decided} من {len(rows)}</div>', unsafe_allow_html=True)
counts = {"review": by_state["review"] + by_state["flag"], **{k: by_state[k] for k in ("approved", "followup", "pending", "failed")}}
total = max(1, len(rows))
st.markdown('<div class="track">'
            + "".join(f'<span style="width:{counts[k] / total * 100:.2f}%;background:{c}"></span>' for k, _, c in TRACK)
            + '</div><div class="legend">'
            + "".join(f'<span><i style="background:{c}"></i>{lbl}<b>{counts[k]}</b></span>'
                      for k, lbl, c in TRACK if counts[k])
            + '</div>', unsafe_allow_html=True)


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


# ------------------------------------------------------------------ شغل ينتظر الموظف
no_key = (f'<span class="sub">المعالجة تحتاج مفتاح الموديل: أضف <code>{esc(key_env)}</code> بملف '
          f'<code>.env</code> وأعد تشغيل البرنامج.</span>')
if pending:
    with st.container(border=True, key="band-pending"):
        a, b = st.columns([4, 1], vertical_alignment="center")
        a.markdown(f'<div class="band-text"><b>{len(pending)} شكوى بانتظار المعالجة.</b>'
                   + (' <span class="sub">كل شكوى تنحفظ أول ما تخلص، فتگدر توقف وترجع تكمّل.</span>' if connected else no_key)
                   + '</div>', unsafe_allow_html=True)
        go = b.button("ابدأ المعالجة", type="primary", disabled=not connected, width="stretch")
        if go:
            run([r["complaint_id"] for r in pending], "جاري المعالجة")

if failed:
    top, n_top = Counter(short_error(r["error"]) for r in failed).most_common(1)[0]
    last = max(r["updated_at"] for r in failed)
    with st.container(border=True, key="band-failed"):
        a, b = st.columns([4, 1], vertical_alignment="center")
        reason = f"{esc(top)}" + ("" if n_top == len(failed) else f" ({n_top} من {len(failed)})")
        a.markdown(f'<div class="band-text"><b>{len(failed)} شكوى تعذّرت معالجتها.</b> {reason}'
                   + (f'<span class="sub">آخر محاولة {esc(fmt_time(last))}. إعادة المحاولة تعالج هذي الشكاوى بس، '
                      f'وكل شي راجعته يبقى مثل ما هو.</span>' if connected else no_key)
                   + '</div>', unsafe_allow_html=True)
        if b.button("إعادة المحاولة", type="primary", disabled=not connected, width="stretch"):
            run([r["complaint_id"] for r in failed], "إعادة المحاولة")


# ------------------------------------------------------------------ القائمة والتفاصيل
FILTERS = {
    "todo": ("للمراجعة", ("flag", "review")),
    "followup": ("للمتابعة", ("followup",)),
    "approved": ("معتمدة", ("approved",)),
    "failed": ("تعذّرت", ("failed",)),
    "all": ("الكل", tuple(STATES)),
}
fcount = {k: sum(by_state[s] for s in v[1]) for k, v in FILTERS.items()}
options = [k for k in FILTERS if fcount[k] or k in ("todo", "all")]
order = list(STATES)
pri_order = {"high": 0, "medium": 1, "low": 2, None: 3}

list_col, detail_col = st.columns([2, 3], gap="large")

with list_col:
    flt = st.segmented_control("عرض", options, default="todo" if fcount["todo"] else "all",
                               format_func=lambda k: f"{FILTERS[k][0]} {fcount[k]}",
                               key=f"flt_{batch_id}_{'-'.join(options)}", label_visibility="collapsed") or "all"
    q = st.text_input("بحث", placeholder="بحث بنص الشكوى أو رقم العملية", label_visibility="collapsed")

    items = [r for r in rows if r["state"] in FILTERS[flt][1]]
    if q:
        items = [r for r in items if q in r["message"] or q in (r["transaction_id"] or "") or q in (r["source_id"] or "")]
    items.sort(key=lambda r: (order.index(r["state"]), pri_order[r["priority"]], r["row_number"]))

    sel_key = f"cur_{batch_id}"
    ids_in_list = [r["complaint_id"] for r in items]
    # الشكوى المفتوحة لازم تكون من القائمة الظاهرة، إلا إذا القائمة فارغة
    if ids_in_list and st.session_state.get(sel_key) not in ids_in_list:
        st.session_state[sel_key] = ids_in_list[0]
    elif st.session_state.get(sel_key) not in {r["complaint_id"] for r in rows}:
        st.session_state[sel_key] = rows[0]["complaint_id"]

    def pick(cid):
        st.session_state[sel_key] = cid

    if not items:
        st.markdown('<div class="hint">ما اكو شكاوى هنا.</div>', unsafe_allow_html=True)
    with st.container(height=640 if len(items) > 9 else "content", border=False, key="list"):
        for r in items:
            s = r["state"]
            meta = ""
            if r["status"] == "done":
                meta = CAT[r["corrected_category"] or r["category"]] + (" · أولوية عالية" if r["priority"] == "high" else "")
            preview = r["message"].replace("\n", " ")
            preview = preview[:78] + ("…" if len(preview) > 78 else "")
            label = f"**:{STATE_MD[s]}[{STATES[s]}]**" + (f" :gray[· {md_esc(meta)}]" if meta else "") + f"  \n{md_esc(preview)}"
            cur = r["complaint_id"] == st.session_state[sel_key]
            st.button(label, key=f"row-{'cur-' if cur else ''}{r['complaint_id']}", on_click=pick, args=(r["complaint_id"],),
                      width="stretch")

r = next(x for x in rows if x["complaint_id"] == st.session_state[sel_key])
cid = r["complaint_id"]
s = r["state"]
# الشكوى اللي تنفتح بعد القرار: الجاية بالقائمة اللي بعدها تنتظر مراجعة
queue = [x["complaint_id"] for x in sorted(rows, key=lambda x: (order.index(x["state"]), pri_order[x["priority"]], x["row_number"]))
         if x["state"] in ("flag", "review") and x["complaint_id"] != cid]


def decide(decision, text, new_cat, note):
    with db.connect() as conn:
        db.save_review(conn, cid, decision, text, new_cat if new_cat != r["category"] else None, note or None)
    if queue:
        st.session_state[sel_key] = queue[0]


with detail_col:
    meta = [x for x in (r["channel"], fmt_time(r["received_at"]), f"السطر {r['row_number']} بالملف") if x]
    st.markdown(f'<div class="d-head"><span class="d-id">{esc(r["source_id"] or cid)}</span>'
                f'<span class="chip {s}">{STATES[s]}</span></div>'
                f'<div class="d-meta">{esc(" · ".join(meta))}</div>', unsafe_allow_html=True)

    if s == "flag":
        st.markdown('<div class="alert flag"><b>انتبه قبل الاعتماد</b><ul>'
                    + "".join(f"<li>{esc(REASONS.get(x, x))}</li>" for x in r["review_reasons"]) + "</ul></div>",
                    unsafe_allow_html=True)
    elif s == "failed":
        st.markdown(f'<div class="alert failed"><b>تعذّرت المعالجة</b>{esc(short_error(r["error"]))}<br>'
                    f'<span style="font-size:.84rem">المحاولات: {r["attempts"]} · آخرها {esc(fmt_time(r["updated_at"]))}</span></div>',
                    unsafe_allow_html=True)
    elif s == "pending":
        st.markdown('<div class="alert quiet">ما انعالجت بعد. ابدأ المعالجة من الأعلى.</div>', unsafe_allow_html=True)
    elif s in ("approved", "followup"):
        what = "اعتمدت الرد" if s == "approved" else "حوّلتها للمتابعة"
        extra = " الإرسال للزبون يصير يدوياً من نظام الدعم." if s == "approved" else ""
        st.markdown(f'<div class="alert {"done" if s == "approved" else "quiet"}">{what} {esc(fmt_time(r["reviewed_at"]))}.{extra}</div>',
                    unsafe_allow_html=True)

    st.markdown('<div class="sec">رسالة الزبون</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="msg">{esc(r["message"])}</div>', unsafe_allow_html=True)

    if r["status"] == "failed":
        if st.button("إعادة المحاولة لهذي الشكوى", disabled=not connected, key=f"retry_{cid}"):
            run([cid], "إعادة المحاولة")
        with st.expander("نص الخطأ الكامل"):
            st.code(r["error"] or "", language=None, wrap_lines=True)
    if r["status"] != "done":
        st.stop()

    # ---------------- النتيجة
    cat = r["corrected_category"] or r["category"]
    missing = set(r["missing_fields"])

    def fact(k, v, field=None, cls=""):
        if v in (None, ""):
            v = '<span class="none">' + ("ما مذكور، والمسودة تطلبه" if field in missing else "ما مذكور") + '</span>'
        else:
            v = f'<span class="v {cls}">{esc(v)}</span>'
        return f'<div><span class="k">{k}</span>{v}</div>'

    st.markdown('<div class="sec">نتيجة مسار</div>', unsafe_allow_html=True)
    st.markdown('<div class="facts">'
                + fact("نوع المشكلة", CAT[cat] + (" (صحّحته)" if r["corrected_category"] else ""))
                + fact("الأولوية", PRI[r["priority"]], cls="high" if r["priority"] == "high" else "")
                + fact("القسم المسؤول", config.ROUTING[cat])
                + fact("الثقة", f"{r['confidence'] * 100:.0f}%")
                + fact("رقم العملية", r["transaction_id"], "transaction_id")
                + fact("المبلغ", config.format_amount(r["amount"]), "amount")
                + fact("تاريخ المشكلة", r["date"], "date")
                + '</div>'
                + (f'<div class="why"><b>سبب التصنيف:</b> {esc(r["reason"])}</div>' if r["reason"] else ""),
                unsafe_allow_html=True)

    # ---------------- المسودة
    st.markdown('<div class="sec">مسودة الرد</div>', unsafe_allow_html=True)
    key = f"draft_{cid}"
    saved = r["final_draft"] or r["draft"] or ""
    if f"new_{key}" in st.session_state:  # مسودة جديدة من "إعادة كتابة"
        st.session_state[key] = st.session_state.pop(f"new_{key}")
    elif key not in st.session_state:
        st.session_state[key] = saved
    st.text_area("مسودة الرد", key=key, height=140, label_visibility="collapsed")
    if st.session_state[key] != saved:
        st.markdown('<div class="unsaved">فيها تعديلات ما انحفظت بعد.</div>', unsafe_allow_html=True)

    cat_keys = list(CAT)
    with st.expander("تصحيح النوع أو إضافة ملاحظة", expanded=bool(r["corrected_category"] or r["note"])):
        new_cat = st.selectbox("نوع المشكلة", cat_keys, index=cat_keys.index(cat), format_func=CAT.get, key=f"cat_{cid}")
        note = st.text_input("ملاحظة داخلية", value=r["note"] or "", key=f"note_{cid}")

    if err := st.session_state.pop(f"err_{cid}", None):
        st.markdown(f'<div class="alert failed" style="margin-bottom:.6rem">{esc(err)}</div>', unsafe_allow_html=True)

    b1, b2, b3 = st.columns([1.2, 1, 1])
    text = st.session_state[key]
    if b1.button("اعتماد الرد" if s != "approved" else "حفظ التعديل", type="primary", width="stretch", key=f"ok_{cid}"):
        found = pipeline.check_draft(text)
        if not text.strip():
            st.session_state[f"err_{cid}"] = "المسودة فارغة."
        elif found:
            st.session_state[f"err_{cid}"] = "المسودة بيها عبارة ممنوعة بدليل الدعم: " + "، ".join(found) + ". عدّلها وأعد الاعتماد."
        else:
            decide("approved", text, new_cat, note)
            toast("انعتمد الرد. ما انرسل شي للزبون.")
        st.rerun()
    if b2.button("أتركها للمتابعة", width="stretch", key=f"fu_{cid}", disabled=s == "followup"):
        decide("needs_followup", text, new_cat, note)
        toast("انحفظت للمتابعة مع المسودة الحالية.")
        st.rerun()
    if b3.button("إعادة كتابة المسودة", width="stretch", disabled=not connected, key=f"re_{cid}",
                 help="يكتب مسودة جديدة حسب النوع المختار. المسودة الحالية تنحفظ قبلها."):
        with db.connect() as conn:
            db.save_review(conn, cid, r["decision"] or "needs_followup", text,
                           new_cat if new_cat != r["category"] else None, note or None)
        with st.spinner("جاري كتابة مسودة جديدة…"):
            try:
                draft, found = pipeline.regenerate_draft(cid)
                st.session_state[f"new_{key}"] = draft
                if found:
                    st.session_state[f"err_{cid}"] = "المسودة الجديدة بيها عبارة ممنوعة، عدّلها قبل الاعتماد."
            except llm.LLMError as e:
                st.session_state[f"err_{cid}"] = f"تعذّرت إعادة الكتابة: {short_error(str(e))}"
        st.rerun()
    st.markdown('<div class="hint">الاعتماد يحفظ الرد جاهز للإرسال اليدوي. مسار ما يرسل أي شي للزبون.</div>',
                unsafe_allow_html=True)
