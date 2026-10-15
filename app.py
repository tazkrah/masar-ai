"""شاشة الموظف — مسار.

تشغيل:  .venv/bin/streamlit run app.py
"""
import html

import pandas as pd
import streamlit as st

from masar import config, db, ingest, llm, pipeline

st.set_page_config(page_title="مسار — شكاوى الزبائن", page_icon="◆", layout="wide")
db.init()

CSS = """
<link href="https://fonts.googleapis.com/css2?family=Almarai:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root { --teal:#0E7C86; --ink:#1C2530; --muted:#5B6773; --line:#E1E7E8; --soft:#F2F6F6; --teal2:#5BA8AE;
        --amber:#B7791F; --amber-bg:#FDF6E7; --red:#B42318; --red-bg:#FDECEA; }
html, body, .stApp, [class*="css"], .stMarkdown, button, input, textarea, select, label {
  font-family: 'Almarai', sans-serif !important; }
.stApp { direction: rtl; }
[data-testid="stSidebar"] { direction: rtl; border-left: 1px solid var(--line); }
.block-container { padding-top: 1.6rem; max-width: 1280px; }
h1, h2, h3 { color: var(--ink); font-weight: 800; letter-spacing: 0; }
.brand { display:flex; align-items:baseline; gap:.7rem; margin-bottom:.2rem; }
.brand .name { color: var(--teal); font-size: 2.3rem; font-weight: 800; line-height: 1; }
.brand .tag { color: var(--ink); font-size: 1.15rem; font-weight: 700; }
.sub { color: var(--muted); font-size: .92rem; margin-bottom: 1.2rem; }
.stats { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); border:1px solid var(--line);
         border-radius:8px; overflow:hidden; margin: .4rem 0 1rem; }
.stat { padding: .85rem 1rem; border-left:1px solid var(--line); background:#fff; }
.stat:last-child { border-left: none; }
.stat .v { font-size: 1.7rem; font-weight: 800; color: var(--ink); line-height:1.1; }
.stat .k { font-size: .82rem; color: var(--muted); margin-top:.2rem; }
.stat.teal .v { color: var(--teal); } .stat.amber .v { color: var(--amber); } .stat.red .v { color: var(--red); }
.bars { display:grid; grid-template-columns: 1fr 1fr; gap: 1.6rem; margin-bottom: 1.2rem; }
.bar-row { display:grid; grid-template-columns: 8.5rem 1fr 2.2rem; align-items:center; gap:.6rem; font-size:.86rem; margin:.28rem 0; }
.bar-track { background: var(--soft); height: 8px; border-radius: 4px; overflow:hidden; }
.bar-fill { background: var(--teal); height: 100%; }
.bar-fill.high { background: var(--ink); } .bar-fill.low { background: var(--teal2); }
.bar-n { color: var(--muted); text-align:left; font-variant-numeric: tabular-nums; }
.sec { font-size: .8rem; color: var(--muted); font-weight:700; margin: .2rem 0 .4rem; }
.msg { background: var(--soft); border-right: 3px solid var(--teal); padding: .9rem 1rem; border-radius: 6px;
       font-size: 1.08rem; line-height: 1.8; color: var(--ink); white-space: pre-wrap; }
.kv { display:grid; grid-template-columns: 7.5rem 1fr; row-gap:.45rem; font-size:.95rem; margin-top:.4rem; }
.kv .k { color: var(--muted); } .kv .v { font-weight: 700; color: var(--ink); }
.kv .empty { color: var(--muted); font-weight: 400; }
.pill { display:inline-block; padding: .1rem .55rem; border-radius: 999px; font-size:.8rem; font-weight:700; }
.p-high { background: var(--ink); color:#fff; } .p-medium { background: var(--soft); color: var(--ink); border:1px solid var(--line); }
.p-low { background:#fff; color: var(--muted); border:1px solid var(--line); }
.note { border:1px solid var(--line); border-radius:6px; padding:.7rem .9rem; font-size:.9rem; margin:.5rem 0; }
.note.review { background: var(--amber-bg); border-color: #F1D9A6; }
.note.fail { background: var(--red-bg); border-color: #F5C2BC; }
.note.ok { background: #EAF5F5; border-color: #BFE0E2; }
.note b { display:block; margin-bottom:.25rem; }
.note ul { margin: 0; padding-right: 1.1rem; }
.why { color: var(--muted); font-size:.9rem; margin-top:.5rem; }
.conn { font-size:.82rem; color: var(--muted); }
.conn .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-left:.35rem; }
.stButton button { border-radius: 6px; font-weight: 700; }
[data-testid="stFileUploaderDropzoneInstructions"] { direction: rtl; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

CAT = config.CATEGORIES
PRI = config.PRIORITIES
REASONS = config.REVIEW_REASON_LABELS


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def fmt_amount(v):
    return f"{v:,} د.ع" if v is not None else None


def conn_status():
    env, ok = llm.key_status()
    if ok:
        return True, f'<span class="conn"><span class="dot" style="background:#0E7C86"></span>متصل: {esc(config.MODEL)}</span>'
    return False, f'<span class="conn"><span class="dot" style="background:#B42318"></span>الموديل غير متصل — ناقص {esc(env)}</span>'


# ------------------------------------------------------------------ الرأس
connected, conn_html = conn_status()
left, right = st.columns([5, 2])
with left:
    st.markdown('<div class="brand"><span class="name">مسار</span><span class="tag">تنظيم شكاوى الزبائن</span></div>'
                '<div class="sub">ارفع ملف الشكاوى، راجع النتائج والمسودات، واعتمد الرد. ما ينرسل أي رد للزبون من هنا.</div>',
                unsafe_allow_html=True)
with right:
    st.markdown(f'<div style="text-align:left;padding-top:.8rem">{conn_html}</div>', unsafe_allow_html=True)

if not connected:
    env, _ = llm.key_status()
    st.markdown(
        f'<div class="note fail"><b>المعالجة متوقفة لحد ما تضيف مفتاح الموديل</b>'
        f'افتح ملف <code>.env</code> بمجلد المشروع واكتب <code>{esc(env)}=...</code> وبعدين أعد تشغيل البرنامج. '
        f'رفع الملفات وفحصها يشتغل بدون مفتاح.</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ الشريط الجانبي
with st.sidebar:
    st.markdown("### دفعة جديدة")
    up = st.file_uploader("ملف CSV بيه عمود الرسالة (message)", type=["csv"], label_visibility="visible")
    if up is not None:
        try:
            rows, skipped = ingest.parse_csv(up.getvalue())
            st.markdown(f'<div class="note ok"><b>الملف سليم</b>{len(rows)} شكوى جاهزة'
                        + (f'، {len(skipped)} سطر انترك' if skipped else '') + '</div>', unsafe_allow_html=True)
            if skipped:
                with st.expander("الأسطر المتروكة"):
                    for s in skipped:
                        st.write(f"السطر {s['row']}: {s['reason']}")
            if st.button("حفظ الدفعة", type="primary", width="stretch"):
                with db.connect() as conn:
                    bid, new = db.create_batch(conn, up.name, rows, skipped)
                st.session_state["batch"] = bid
                st.session_state["flash"] = f"انحفظت الدفعة. {new} شكوى جديدة، {len(rows) - new} موجودة من قبل وما تكررت."
                st.rerun()
        except ingest.CSVError as e:
            st.markdown(f'<div class="note fail"><b>ما نگدر نقرأ الملف</b>{esc(e)}</div>', unsafe_allow_html=True)

    st.markdown("### الدفعات")
    with db.connect() as conn:
        batches = db.list_batches(conn)
    if not batches:
        st.caption("ما اكو دفعات بعد.")
    else:
        labels = {b["id"]: f"#{b['id']} · {b['filename']} · {b['uploaded_at'][:16].replace('T', ' ')}" for b in batches}
        ids = list(labels)
        cur = st.session_state.get("batch", ids[0])
        st.session_state["batch"] = st.radio("اختر دفعة", ids, index=ids.index(cur) if cur in ids else 0,
                                             format_func=labels.get, label_visibility="collapsed")
    st.divider()
    st.caption(f"حد الثقة: {config.CONFIDENCE_THRESHOLD:.2f} · الموديل: {config.MODEL}")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

batch_id = st.session_state.get("batch")
if not batch_id:
    st.markdown("#### ابدأ برفع ملف")
    st.markdown("الملف لازم يكون CSV بيه عمود اسمه `message` للرسالة. أعمدة اختيارية: `complaint_id`، `received_at`، `channel`. "
                "بيانات تجريبية جاهزة بمجلد `data/complaints/`.")
    st.stop()

with db.connect() as conn:
    rows = db.batch_rows(conn, batch_id)
    batch = next(b for b in batches if b["id"] == batch_id)

pending = [r for r in rows if r["status"] == "pending"]
failed = [r for r in rows if r["status"] == "failed"]
done = [r for r in rows if r["status"] == "done"]


def run(ids, label):
    bar = st.progress(0.0, text=label)

    def cb(i, n, cid, status):
        bar.progress(i / n, text=f"{label} {i}/{n}")

    s = pipeline.process(ids, on_progress=cb)
    st.session_state["flash"] = f"خلصت المعالجة: {s['done']} نجحت، {s['failed']} فشلت."
    st.rerun()


# ------------------------------------------------------------------ المعالجة
if pending:
    c1, c2 = st.columns([3, 1])
    c1.markdown(f'<div class="note"><b>{len(pending)} شكوى بانتظار المعالجة</b>'
                f'كل شكوى تاخذ اتصالين بالموديل: واحد للفهم والاستخراج، وواحد للمسودة.</div>', unsafe_allow_html=True)
    if c2.button("ابدأ المعالجة", type="primary", disabled=not connected, width="stretch"):
        run([r["complaint_id"] for r in pending], "جاري المعالجة")

# ------------------------------------------------------------------ الملخص
needs_review = [r for r in done if r["needs_review"] and not r["decision"]]
approved = [r for r in rows if r["decision"] == "approved"]
st.markdown(f"### الدفعة #{batch_id} <span style='font-weight:400;color:#5B6773;font-size:.95rem'>· {esc(batch['filename'])}</span>",
            unsafe_allow_html=True)
st.markdown(f"""
<div class="stats">
  <div class="stat"><div class="v">{len(rows)}</div><div class="k">شكاوى بالدفعة</div></div>
  <div class="stat teal"><div class="v">{len(done)}</div><div class="k">تمت معالجتها</div></div>
  <div class="stat amber"><div class="v">{len(needs_review)}</div><div class="k">تحتاج مراجعة</div></div>
  <div class="stat red"><div class="v">{len(failed)}</div><div class="k">تعذّرت معالجتها</div></div>
  <div class="stat"><div class="v">{len(approved)}</div><div class="k">مسودات معتمدة</div></div>
