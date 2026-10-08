#!/usr/bin/env python3
"""Карточка компании по ИНН через Checko API v2 (https://api.checko.ru/v2).

Запуск (из папки проекта):
    python tools/checko_fetch.py 7707049388
    python tools/checko_fetch.py 7707049388 --extra          # + суды, приставы, банкротства, проверки, Федресурс
    python tools/checko_fetch.py --ogrn 1027700198767

Ключ берётся из переменной окружения CHECKO_API_KEY или из файла .env
(строка CHECKO_API_KEY=...) в корне проекта либо в текущей папке.
Ключ нигде не печатается и не сохраняется в выходных файлах.

Результат: local/checko/<ИНН>_<дата>.json (сырые ответы) и .txt (краткая карточка).
Папка local/ в .gitignore, в репозиторий не попадает.

Только стандартная библиотека Python 3.8+.
Лимит бесплатного тарифа: 100 успешных запросов в сутки. Один запуск без --extra = 2 запроса.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.checko.ru/v2"
CORE_METHODS = ["company", "reliability"]
# Параметр inn/ogrn для этих методов в документации подтверждён для company и reliability;
# для остальных используется та же схема, при ошибке скрипт покажет текст ответа API.
EXTRA_METHODS = ["legal-cases", "enforcements", "bankruptcy-messages", "inspections", "fedresurs"]
TIMEOUT = 30


# ---------- ключ и ввод ----------

def load_key(search_dirs):
    """Ключ из окружения, затем из .env. Возвращает строку или None."""
    key = os.environ.get("CHECKO_API_KEY", "").strip()
    if key:
        return key
    for d in search_dirs:
        # ".env.txt" — Проводник Windows часто дописывает расширение при сохранении
        path = next((os.path.join(d, n) for n in (".env", ".env.txt")
                     if os.path.isfile(os.path.join(d, n))), None)
        if not path:
            continue
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == "CHECKO_API_KEY":
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
    return None


def valid_inn(inn):
    """Проверка длины и контрольных цифр ИНН (10 цифр — юрлицо, 12 — ИП)."""
    if not re.fullmatch(r"\d{10}|\d{12}", inn or ""):
        return False
    d = [int(c) for c in inn]
    if len(d) == 10:
        w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(a * b for a, b in zip(w, d[:9])) % 11 % 10 == d[9]
    w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    c1 = sum(a * b for a, b in zip(w1, d[:10])) % 11 % 10
    c2 = sum(a * b for a, b in zip(w2, d[:11])) % 11 % 10
    return c1 == d[10] and c2 == d[11]


def redact(text, key):
    return text.replace(key, "***") if key else text


# ---------- запросы ----------

def fetch_json(url):
    """GET -> (http_status, dict). Тело ошибки тоже разбирается: в нём meta.message."""
    req = urllib.request.Request(url, headers={"User-Agent": "hr_projects-checko/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"meta": {"status": "error", "message": body[:300]}}


def call(method, key, ident, fetch=fetch_json):
    """ident: {'inn': '...'} или {'ogrn': '...'}. Возвращает (ok, json, текст_ошибки)."""
    params = {"key": key}
    params.update(ident)
    url = "%s/%s?%s" % (API_BASE, method, urllib.parse.urlencode(params))
    try:
        status, data = fetch(url)
    except Exception as e:  # сеть, DNS, TLS — URL с ключом в сообщение не попадает
        return False, None, "%s: сетевая ошибка (%s)" % (method, redact(str(e), key))
    meta = (data or {}).get("meta", {})
    if status != 200 or meta.get("status") == "error":
        msg = redact(str(meta.get("message") or "без пояснения"), key)
        return False, data, "%s: HTTP %s, %s" % (method, status, msg)
    return True, data, None


# ---------- разбор ----------

def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime((s or "")[:10], fmt).date()
        except ValueError:
            continue
    return None


def age_text(reg, today):
    if not reg:
        return "н/д"
    months = (today.year - reg.year) * 12 + today.month - reg.month - (1 if today.day < reg.day else 0)
    y, m = divmod(max(months, 0), 12)
    return "%d г. %d мес." % (y, m)


def stop_factors(company, bankr_count, today):
    """Стоп-факторы шкалы проекта. Возвращает список строк (пусто — не найдено)."""
    out = []
    status = (company.get("Статус") or {}).get("Наим") or ""
    if company.get("Ликвид") or re.search(r"ликвид|банкрот|исключ|прекращ", status, re.I):
        out.append("статус: %s" % (status or "ликвидация"))
    reg = parse_date(company.get("ДатаРег"))
    young = bool(reg) and (today - reg).days < 365
    mass_addr = bool((company.get("ЮрАдрес") or {}).get("МассАдрес"))
    mass_dir = bool(company.get("МассРуковод"))
    if young and mass_addr and mass_dir:
        out.append("компания младше года: массовый адрес + массовый руководитель")
    if company.get("ДисквЛица"):
        out.append("в руководстве дисквалифицированное лицо")
    if company.get("Санкции"):
        out.append("организация в санкционных списках")
    if company.get("НедобПост"):
        out.append("в реестре недобросовестных поставщиков")
    if bankr_count:
        out.append("сообщения ЕФРСБ о банкротстве: %d" % bankr_count)
    return out


def build_card(company_resp, rel_resp, extra, today):
    """Текстовая карточка. Недостающие блоки помечаются «нет данных»."""
    L = []
    c = (company_resp or {}).get("data") or {}
    if c:
        reg = parse_date(c.get("ДатаРег"))
        L.append("%s" % (c.get("НаимСокр") or c.get("НаимПолн") or "н/д"))
        L.append("ИНН %s · ОГРН %s" % (c.get("ИНН", "н/д"), c.get("ОГРН", "н/д")))
        L.append("Статус: %s" % ((c.get("Статус") or {}).get("Наим") or "н/д"))
        L.append("Зарегистрирована: %s (возраст %s)" % (c.get("ДатаРег", "н/д"), age_text(reg, today)))
        L.append("Регион: %s" % ((c.get("Регион") or {}).get("Наим") or "н/д"))
        L.append("ОКВЭД: %s" % ((c.get("ОКВЭД") or {}).get("Наим") or "н/д"))
        cap = (c.get("УстКап") or {}).get("Сумма")
        L.append("Уставный капитал: %s" % ("{:,} руб.".format(cap).replace(",", " ") if cap is not None else "н/д"))
        L.append("Среднесписочная численность: %s" % c.get("СЧР", "нет данных"))
        L.append("Реестр МСП: %s" % ((c.get("РМСП") or {}).get("Кат") or "нет"))
        tax = c.get("Налоги") or {}
        if "СумУпл" in tax:
            L.append("Уплачено налогов за %s: %s руб." % (tax.get("СведУплГод", "?"), tax.get("СумУпл")))
        if tax.get("СумНедоим"):
            L.append("Недоимка по налогам: %s руб. (на %s)" % (tax.get("СумНедоим"), tax.get("НедоимДата", "?")))
        flags = []
        if (c.get("ЮрАдрес") or {}).get("МассАдрес"):
            flags.append("массовый адрес")
        if (c.get("ЮрАдрес") or {}).get("Недост"):
            flags.append("недостоверный адрес")
        for k, t in (("МассРуковод", "массовый руководитель"), ("МассУчред", "массовый учредитель"),
                     ("ДисквЛица", "дисквалифицированное лицо"), ("Санкции", "санкции"),
                     ("НедобПост", "недобросовестный поставщик")):
            if c.get(k):
                flags.append(t)
        L.append("Флаги ЕГРЮЛ: %s" % (", ".join(flags) if flags else "не выявлено"))
        bankr = len(c.get("ЕФРСБ") or [])
    else:
        L.append("Карточка организации: нет данных")
        bankr = 0
    L.append("")
    r = (rel_resp or {}).get("data") or {}
    if r:
        L.append("НАДЁЖНОСТЬ (оценка сервиса Checko, финансово-правовая, не отношение к персоналу)")
        L.append("Масштаб бизнеса: %s из 100" % r.get("Масштаб", "н/д"))
        L.append("Номинальность: %s" % ((r.get("Номинальность") or {}).get("Наим") or "н/д"))
        fr = r.get("ФинРиски") or {}
        L.append("Финансовые риски: уровень %s из 5 — %s" % (fr.get("Уровень", "н/д"), fr.get("Наим", "")))
        b = r.get("Баланс") or {}
        L.append("Баланс факторов: положительных %s%%, требуют внимания %s%%, негативных %s%%"
                 % (b.get("Полож", "?"), b.get("ТрВним", "?"), b.get("Негатив", "?")))
        neg = sorted([f for f in r.get("Факторы") or [] if f.get("Тип") == "Негатив"],
                     key=lambda f: -(f.get("Вес") or 0))
        for f in neg[:5]:
            L.append("  негатив: %s" % f.get("Текст"))
    else:
        L.append("Надёжность: нет данных")
    for name, resp in extra.items():
        L.append("")
        L.append("%s: %s" % (name, "получено, см. JSON" if resp else "нет данных"))
    L.append("")
    sf = stop_factors(c, bankr, today) if c else []
    if not c:
        L.append("СТОП-ФАКТОРЫ: нет данных (карточка организации не получена)")
    else:
        L.append("СТОП-ФАКТОРЫ: %s" % ("; ".join(sf) if sf else "не выявлены"))
    return "\n".join(L)


# ---------- запуск ----------

def run(args, key, fetch=fetch_json, today=None, out_dir=None):
    today = today or dt.date.today()
    ident = {"inn": args.inn} if args.inn else {"ogrn": args.ogrn}
    methods = list(CORE_METHODS) + (EXTRA_METHODS if args.extra else [])
    raw, errors = {}, []
    for m in methods:
        ok, data, err = call(m, key, ident, fetch)
        if ok:
            raw[m] = data
        else:
            errors.append(err)
            if m in CORE_METHODS and data is not None:
                raw[m + "__error"] = data.get("meta")
    extra = {m: raw.get(m) for m in methods if m not in CORE_METHODS}
    card = build_card(raw.get("company"), raw.get("reliability"), extra, today)
    last_meta = next((raw[m].get("meta") for m in reversed(methods) if m in raw), {}) or {}
    tail = ["", "Запросов за сегодня: %s · баланс: %s руб." % (
        last_meta.get("today_request_count", "н/д"), last_meta.get("balance", "н/д"))]
    if errors:
        tail += ["", "ОШИБКИ:"] + ["  " + e for e in errors]
    text = card + "\n" + "\n".join(tail)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        stem = "%s_%s" % (args.inn or args.ogrn, today.isoformat())
        with open(os.path.join(out_dir, stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, stem + ".txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return text, bool(errors)


def main(argv=None):
    p = argparse.ArgumentParser(description="Карточка компании по ИНН через Checko API")
    p.add_argument("inn", nargs="?", help="ИНН организации (10 цифр) или ИП (12)")
    p.add_argument("--ogrn", help="ОГРН вместо ИНН")
    p.add_argument("--extra", action="store_true",
                   help="дополнительно: суды, приставы, банкротства, проверки, Федресурс (+5 запросов)")
    args = p.parse_args(argv)
    if bool(args.inn) == bool(args.ogrn):
        p.error("укажите либо ИНН, либо --ogrn")
    if args.inn and not valid_inn(args.inn):
        p.error("ИНН %r не прошёл проверку длины и контрольных цифр" % args.inn)
    if args.ogrn and not re.fullmatch(r"\d{13}|\d{15}", args.ogrn):
        p.error("ОГРН должен содержать 13 цифр (ОГРНИП — 15)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key = load_key([root, os.getcwd()])
    if not key:
        sys.exit("Ключ не найден. Создайте файл .env в корне проекта со строкой CHECKO_API_KEY=ваш_ключ")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text, had_errors = run(args, key, out_dir=os.path.join(root, "local", "checko"))
    print(text)
    sys.exit(1 if had_errors else 0)


if __name__ == "__main__":
    main()
