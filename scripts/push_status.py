# -*- coding: utf-8 -*-
"""Публикация статуса конвейера агентов (/hypotheses) на дашборд монитора.
Вызывается оркестратором после сборки пакета гипотез:
python scripts/push_status.py '{"date":"2026-07-08","focus":"Астана","generated":6,"pass":3,"dead":2,"controller":"APPROVED","scripts":4,"landings":1,"top":[{"name":"...","metric":"...","status":"ждёт запуска"}]}'
Пишет docs/data/pipeline.json; коммит/пуш делает вызывающий."""
import json, os, sys

payload = json.loads(sys.argv[1])
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(root, "docs", "data", "pipeline.json")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
print("pipeline.json updated:", payload.get("date"), payload.get("focus"))