</div>""", unsafe_allow_html=True)

if done:
    def bars(counter, labels, kind=None):
        total = max(1, sum(counter.values()))
        out = []
        for key, label in labels.items():
            n = counter.get(key, 0)
            cls = f" {key}" if kind == "pri" else ""
            out.append(f'<div class="bar-row"><span>{esc(label)}</span><div class="bar-track">'
                       f'<div class="bar-fill{cls}" style="width:{n / total * 100:.0f}%"></div></div>'
                       f'<span class="bar-n">{n}</span></div>')
        return "".join(out)

    cats = pd.Series([r["category"] for r in done]).value_counts().to_dict()
    pris = pd.Series([r["priority"] for r in done]).value_counts().to_dict()
    st.markdown(f'<div class="bars"><div><div class="sec">حسب نوع المشكلة</div>{bars(cats, CAT)}</div>'
                f'<div><div class="sec">حسب الأولوية</div>{bars(pris, PRI, "pri")}'
                f'<div class="sec" style="margin-top:1rem">أسباب المراجعة الأكثر</div>'
                + "".join(f'<div class="bar-row"><span>{esc(REASONS.get(k, k))}</span><div></div><span class="bar-n">{v}</span></div>'
                          for k, v in pd.Series([x for r in done for x in r["review_reasons"]]).value_counts().head(4).items())
                + '</div></div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ الفاشلة
if failed:
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.markdown(f"**{len(failed)} شكوى تعذّرت معالجتها.** إعادة المحاولة تحدّث نفس السجل وما تنشئ نسخة جديدة.")
        if b.button("إعادة المحاولة", disabled=not connected, width="stretch"):
            run([r["complaint_id"] for r in failed], "إعادة المحاولة")
        st.dataframe(pd.DataFrame([{
            "السطر": r["row_number"], "الرسالة": r["message"][:80], "السبب": r["error"], "المحاولات": r["attempts"],
        } for r in failed]), hide_index=True, width="stretch")

if not done:
    st.stop()

# ------------------------------------------------------------------ قائمة الشكاوى
st.markdown("#### الشكاوى")
f1, f2, f3 = st.columns([2, 2, 2])
view = f1.segmented_control("عرض", ["الكل", "تحتاج مراجعة", "أولوية عالية", "معتمدة"], default="الكل",
                            label_visibility="collapsed")
cat_filter = f2.selectbox("النوع", ["كل الأنواع"] + list(CAT.values()), label_visibility="collapsed")
q = f3.text_input("بحث", placeholder="بحث بالنص أو رقم العملية", label_visibility="collapsed")

items = done
if view == "تحتاج مراجعة":
    items = [r for r in items if r["needs_review"] and not r["decision"]]
elif view == "أولوية عالية":
    items = [r for r in items if r["priority"] == "high"]
elif view == "معتمدة":
    items = [r for r in items if r["decision"] == "approved"]
if cat_filter != "كل الأنواع":
    items = [r for r in items if CAT[r["category"]] == cat_filter]
if q:
    items = [r for r in items if q in r["message"] or q in (r["transaction_id"] or "")]

order = {"high": 0, "medium": 1, "low": 2}
items = sorted(items, key=lambda r: (bool(r["decision"]), not r["needs_review"], order[r["priority"]], r["row_number"]))


def status_label(r):
    if r["decision"] == "approved":
        return "معتمدة"
    if r["decision"] == "needs_followup":
        return "متابعة"
    return "مراجعة" if r["needs_review"] else "جاهزة"


df = pd.DataFrame([{
    "الحالة": status_label(r),
    "الأولوية": PRI[r["priority"]],
    "النوع": CAT[(r["corrected_category"] or r["category"])],
    "القسم": r["department"],
    "الرسالة": r["message"][:90],
    "رقم العملية": r["transaction_id"] or "—",
    "المبلغ": fmt_amount(r["amount"]) or "—",
    "الثقة": round(r["confidence"] * 100),
} for r in items])

if df.empty:
    st.caption("ما اكو شكاوى بهذا الفلتر.")
    st.stop()

sel = st.dataframe(
    df, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(df)),
    on_select="rerun", selection_mode="single-row",
    column_config={"الثقة": st.column_config.ProgressColumn("الثقة %", min_value=0, max_value=100, format="%d")},
)
picked = sel.selection.rows[0] if sel.selection.rows else 0
r = items[picked]

# ------------------------------------------------------------------ تفاصيل الشكوى
st.markdown("---")
st.markdown(f"#### الشكوى {esc(r['source_id'] or r['complaint_id'])}"
            f" <span style='font-weight:400;color:#5B6773;font-size:.9rem'>· السطر {r['row_number']}"
            f"{' · ' + esc(r['channel']) if r['channel'] else ''}{' · ' + esc(r['received_at']) if r['received_at'] else ''}</span>",
            unsafe_allow_html=True)

colA, colB = st.columns([1, 1], gap="large")
with colA:
    st.markdown('<div class="sec">الرسالة الأصلية</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="msg">{esc(r["message"])}</div>', unsafe_allow_html=True)

    def v(x):
        return f'<span class="v">{esc(x)}</span>' if x not in (None, "") else '<span class="v empty">فارغ — ما مذكور بالرسالة</span>'

    st.markdown('<div class="sec" style="margin-top:1rem">النتيجة</div>', unsafe_allow_html=True)
    st.markdown(f"""<div class="kv">
      <span class="k">نوع المشكلة</span><span class="v">{esc(CAT[r['category']])}</span>
      <span class="k">الأولوية</span><span><span class="pill p-{r['priority']}">{PRI[r['priority']]}</span></span>
      <span class="k">القسم</span><span class="v">{esc(r['department'])}</span>
      <span class="k">رقم العملية</span>{v(r['transaction_id'])}
      <span class="k">المبلغ</span>{v(fmt_amount(r['amount']))}
      <span class="k">التاريخ</span>{v(r['date'])}
      <span class="k">الثقة</span><span class="v">{r['confidence'] * 100:.0f}%</span>
    </div><div class="why"><b>سبب التصنيف:</b> {esc(r['reason'])}</div>""", unsafe_allow_html=True)

    if r["review_reasons"]:
        st.markdown('<div class="note review"><b>تحتاج مراجعة</b><ul>'
                    + "".join(f"<li>{esc(REASONS.get(x, x))}</li>" for x in r["review_reasons"]) + "</ul></div>",
                    unsafe_allow_html=True)
    if r["missing_fields"]:
        st.markdown('<div class="note"><b>معلومات ناقصة يحتاجها القسم</b>'
                    + "، ".join(config.FIELD_LABELS[m] for m in r["missing_fields"]) + "</div>", unsafe_allow_html=True)
    st.caption(f"{r['model']} · {r['latency_ms']} ms · محاولات: {r['attempts']}")

with colB:
    st.markdown('<div class="sec">مسودة الرد — راجعها وعدّلها قبل الاعتماد</div>', unsafe_allow_html=True)
    key = f"draft_{r['complaint_id']}"
    if f"new_{key}" in st.session_state:  # مسودة جديدة من "إعادة كتابة"
        st.session_state[key] = st.session_state.pop(f"new_{key}")
    elif key not in st.session_state:
        st.session_state[key] = r["final_draft"] or r["draft"] or ""
    st.text_area("المسودة", key=key, height=190, label_visibility="collapsed")

    cat_keys = list(CAT)
    current_cat = r["corrected_category"] or r["category"]
    new_cat = st.selectbox("تصحيح النوع (إذا الموديل غلط)", cat_keys, index=cat_keys.index(current_cat),
                           format_func=CAT.get, key=f"cat_{r['complaint_id']}")
    note = st.text_input("ملاحظة (اختياري)", value=r["note"] or "", key=f"note_{r['complaint_id']}")

    b1, b2, b3 = st.columns(3)
    if b1.button("اعتماد المسودة", type="primary", width="stretch", key=f"ok_{r['complaint_id']}"):
        found = pipeline.check_draft(st.session_state[key])
        if found:
            st.error("المسودة بيها عبارة ممنوعة: " + "، ".join(found))
        else:
            with db.connect() as conn:
                db.save_review(conn, r["complaint_id"], "approved", st.session_state[key],
                               new_cat if new_cat != r["category"] else None, note or None)
            st.session_state["flash"] = "انعتمدت المسودة. الإرسال للزبون يصير يدوياً من نظام الدعم."
            st.rerun()
    if b2.button("تحتاج متابعة", width="stretch", key=f"fu_{r['complaint_id']}"):
        with db.connect() as conn:
            db.save_review(conn, r["complaint_id"], "needs_followup", st.session_state[key],
                           new_cat if new_cat != r["category"] else None, note or None)
        st.rerun()
    if b3.button("إعادة كتابة", width="stretch", disabled=not connected, key=f"re_{r['complaint_id']}",
                 help="يكتب مسودة جديدة حسب النوع المختار"):
        if new_cat != r["category"]:
            with db.connect() as conn:
                db.save_review(conn, r["complaint_id"], r["decision"] or "needs_followup", st.session_state[key], new_cat, note or None)
        with st.spinner("جاري كتابة مسودة جديدة"):
            try:
                draft, found = pipeline.regenerate_draft(r["complaint_id"])
                st.session_state[f"new_{key}"] = draft
                if found:
                    st.session_state["flash"] = "المسودة الجديدة بيها عبارة ممنوعة، عدّلها قبل الاعتماد."
            except llm.LLMError as e:
                st.session_state["flash"] = f"تعذّرت إعادة الكتابة: {e}"
        st.rerun()

    if r["decision"]:
        st.markdown(f'<div class="note ok"><b>{"معتمدة" if r["decision"] == "approved" else "محوّلة للمتابعة"}</b>'
                    f'{esc(r["reviewed_at"][:16].replace("T", " "))}</div>', unsafe_allow_html=True)
