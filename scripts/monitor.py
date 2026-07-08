# -*- coding: utf-8 -*-
"""Монитор Битрикс24 ExpertNeuro. Чистый Python (stdlib), 0 токенов LLM.
Тянет сделки за последние 8 дней (read-only), считает метрики по менеджерам /
подрядчикам / городам, формирует алерты и пишет docs/data/data.js + history.json.
Запуск: BITRIX_WEBHOOK=<url> python scripts/monitor.py
"""
import json, os, re, sys, time, urllib.parse, urllib.request, datetime

WEBHOOK = os.environ.get("BITRIX_WEBHOOK", "").rstrip("/") + "/"
if WEBHOOK == "/":
    sys.exit("ERROR: env BITRIX_WEBHOOK не задан")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
os.makedirs(DATA, exist_ok=True)

ENTRY = {1: "Алматы", 3: "Астана", 100: "Шымкент", 114: "Тараз", 31: "Дожим"}
ENTRYCATS = {1, 3, 100, 114}

# Москва (+03) — часовой пояс Битрикса
MSK = datetime.timezone(datetime.timedelta(hours=3))
now = datetime.datetime.now(MSK)
today = now.date()
D_FROM = (today - datetime.timedelta(days=7)).isoformat() + "T00:00:00+03:00"
D_TO = (today + datetime.timedelta(days=1)).isoformat() + "T00:00:00+03:00"


