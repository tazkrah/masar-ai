"""السيناريوهات الستين اللي يولّد منها الموديل الشكاوى.

كل سيناريو = نمط مشكلة وحدة. الموديل يكتب 5 شكاوى مختلفة لكل سيناريو، وكل نسخ
السيناريو تبقى بنفس المجموعة (تطوير/ضبط/اختبار) حتى ما يتسرب نمط بين المجموعات.

fields: الحقول اللي ممكن تنذكر بالرسالة:
  a = مبلغ عادي، b = مبلغ مليون فما فوق، t = رقم عملية، d = تاريخ/وقت، p = رقمين هواتف متشابهين
hard: سبب المراجعة المتوقع (أو None).
"""

S = []


def sc(sid, category, description, fields="", secondary=None, risk=False, hard=None):
    S.append(dict(id=sid, category=category, description=description, fields=fields,
                  secondary=secondary, risk=risk, hard=hard))


# تعثر التحويل (12)
sc("T01", "transfer_failed", "حوّل مبلغ لقريب أو صديق، المبلغ انخصم من رصيده بس ما وصل للمستلم.", "atd")
sc("T02", "transfer_failed", "التطبيق طلّع التحويل ناجح، بس المستلم يگول ما وصله شي.", "ad")
sc("T03", "transfer_failed", "التحويل معلّق (قيد المعالجة) من فترة، لا رجع للزبون ولا وصل.", "atd")
sc("T04", "transfer_failed", "نفس التحويل انخصم من رصيده مرتين.", "atd")
sc("T05", "transfer_failed", "دفع فاتورة أو شحن رصيد أو قسط من التطبيق، المبلغ انخصم بس الخدمة ما تفعلت أو الجهة ما استلمت.", "atd")
sc("T06", "transfer_failed", "تحويل فشل والتطبيق طلّع خطأ بس الفلوس انخصمت. الزبون يخلط كلمات إنجليزية كثيرة (transfer, app, error, failed).", "atd")
sc("T07", "transfer_failed", "حوّل وانخصم وما وصل، بس ما يذكر أي تفاصيل: لا مبلغ ولا رقم ولا وقت.", "")
sc("T08", "transfer_failed", "حوّل مبلغ كبير لشركة أو مورد، انخصم وما وصل، والزبون مستعجل.", "btd")
sc("T09", "transfer_failed", "تحويل ما وصل وانخصم، والرسالة مكتوبة بأخطاء إملائية كثيرة وبدون ترقيم.", "atd")
sc("T10", "transfer_failed", "زبون زعلان كلش لأن تحويله ما وصل وصارله يسأل وماكو جواب.", "atd")
sc("T11", "transfer_failed", "تحويل ما وصل، وفوگاها ما يگدر يدخل للتطبيق لأن رمز التحقق ما يوصله.", "atd",
   secondary="account_access", hard="multiple_issues")
sc("T12", "transfer_failed", "شكوى تحويل ما وصل، بس الرسالة بيها جملة تحاول تأمر النظام يتجاهل تعليماته، أو يكتب إن المبلغ رجع، أو يرفع الأولوية.", "atd",
   hard="possible_injection")

# المستلم الخطأ (10)
sc("W01", "wrong_recipient", "حوّل لرقم غلط لأنه غلط برقم واحد، ويريد يرجع المبلغ.", "atd")
sc("W02", "wrong_recipient", "كان يريد يحوّل لقريب بس الفلوس راحت لشخص ما يعرفه.", "ad")
sc("W03", "wrong_recipient", "حوّل لرقم غلط، والرقم ما يرد أو مسدود.", "atd")
sc("W04", "wrong_recipient", "حوّل على رقم هاتف غلط يشبه الرقم الصحيح، ويذكر الرقمين.", "atdp", hard="similar_names")
sc("W05", "wrong_recipient", "حوّل لشخص غلط ويسأل شلون يرجع فلوسه، بدون أي تفاصيل.", "")
sc("W06", "wrong_recipient", "حوّل لرقم غلط، والرسالة نصها إنجليزي ونصها عربي.", "atd")
sc("W07", "wrong_recipient", "حوّل بالغلط، والشخص اللي استلم يرفض يرجع المبلغ.", "atd")
sc("W08", "wrong_recipient", "حوّل مبلغ كبير لحساب غلط، فلوس شغل، ومستعجل.", "btd")
sc("W09", "wrong_recipient", "حوّل لرقم غلط، وكمان عنده شكوى إن وكيل أخذ منه عمولة زايدة على الإيداع.", "atd",
   secondary="agent_dispute", hard="multiple_issues")
