"""توليد بيانات المسابقة: 300 شكوى يكتبها موديل لغوي باللهجة العراقية، مع الإجابة الصحيحة.

    python scripts/generate_llm_dataset.py --dry-run     # يعرض التعليمات لسيناريو واحد بدون اتصال
    python scripts/generate_llm_dataset.py               # التوليد الكامل (60 اتصال تقريباً)

الطريقة:
- 60 سيناريو (scripts/scenarios.py)، الموديل يكتب 5 رسائل لكل سيناريو.
- الكود يختار المعلومات (المبلغ، رقم العملية، الوقت، تاريخ الاستلام) ويحدد شكل كتابتها،
  والموديل يكتب الرسالة حولها. فالإجابة الصحيحة معروفة من الكود، مو من تقدير الموديل.
- كل رسالة تنفحص: المعلومات المعطاة موجودة حرفياً، وما في أرقام أو تعابير وقت زايدة.
  الرسالة اللي تفشل تنعاد كتابتها (لحد 3 جولات). كل الاتصالات تنسجل بـ generation_log.jsonl.
- التقسيم بالسيناريو: 180 تطوير / 60 ضبط / 60 اختبار، والسيناريوهات المتشابهة بنفس المجموعة.

الموديل: MASAR_GEN_MODEL و MASAR_GEN_PROVIDER بملف .env (الافتراضي نفس موديل النظام).
"""
import argparse
import csv
import hashlib
import json
import random
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

from masar import config, llm  # noqa: E402
from masar.ingest import ARABIC_DIGITS  # noqa: E402
from generate_template_dataset import render_amount, render_date, render_tx, similar_phones, AMOUNTS, BIG  # noqa: E402
from scenarios import S, split  # noqa: E402

SEED = 20261016
PER_SCENARIO = 5
MAX_ROUNDS = 3
TEMPERATURE = 1.0
GEN_PROMPT_VERSION = "gen-v1"
OUT = ROOT / "data" / "complaints"
GEN_PROVIDER = os.getenv("MASAR_GEN_PROVIDER", config.PROVIDER)
GEN_MODEL = os.getenv("MASAR_GEN_MODEL", config.MODEL)