def call(method, params):
    data = urllib.parse.urlencode(params, doseq=True).encode()
    for att in range(6):
        try:
            req = urllib.request.Request(WEBHOOK + method + ".json", data=data, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except Exception:
            if att == 5:
                raise
            time.sleep(2 * (att + 1))


# ── справочники ──
SN = {}
for c in ENTRY:
    for s in call("crm.status.list", [("filter[ENTITY_ID]", f"DEAL_STAGE_{c}")]).get("result", []) or []:
        SN[s["STATUS_ID"]] = s["NAME"]
# карта менеджеров: из секрета BX_USERS_JSON (публичный репо не должен содержать имена),
# локальный fallback — файл bx_users.json (в .gitignore)
if os.environ.get("BX_USERS_JSON"):
    USERS = json.loads(os.environ["BX_USERS_JSON"])
else:
    with open(os.path.join(ROOT, "bx_users.json"), encoding="utf-8") as f:
        USERS = json.load(f)

# ── выгрузка сделок ──
SEL = ["ID", "TITLE", "DATE_CREATE", "CATEGORY_ID", "STAGE_ID", "CONTACT_ID",
       "ASSIGNED_BY_ID", "UTM_SOURCE", "UTM_MEDIUM", "UTM_CAMPAIGN", "UTM_CONTENT", "UTM_TERM"]
deals = []
for cat in ENTRY:
    start = 0
    while True:
        r = call("crm.deal.list",
                 [("filter[CATEGORY_ID]", str(cat)), ("filter[>=DATE_CREATE]", D_FROM),
                  ("filter[<DATE_CREATE]", D_TO), ("order[DATE_CREATE]", "ASC")]
                 + [("select[]", s) for s in SEL] + [("start", str(start))])
        deals.extend(r.get("result", []) or [])
        nxt = r.get("next")
        if nxt is None:
            break
        start = int(nxt)
        time.sleep(0.1)


def dom(t):
    m = re.search(r"https?://([^/\s#]+)", t or "")
    return m.group(1).lower() if m else ""


def hu(x):
    return any(x.get(k) for k in ["UTM_SOURCE", "UTM_MEDIUM", "UTM_CAMPAIGN", "UTM_CONTENT", "UTM_TERM"])


def contractor(x):
    t = x.get("TITLE") or ""
    tl = t.lower()
    dmn = dom(t)
    camp = (x.get("UTM_CAMPAIGN") or "").lower()
    if "chill traffic" in tl or "chilltraffic" in tl or "expert-neuro.kz" in tl:
        return "Chill Traffic"
    if (x.get("UTM_MEDIUM") or "").upper() == "CT" or "ct_ala" in camp:
        return "Chill Traffic"
    if "мухтарбек" in tl or "mukhtar" in camp or "mukhtar" in (x.get("UTM_CONTENT") or "").lower():
        return "Мухтарбек"
    if dmn == "expertneuro1.kz":
        return "i-con"
    if dmn:
        return "SP"
    if hu(x):
        city = ENTRY.get(int(x.get("CATEGORY_ID") or 0), "")
        k = camp + " " + tl
        if city in ("Тараз", "Шымкент") or any(w in k for w in ["тараз", "taraz", "шымкент", "shym"]):
            return "Ержан"
        if "tiktok" in (x.get("UTM_SOURCE") or "").lower():
            return "Chill Traffic"
        return "i-con"
    return "Не засчитано"


TRASH = ["другой город", "не наш", "дубль", "случайно", "некорр", "неккор", "к другим", "нет денег", "не актуально"]


def outcome(cat, stid):
    cat = int(cat or 0)
    n = (SN.get(stid, "") or "").lower()
    if cat == 31:
        return "dojim"
    if cat not in ENTRYCATS:
        return "booked"
    if any(w in n for w in ["записал", "запись", "уже записан", "финальн", "успешна", "пришёл", "пришел"]):
        return "booked"
    if any(w in n for w in TRASH):
        return "trash"
    if "нб" == n or "не берёт" in n or "не берет" in n:
        return "nb"
    return "inwork"


CP = {"i-con": 6, "SP": 6, "Chill Traffic": 6, "Мухтарбек": 6, "Ержан": 5, "Не засчитано": 1}
OP = {"booked": 5, "inwork": 4, "nb": 3, "dojim": 2, "trash": 1}

# ── дедуп по контакту ──
byc = {}
for d in deals:
    byc.setdefault(d.get("CONTACT_ID") or ("id" + d["ID"]), []).append(d)

people = []
for cid, cards in byc.items():
    rep = min(cards, key=lambda c: c["DATE_CREATE"])
    o = max((outcome(c.get("CATEGORY_ID"), c.get("STAGE_ID")) for c in cards), key=lambda z: OP[z])
    aid = str(rep.get("ASSIGNED_BY_ID") or "")
    people.append({
        "day": rep["DATE_CREATE"][:10],
        "contr": max((contractor(c) for c in cards), key=lambda z: CP[z]),
        "city": ENTRY.get(int(rep.get("CATEGORY_ID") or 0), "?"),
        "outcome": o,
        "mgr": USERS.get(aid, f"ID {aid}") if aid else "(нет)",
        "stage": SN.get(rep.get("STAGE_ID"), rep.get("STAGE_ID") or ""),
    })

DAYS = [(today - datetime.timedelta(days=i)).isoformat() for i in range(7, -1, -1)]
t_str, y_str = today.isoformat(), (today - datetime.timedelta(days=1)).isoformat()


def agg(rows):
    a = {"leads": 0, "booked": 0, "dojim": 0, "trash": 0, "inwork": 0, "nb": 0}
    for p in rows:
        a["leads"] += 1
        a[p["outcome"]] += 1
    a["conv"] = round(100 * a["booked"] / a["leads"], 1) if a["leads"] else 0
    return a


ads = [p for p in people if p["contr"] != "Не засчитано"]

# по дням (реклама)
by_day = {d: agg([p for p in ads if p["day"] == d]) for d in DAYS}
# по менеджерам: сегодня и вчера (ВСЕ лиды, вкл. не-рекламу — менеджер отвечает за всё)
def mgr_table(day):
    out = []
    for m in sorted({p["mgr"] for p in people if p["day"] == day}):
        rows = [p for p in people if p["day"] == day and p["mgr"] == m]
        a = agg(rows)
        a["mgr"] = m
        out.append(a)
    out.sort(key=lambda z: -z["leads"])
    return out


mgr_today, mgr_yest = mgr_table(t_str), mgr_table(y_str)
# по подрядчикам / городам: сегодня vs вчера
def group(day, key):
    return {k: agg([p for p in ads if p["day"] == day and p[key] == k])
            for k in sorted({p[key] for p in ads if p["day"] == day})}


contr_today, contr_yest = group(t_str, "contr"), group(y_str, "contr")
city_today, city_yest = group(t_str, "city"), group(y_str, "city")

# «ничьи» лиды сегодня (нет живого ответственного)
ownerless = [p for p in people if p["day"] == t_str and
             (p["mgr"] in ("(нет)", "Администратор Портала") or p["mgr"].startswith("ID "))]

# ── алерты ──
alerts = []
week_avg = sum(by_day[d]["leads"] for d in DAYS[:-1]) / 7 if DAYS[:-1] else 0
if by_day[y_str]["leads"] < 0.7 * week_avg:
    alerts.append({"lvl": "critical", "text": f"Поток вчера {by_day[y_str]['leads']} — ниже 70% недельного среднего ({week_avg:.0f}/день)"})
if len(ownerless) >= 10:
    alerts.append({"lvl": "critical", "text": f"«Ничьих» лидов сегодня: {len(ownerless)} — автораспределение не назначает менеджера"})
elif len(ownerless) >= 5:
    alerts.append({"lvl": "warning", "text": f"«Ничьих» лидов сегодня: {len(ownerless)}"})
for m in mgr_yest:
    if m["leads"] >= 15 and m["conv"] < 10 and not m["mgr"].startswith("("):
        alerts.append({"lvl": "warning", "text": f"{m['mgr']}: вчера {m['leads']} лидов, записал {m['booked']} ({m['conv']}%)"})
for c, a in contr_yest.items():
    prev = agg([p for p in ads if p["day"] == DAYS[-3] and p["contr"] == c]) if len(DAYS) >= 3 else None
    if prev and prev["leads"] >= 20 and a["leads"] < 0.5 * prev["leads"]:
        alerts.append({"lvl": "warning", "text": f"{c}: лиды упали {prev['leads']} → {a['leads']} день к дню"})

# конвейер агентов показывается на otdel.html из шифрованного runs.enc.js;
# plaintext-статуса в публичном репо быть не должно
pipeline = None

snapshot = {
    "updated": now.strftime("%Y-%m-%d %H:%M") + " МСК",
    "today": t_str, "yesterday": y_str,
    "by_day": by_day, "days": DAYS,
    "mgr_today": mgr_today, "mgr_yest": mgr_yest,
    "contr_today": contr_today, "contr_yest": contr_yest,
    "city_today": city_today, "city_yest": city_yest,
    "ownerless_today": len(ownerless),
    "alerts": alerts,
    "pipeline": pipeline,
    "note": "Сегодняшний день неполный и «дозревает» (+13ч НБ→дожим). Дедуп по контакту. «Не засчитано» исключено из рекламных метрик, но входит в нагрузку менеджеров.",
}

# history: финальные числа по ВЧЕРА (аппенд без дублей)
hist_path = os.path.join(DATA, "history.json")
history = []
if os.path.exists(hist_path):
    with open(hist_path, encoding="utf-8") as f:
        history = json.load(f)
history = [h for h in history if h["day"] != y_str]
history.append({"day": y_str, **by_day[y_str],
                "contr": {k: v["leads"] for k, v in contr_yest.items()},
                "ownerless": len([p for p in people if p["day"] == y_str and
                                  (p["mgr"] in ("(нет)", "Администратор Портала") or p["mgr"].startswith("ID "))])})
history = sorted(history, key=lambda h: h["day"])[-60:]

snapshot["history"] = history

PASSWORD = os.environ.get("DASH_PASSWORD", "")
if PASSWORD:
    # публичный хостинг: данные ТОЛЬКО шифрованные (AES-256-GCM, ключ из пароля PBKDF2-SHA256)
    import base64, secrets as pysecrets
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ITER = 300_000

    def enc(obj):
        salt = pysecrets.token_bytes(16)
        iv = pysecrets.token_bytes(12)
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(PASSWORD.encode())
        ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), None)
        return {"salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
                "ct": base64.b64encode(ct).decode(), "iter": ITER}

    def dec(blob):
        salt = base64.b64decode(blob["salt"]); iv = base64.b64decode(blob["iv"])
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=blob.get("iter", ITER)).derive(PASSWORD.encode())
        return json.loads(AESGCM(key).decrypt(iv, base64.b64decode(blob["ct"]), None).decode())

    # история хранится тоже шифрованной
    hist_enc = os.path.join(DATA, "history.enc.json")
    history = []
    if os.path.exists(hist_enc):
        try:
            with open(hist_enc, encoding="utf-8") as f:
                history = dec(json.load(f))
        except Exception:
            print("WARN: history.enc.json не расшифровалась текущим паролем — начинаю историю заново")
            history = []
    history = [h for h in history if h["day"] != y_str]
    history.append({"day": y_str, **by_day[y_str],
                    "contr": {k: v["leads"] for k, v in contr_yest.items()},
                    "ownerless": len([p for p in people if p["day"] == y_str and
                                      (p["mgr"] in ("(нет)", "Администратор Портала") or p["mgr"].startswith("ID "))])})
    history = sorted(history, key=lambda h: h["day"])[-60:]
    snapshot["history"] = history
    with open(hist_enc, "w", encoding="utf-8") as f:
        json.dump(enc(history), f)
    with open(os.path.join(DATA, "data.enc.js"), "w", encoding="utf-8") as f:
        f.write("window.MONITOR_ENC=" + json.dumps(enc(snapshot)) + ";")
    # открытые файлы не оставляем
    for fn in ("data.js", "latest.json", "history.json"):
        p = os.path.join(DATA, fn)
        if os.path.exists(p):
            os.remove(p)
else:
    # локальный режим без пароля — открытые файлы
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.MONITOR_DATA=" + json.dumps(snapshot, ensure_ascii=False) + ";")

print(f"OK: {len(people)} людей за 8 дней | сегодня {by_day[t_str]['leads']} рекл. лидов | алертов {len(alerts)} | режим: {'ENC' if PASSWORD else 'open'}")