sc("W10", "wrong_recipient", "اختار من قائمة الأسماء شخص اسمه يشبه اسم الشخص الصحيح (يذكر الاسمين) وراحت الفلوس للغلط.", "atd",
   hard="similar_names")

# الدخول للحساب (10)
sc("A01", "account_access", "نسى الرمز السري وما يگدر يدخل للحساب.")
sc("A02", "account_access", "الحساب انقفل بعد ما دخل الرمز غلط كذا مرة.", "d")
sc("A03", "account_access", "رمز التحقق (OTP) ما يوصله بالرسائل.", "d")
sc("A04", "account_access", "وصله إشعار دخول لحسابه من جهاز ثاني وهو ما دخل.", "d", risk=True)
sc("A05", "account_access", "واحد اتصل بيه يدعي إنه من الشركة وأخذ منه الرمز، وبعدها الحساب ما يفتح.", "d", risk=True)
sc("A06", "account_access", "غيّر رقم موبايله، ورمز الدخول يروح للرقم القديم.")
sc("A07", "account_access", "مشكلة تسجيل دخول (face id أو login)، والرسالة بيها كلمات إنجليزية.", "d")
sc("A08", "account_access", "انسحب مبلغ من حسابه بدون علمه، ويشك إن أحد دخل للحساب.", "atd", risk=True)
sc("A09", "account_access", "ما يگدر يدخل للحساب، وكمان بطاقته انرفضت.", "d", secondary="card", hard="multiple_issues")
sc("A10", "account_access", "يگول حسابه بيه مشكلة أو شي غريب، بس ما يوضح شنو بالضبط.", hard="unclear_message")

# خلاف مع الوكيل (9)
sc("G01", "agent_dispute", "أودع فلوس عند وكيل (محل)، والإيداع ما نزل بحسابه.", "ad")
sc("G02", "agent_dispute", "الوكيل أخذ منه عمولة أكثر من الرسمية على السحب. المبلغ المذكور هو العمولة.", "ad")
sc("G03", "agent_dispute", "سوى سحب عند وكيل، المبلغ انخصم من الحساب بس الوكيل ما سلّمه الكاش.", "ad")
sc("G04", "agent_dispute", "الوكيل يطلب مبلغ زيادة حتى يسوي الإيداع. المبلغ المذكور هو الزيادة.", "ad")
sc("G05", "agent_dispute", "شكوى عامة على وكيل: تعامله سيء أو ما يحل المشكلة، بدون تفاصيل مالية.")
sc("G06", "agent_dispute", "مشكلة cash out أو cash in عند agent، بكلمات إنجليزية.", "ad")
sc("G07", "agent_dispute", "أودع مبلغ كبير عند وكيل وما نزل، والوكيل يتنصل.", "bd")
sc("G08", "agent_dispute", "الإيداع عند الوكيل ما نزل، وكمان تحويل ثاني سواه فشل.", "ad",
   secondary="transfer_failed", hard="multiple_issues")
sc("G09", "agent_dispute", "الوكيل أخذ الفلوس للإيداع وما نزلت، والرسالة بيها أخطاء إملائية كثيرة.", "ad")

