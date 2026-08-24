#!/usr/bin/env python3
"""Собирает курсы валют туроператоров и пишет rates.json для калькулятора доплаты.

Источник: tour-kurs.ru — таблица «Курсы валют туроператоров на сегодня».
Сайт проверяет официальные страницы туроператоров и указывает ссылку на каждого.

Запуск:
    python collect-rates.py                 # боевой: скачать и записать ../rates.json
    python collect-rates.py --from-file f   # тест: разобрать сохранённую страницу
    python collect-rates.py --out path      # куда писать json

Скрипт намеренно падает с ненулевым кодом, если данные выглядят подозрительно:
лучше оставить вчерашний курс, чем показать туристу мусор.
"""

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = "https://tour-kurs.ru/"
USER_AGENT = "Mozilla/5.0 (compatible; primetour-rates/1.0; +https://primetour.pro/)"

# Разумные границы курса, ₽ за единицу валюты. Всё, что вне — ошибка разбора.
RATE_MIN = 30.0
RATE_MAX = 300.0
MIN_OPERATORS = 5

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Короткие id для виджета: по домену туроператора.
ID_BY_HOST = {
    "anextour.ru": "anex",
    "bgoperator.ru": "bg",
    "intourist.ru": "intourist",
    "coral.ru": "coral",
    "pegast.ru": "pegas",
    "sunmar.ru": "sunmar",
    "funsuntravel.ru": "funsun",
    "fstravel.com": "funsun",
    "russian-express.ru": "rexpress",
    "teztour.ru": "tez",
    "tui.ru": "tui",
    "biblio-globus.ru": "bg",
}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", "replace")


def strip_tags(html):
    return re.sub(r"<[^>]+>", "", html)


def parse_number(cell_html):
    """«88,31 ₽» → 88.31. Возвращает None, если числа нет."""
    text = strip_tags(cell_html).replace("\xa0", " ").replace("&nbsp;", " ")
    match = re.search(r"(\d{1,3}(?:[ ]\d{3})*[.,]\d+|\d+[.,]\d+|\d+)", text)
    if not match:
        return None
    return float(match.group(1).replace(" ", "").replace(",", "."))


def parse_date(html):
    """Дата из заголовка «Курсы валют туроператоров на сегодня 23 августа 2026 г.»"""
    match = re.search(
        r"Курсы валют туроператоров на сегодня\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})",
        html, re.I,
    )
    if not match:
        return None
    day, month_name, year = match.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return "%02d.%02d.%s" % (int(day), month, year)


def today_table(html):
    """HTML первой таблицы после заголовка «... на сегодня ...»."""
    heading = re.search(r"Курсы валют туроператоров на сегодня", html, re.I)
    if not heading:
        raise ValueError("не найден заголовок таблицы «на сегодня» — вёрстка источника изменилась")
    start = html.find("<table", heading.end())
    end = html.find("</table>", start)
    if start == -1 or end == -1:
        raise ValueError("не найдена таблица после заголовка «на сегодня»")
    return html[start:end]


def parse_rows(table_html):
    operators = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if len(cells) < 3:
            continue

        name_match = re.search(r'class="font-medium"[^>]*>(.*?)<', cells[0], re.S)
        name = strip_tags(name_match.group(1)).strip() if name_match else strip_tags(cells[0]).strip()
        if not name or name.lower().startswith("туроператор"):
            continue

        host_match = re.search(r'href="https?://(?:www\.)?([^/"]+)', cells[0])
        host = host_match.group(1) if host_match else ""

        usd = parse_number(cells[1])
        eur = parse_number(cells[2])
        if usd is None or eur is None:
            continue

        operators.append({
            "id": ID_BY_HOST.get(host, re.sub(r"[^a-z0-9]", "", host.split(".")[0])[:12] or "op%d" % len(operators)),
            "name": name,
            "usd": round(usd, 4),
            "eur": round(eur, 4),
            "site": "https://%s/" % host if host else None,
        })
    return operators


def validate(payload):
    operators = payload["operators"]
    if len(operators) < MIN_OPERATORS:
        raise ValueError("разобрано только %d туроператоров, ожидали минимум %d" % (len(operators), MIN_OPERATORS))
    if not payload["date"]:
        raise ValueError("не разобрана дата курсов")
    for operator in operators:
        for currency in ("usd", "eur"):
            rate = operator[currency]
            if not (RATE_MIN <= rate <= RATE_MAX):
                raise ValueError("курс вне разумных границ: %s %s = %s" % (operator["name"], currency, rate))
        if operator["eur"] < operator["usd"]:
            raise ValueError("евро дешевле доллара у %s — похоже, колонки перепутаны" % operator["name"])


def build(html):
    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": parse_date(html),
        "source": SOURCE_URL,
        "operators": parse_rows(today_table(html)),
    }
    validate(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", help="разобрать сохранённую страницу вместо загрузки")
    parser.add_argument("--out", default="rates.json", help="куда писать результат")
    parser.add_argument("--dry-run", action="store_true", help="показать результат, но не писать файл")
    args = parser.parse_args()

    try:
        if args.from_file:
            with open(args.from_file, encoding="utf-8", errors="replace") as handle:
                html = handle.read()
        else:
            html = fetch(SOURCE_URL)
        payload = build(html)
    except Exception as error:
        print("Курсы не обновлены: %s" % error, file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("Записано %s: %d туроператоров на %s" % (args.out, len(payload["operators"]), payload["date"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
