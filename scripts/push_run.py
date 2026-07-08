# -*- coding: utf-8 -*-
"""Публикация журнала прогона агентов на страницу «Отдел».
Читает run.json (журнал конвейера /hypotheses), добавляет в шифрованный
docs/data/runs.enc.js (AES-256-GCM, пароль DASH_PASSWORD из env или токены/.env основной папки).
Запуск: python scripts/push_run.py <run.json>"""
import base64, json, os, secrets, sys

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "docs", "data", "runs.enc.js")
ITER = 300_000

pw = os.environ.get("DASH_PASSWORD", "")
if not pw:
    envp = os.path.join(os.path.dirname(ROOT), "токены", ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8"):
            if line.startswith("DASH_PASSWORD="):
                pw = line.strip().split("=", 1)[1]
if not pw:
    sys.exit("ERROR: DASH_PASSWORD не найден (env или токены/.env)")


def kdf(salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode())


def enc(obj):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    ct = AESGCM(kdf(salt)).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), None)
    return {"salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
            "ct": base64.b64encode(ct).decode(), "iter": ITER}


def dec(blob):
    return json.loads(AESGCM(kdf(base64.b64decode(blob["salt"]))).decrypt(
        base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None).decode())


run = json.load(open(sys.argv[1], encoding="utf-8"))
runs = []
if os.path.exists(RUNS):
    src = open(RUNS, encoding="utf-8").read()
    blob = json.loads(src[src.index("=") + 1:].rstrip(";"))
    try:
        runs = dec(blob)
    except Exception:
        print("WARN: runs.enc.js не расшифровался — начинаю заново")
runs = [r for r in runs if r.get("run_id") != run.get("run_id")]
runs.append(run)
runs = sorted(runs, key=lambda r: r.get("started", ""))[-30:]
os.makedirs(os.path.dirname(RUNS), exist_ok=True)
with open(RUNS, "w", encoding="utf-8") as f:
    f.write("window.OTDEL_ENC=" + json.dumps(enc(runs)) + ";")
print(f"runs.enc.js: {len(runs)} прогонов, добавлен {run.get('run_id')}")