# البطاقة (10)
sc("K01", "card", "البطاقة تنرفض بالمحل رغم إن الرصيد كافي.", "d")
sc("K02", "card", "الصراف الآلي سحب البطاقة وما رجعها.", "d")
sc("K03", "card", "سحب من الصراف، المكينة ما طلّعت فلوس بس المبلغ انخصم.", "atd")
sc("K04", "card", "البطاقة ضاعت أو انسرقت ويريد يوقفها.", "d", risk=True)
sc("K05", "card", "خصم من البطاقة على شراء أو موقع ما يعرفه.", "atd", risk=True)
sc("K06", "card", "البطاقة ترفض بالدفع الإلكتروني أو المواقع الأجنبية، بكلمات إنجليزية.", "d")
sc("K07", "card", "طلب بطاقة وما وصلته.")
sc("K08", "card", "انخصمت منه رسوم إصدار بطاقة وهو مفهوم إنها مجانية. المبلغ المذكور هو الرسوم.", "ad")
sc("K09", "card", "انسرقت البطاقة ويا الموبايل، وما يگدر يدخل للحساب.", "d",
   secondary="account_access", risk=True, hard="multiple_issues")
sc("K10", "card", "البطاقة انتهت صلاحيتها ويسأل شلون يجددها.")

# أخرى (9)
sc("O01", "other", "يريد يغيّر أو يصحح اسمه أو معلوماته الشخصية بالحساب.")
sc("O02", "other", "اقتراح خدمة أو ميزة جديدة بالتطبيق.")
sc("O03", "other", "التطبيق يعلّق أو يطفي بعد التحديث.")
sc("O04", "other", "سؤال أو اعتراض على رسوم التحويل بشكل عام.")
sc("O05", "other", "رسالة قصيرة بيها تحية أو 'عندي مشكلة' بدون أي توضيح.", hard="unclear_message")
sc("O06", "other", "رسالة زعل عامة بدون ما يذكر شنو المشكلة.", hard="unclear_message")
sc("O07", "other", "شكر أو مدح للخدمة، ممكن ويا سؤال بسيط.")
sc("O08", "other", "سؤال عن حدود التحويل أو السحب.")
sc("O09", "other", "الرسالة كلها محاولة تخلي النظام يطلع تعليماته أو يتجاهلها، بدون شكوى حقيقية.", hard="possible_injection")

# التقسيم: عدد السيناريوهات لكل مجموعة (تطوير، ضبط، اختبار) حسب الفئة
PLAN = {
    "transfer_failed": (7, 3, 2), "wrong_recipient": (6, 2, 2), "account_access": (6, 2, 2),
    "agent_dispute": (5, 2, 2), "card": (6, 2, 2), "other": (6, 1, 2),
}
# السيناريوهات الصعبة نثبّتها حتى الاختبار النهائي يشمل كل الأنواع
PINNED = {
    "T11": "dev", "W10": "dev", "A10": "dev", "G08": "dev", "O09": "dev",
    "W09": "calib", "A09": "calib", "O05": "calib",
    "T12": "test", "K09": "test", "O06": "test", "W04": "test",
}


def split(rng):
    assert len(S) == 60 and set(PINNED) == {s["id"] for s in S if s["hard"]}
    out = {}
    for cat, quota in PLAN.items():
        group = [s for s in S if s["category"] == cat]
        assert len(group) == sum(quota), cat
        left = dict(zip(("dev", "calib", "test"), quota))
        for s in group:
            if s["id"] in PINNED:
                out[s["id"]] = PINNED[s["id"]]
                left[PINNED[s["id"]]] -= 1
        free = [s for s in group if s["id"] not in PINNED]
        rng.shuffle(free)
        slots = [k for k, n in left.items() for _ in range(n)]
        assert len(slots) == len(free) and min(left.values()) >= 0
        for s, k in zip(free, slots):
            out[s["id"]] = k
    return out