SYSTEM = """أنت تساعد فريق يبني نظام دعم تجريبي لمحفظة إلكترونية عراقية. نحتاج بيانات اختبار وهمية: رسائل شكاوى يكتبها زبائن عراقيين بالتطبيق أو الواتساب.

اكتب كل رسالة مثل ما يكتبها زبون حقيقي: لهجة عراقية مو فصحى، طبيعية، بدون شرح أو تعليق منك. نوّع بين الرسائل بالصياغة والترتيب والمفردات، وما تكرر نفس الجمل بين رسالة وثانية.

قواعد إلزامية:
1. أي معلومة نعطيك إياها بين «» تنكتب بالرسالة حرفياً بنفس الشكل والأرقام، بدون علامات «».
2. لا تضيف أي مبلغ، أو رقم من 4 خانات أو أكثر، أو تاريخ، أو تعبير وقت (مثل: اليوم، امس، البارحة، هسه، الحين، الصبح، المسا، قبل يومين، الأسبوع، الشهر، ساعة) غير اللي نعطيك إياه.
3. إذا ما عطيناك مبلغ أو رقم عملية أو وقت، الرسالة ما تذكره أبداً.
4. لا تذكر اسم شركة أو تطبيق حقيقي، ولا أسماء أشخاص معروفين.
5. التزم بالسيناريو والأسلوب المطلوب لكل رسالة.

رجّع JSON فقط بالشكل المطلوب."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["n", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

LENGTHS = ["قصيرة (جملة وحدة)", "متوسطة (جملتين أو ثلاث)", "أطول شوية (3 إلى 5 جمل)"]
TONES = ["هادئ ومؤدب", "مستعجل", "زعلان", "قلقان", "مختصر وجاف", "عادي"]
TIME_WORDS = ["اليوم", "البارحة", "البارح", "امبارح", "مبارح", "امس", "أمس", "الصبح", "الصباح", "العصر",
              "المسا", "الليل", "قبل يوم", "قبل يومين", "قبل ثلاث", "قبل كم يوم", "هسه", "هسة", "الحين",
              "الأسبوع", "الاسبوع", "اسبوع", "الشهر", "شهر", "السنة", "ساعة", "ساعات", "دقيقة", "دقايق"]


def digits(s):
    return (s or "").translate(ARABIC_DIGITS)


def plan_variant(rng, scen):
    """يختار المعلومات وشكل كتابتها والأسلوب لنسخة وحدة."""
    received = date(2026, 10, 1) + timedelta(days=rng.randint(0, 13))
    f = scen["fields"]
    truth = {"transaction_id": None, "amount": None, "date": None}
    given = {}
    if "a" in f and rng.random() < 0.8:
        v = rng.choice(AMOUNTS)
        truth["amount"], given["amount"] = v, render_amount(rng, v)
    if "b" in f:
        v = rng.choice(BIG)
        truth["amount"], given["amount"] = v, render_amount(rng, v)
    if "t" in f and rng.random() < 0.6:
        truth["transaction_id"], given["tx"] = render_tx(rng)
    if "d" in f and rng.random() < 0.6:
        truth["date"], given["date"] = render_date(rng, received)
    if "p" in f:
        given["phone_wrong"], given["phone_right"] = similar_phones(rng)
    extras = []
    if rng.random() < 0.35:
        extras.append("بيها أخطاء إملائية بسيطة")
    if rng.random() < 0.25:
        extras.append("بيها كلمة أو كلمتين إنجليزي")
    if rng.random() < 0.4:
        extras.append("تبدي بتحية أو تنتهي بدعاء أو شكر")
    return {
        "received_at": f"{received.isoformat()} {rng.randint(8, 22):02d}:{rng.choice([0, 10, 25, 40, 55]):02d}",
        "channel": rng.choice(["تطبيق", "واتساب", "فيسبوك", "مركز الاتصال"]),
        "truth": truth,
        "given": given,
        "style": f"{rng.choice(LENGTHS)}، النبرة {rng.choice(TONES)}" + ("، " + "، ".join(extras) if extras else ""),
    }


def facts_line(given):
    parts = []
    if "amount" in given:
        parts.append(f"المبلغ «{given['amount']}»")
    if "tx" in given:
        parts.append(f"رقم العملية «{given['tx']}»")
    if "date" in given:
        parts.append(f"الوقت «{given['date']}»")
    if "phone_wrong" in given:
        parts.append(f"الرقم اللي حوّل عليه بالغلط «{given['phone_wrong']}» والرقم الصحيح «{given['phone_right']}»")
    missing = [n for k, n in (("amount", "مبلغ"), ("tx", "رقم عملية"), ("date", "وقت")) if k not in given]
    line = "، ".join(parts) if parts else ""
    if missing:
        line += ("؛ " if line else "") + "بدون " + " ولا ".join(missing)
    return line


def user_prompt(scen, variants):
    lines = [f"السيناريو: {scen['description']}", "", f"اكتب {len(variants)} رسائل، كل وحدة مستقلة:"]
    for i, v in enumerate(variants, 1):
        lines.append(f"{i}. الأسلوب: {v['style']}.\n   المعلومات: {facts_line(v['given'])}.")
    return "\n".join(lines)


def verify(text, given, seen):
    """يرجع سبب الرفض أو None."""
    t = (text or "").strip()
    if not 8 <= len(t) <= 700:
        return "الطول مو مناسب"
    if "«" in t or "»" in t:
        return "بيها علامات «»"
    for k, v in given.items():
        if v not in t:
            return f"المعلومة {k} مو مكتوبة حرفياً: {v}"
    allowed = set(re.findall(r"\d+", digits(" ".join(given.values()))))
    extra = [r for r in re.findall(r"\d{4,}", digits(t)) if r not in allowed]
    if extra:
        return f"أرقام زايدة: {extra}"
    date_expr = given.get("date", "")
    stripped = t.replace(date_expr, " ") if date_expr else t
    for w in TIME_WORDS:
        if re.search(rf"(^|[\s،,.!؟?]|[وفب]){re.escape(w)}", stripped):
            return f"تعبير وقت زايد: {w}"
    if t in seen:
        return "مكررة"
    return None


def generate_scenario(scen, variants, log, seen):
    texts = [None] * len(variants)
    pending = list(range(len(variants)))
    usage = {"calls": 0, "input": 0, "output": 0, "rejected": 0}
    for rnd in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        batch = [variants[i] for i in pending]
        res = llm.call(SYSTEM, user_prompt(scen, batch), schema=SCHEMA, max_tokens=6000,
                       provider=GEN_PROVIDER, model=GEN_MODEL, temperature=TEMPERATURE)
        usage["calls"] += 1
        usage["input"] += res.input_tokens or 0
        usage["output"] += res.output_tokens or 0
        items = {it.get("n"): it.get("text", "") for it in (res.data or {}).get("items", [])}
        still = []
        for j, idx in enumerate(pending, 1):
            text = (items.get(j) or "").strip()
            why = verify(text, variants[idx]["given"], seen)
            log.write(json.dumps({"scenario": scen["id"], "round": rnd, "slot": idx + 1, "text": text,
                                  "given": variants[idx]["given"], "rejected": why}, ensure_ascii=False) + "\n")
            if why:
                still.append(idx)
                usage["rejected"] += 1
            else:
                texts[idx] = text
                seen.add(text)
        pending = still
    if pending:
        raise SystemExit(f"السيناريو {scen['id']}: {len(pending)} رسائل فشلت بالفحص بعد {MAX_ROUNDS} جولات. "
                         f"شوف generation_log.jsonl وعدّل التعليمات.")
    return texts, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="يعرض التعليمات لأول سيناريو بدون اتصال")
    ap.add_argument("--force", action="store_true", help="يكتب فوق بيانات مولّدة سابقاً")
    args = ap.parse_args()

    rng = random.Random(SEED)
    splits = split(rng)
    plans = {s["id"]: [plan_variant(rng, s) for _ in range(PER_SCENARIO)] for s in S}

    if args.dry_run:
        s = S[0]
        print("== SYSTEM ==\n" + SYSTEM + "\n\n== USER ==\n" + user_prompt(s, plans[s["id"]]))
        print(f"\nالموديل: {GEN_PROVIDER}/{GEN_MODEL}، temperature={TEMPERATURE}")
        return
    if (OUT / "manifest.json").exists() and not args.force:
        raise SystemExit("البيانات المولّدة موجودة. ما نعيد توليدها حتى ما تتغير مجموعة الاختبار. إذا متأكد، أضف --force.")
    env, ok = llm.key_status(GEN_PROVIDER)
    if not ok:
        raise SystemExit(f"ما لگينا {env}. حطه بملف .env.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "labels").mkdir(exist_ok=True)
    rows = {"dev": [], "calib": [], "test": []}
    totals = {"calls": 0, "input": 0, "output": 0, "rejected": 0}
    seen = set()
    with (OUT / "generation_log.jsonl").open("w", encoding="utf-8") as log:
        for n, s in enumerate(S, 1):
            print(f"\r{n}/60 {s['id']}", end="", flush=True)
            texts, usage = generate_scenario(s, plans[s["id"]], log, seen)
            for k in totals:
                totals[k] += usage[k]
            sp = splits[s["id"]]
            for i, (text, v) in enumerate(zip(texts, plans[s["id"]]), 1):
                cid = f"{sp.upper()}-{s['id']}-{i}"
                tr = v["truth"]
                rows[sp].append({
                    "row": {"complaint_id": cid, "received_at": v["received_at"], "channel": v["channel"], "message": text},
                    "label": {
                        "complaint_id": cid, "family_id": s["id"], "category": s["category"],
                        "secondary_category": s["secondary"] or "",
                        "priority": config.priority_for(s["category"], tr["amount"], s["risk"]),
                        "department": config.ROUTING[s["category"]],
                        "transaction_id": tr["transaction_id"] or "",
                        "amount": tr["amount"] if tr["amount"] is not None else "",
                        "date": tr["date"] or "",
                        "security_risk": int(s["risk"]),
                        "expect_review": int(bool(s["hard"])),
                        "hard_type": s["hard"] or "",
                    },
                })
    print()

    manifest = {
        "source": "llm",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": GEN_PROVIDER, "model": GEN_MODEL, "temperature": TEMPERATURE,
        "prompt_version": GEN_PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "seed": SEED, "per_scenario": PER_SCENARIO, "usage": totals, "splits": {},
    }
    for sp, items in rows.items():
        rng.shuffle(items)
        p, lp = OUT / f"{sp}.csv", OUT / "labels" / f"{sp}_labels.csv"
        with p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(items[0]["row"]))
            w.writeheader()
            w.writerows(r["row"] for r in items)
        with lp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(items[0]["label"]))
            w.writeheader()
            w.writerows(r["label"] for r in items)
        cats = {}
        for r in items:
            cats[r["label"]["category"]] = cats.get(r["label"]["category"], 0) + 1
        manifest["splits"][sp] = {
            "rows": len(items), "scenarios": sorted({r["label"]["family_id"] for r in items}), "by_category": cats,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "labels_sha256": hashlib.sha256(lp.read_bytes()).hexdigest(),
        }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("model", "usage")}, ensure_ascii=False))
    print({sp: m["rows"] for sp, m in manifest["splits"].items()})


if __name__ == "__main__":
    main()
