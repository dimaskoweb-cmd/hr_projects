#!/usr/bin/env python3
"""Генератор A4 PDF для HR: мост «задача компании → мой опыт», карта совпадения, SWOT интеграции.

Вход: JSON (схема в README.md). Выход: PDF на 2 страницы A4.
Запуск:  python3 tools/build_pdf.py examples/demo.json -o out.pdf

В PDF нет данных о компании (рейтинги, суды, надёжность) — они остаются в чате.
"""
import argparse
import json
import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from xml.sax.saxutils import escape

# --- палитра: референс dataviz (синяя последовательная шкала, нейтральные чернила) ---
INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#898781")
GRID = colors.HexColor("#e1e0d9")
TINT = colors.HexColor("#f3f6fb")
ACCENT = colors.HexColor("#256abf")
HEAT = {0: "#f0efec", 1: "#cde2fb", 2: "#9ec5f4", 3: "#6da7ec", 4: "#256abf", 5: "#0d366b"}
HEAT_TEXT = {0: INK, 1: INK, 2: INK, 3: INK, 4: colors.white, 5: colors.white}
MATCH_LABEL = {5: "Прямое", 4: "Очень близкое", 3: "Релевантная база",
               2: "Частичное", 1: "Слабая связь", 0: "Нет подтверждения"}
IMPORTANCE_W = {"Критично": 4, "Важно": 3, "Средне": 2, "Второстепенно": 1}
SWOT_COLORS = {"S": "#2a78d6", "W": "#eb6834", "O": "#1baf7a", "T": "#4a3aa7"}
SWOT_TITLES = {"S": "S · Сильные стороны — что я приношу",
               "W": "W · Зоны роста — что я учитываю",
               "O": "O · Возможности — что мы можем получить вместе",
               "T": "T · Риски интеграции — и как я их снимаю"}

MARGIN = 14 * mm
FONT_DIR = os.environ.get("HR_FONT_DIR", "/usr/share/fonts/truetype/dejavu")


def register_fonts():
    spec = [("DV", "DejaVuSansCondensed.ttf", "DejaVuSans.ttf"),
            ("DV-B", "DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"),
            ("DV-I", "DejaVuSansCondensed-Oblique.ttf", "DejaVuSans-Oblique.ttf")]
    for name, preferred, fallback in spec:
        path = os.path.join(FONT_DIR, preferred)
        if not os.path.exists(path):
            path = os.path.join(FONT_DIR, fallback)
        if not os.path.exists(path):
            sys.exit(f"Не найден шрифт с кириллицей: {preferred} / {fallback} в {FONT_DIR} "
                     f"(задайте HR_FONT_DIR)")
        pdfmetrics.registerFont(TTFont(name, path))
    pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-B")


# --- проверка входных данных ---
def validate(data):
    errors, warnings = [], []
    for key in ("meta", "thesis", "bridge", "heatmap", "swot"):
        if key not in data:
            errors.append(f"нет раздела '{key}'")
    if errors:
        return errors, warnings
    for key in ("candidate", "candidate_title", "position", "company", "date"):
        if not data["meta"].get(key):
            errors.append(f"meta.{key} пустое")
    for i, row in enumerate(data["heatmap"], 1):
        if row.get("importance") not in IMPORTANCE_W:
            errors.append(f"heatmap[{i}].importance: допустимо {list(IMPORTANCE_W)}")
        if not isinstance(row.get("match"), int) or not 0 <= row["match"] <= 5:
            errors.append(f"heatmap[{i}].match: целое 0–5")
    for q in "SWOT":
        items = data["swot"].get(q, [])
        if not items:
            errors.append(f"swot.{q} пустой")
        if len(items) > 5:
            warnings.append(f"swot.{q}: {len(items)} пунктов, страница может переполниться")
    # правило проекта: уровень английского — C1/B2, C2 не использовать (латиница и кириллица)
    flat = json.dumps(data, ensure_ascii=False)
    if re.search(r"(?<![A-Za-zА-Яа-я0-9])[CС]2(?![A-Za-zА-Яа-я0-9])", flat):
        errors.append("найден уровень «C2»: по правилу проекта английский указывается как C1/B2")
    # решения кандидата 2026-10-08: Dorax не использовать; должность — заместитель исполнительного директора
    if re.search(r"dorax", flat, re.I):
        errors.append("найдено «Dorax»: по решению кандидата эта позиция в откликах не используется")
    if re.search(r"заместител\w*\s+генеральн", flat, re.I):
        errors.append("найдено «заместитель генерального директора»: верно «заместитель исполнительного директора»")
    return errors, warnings


def weighted_match(rows):
    total = sum(IMPORTANCE_W[r["importance"]] * 5 for r in rows)
    got = sum(IMPORTANCE_W[r["importance"]] * r["match"] for r in rows)
    return got / total if total else 0.0


def fmt_date(s):
    parts = s.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else s


def P(text, style):
    return Paragraph(escape(str(text)), style)


def make_styles():
    base = dict(fontName="DV", fontSize=9, leading=12.5, textColor=INK)
    return {
        "name": ParagraphStyle("name", fontName="DV-B", fontSize=20, leading=24, textColor=INK),
        "sub": ParagraphStyle("sub", fontName="DV", fontSize=10, leading=14, textColor=INK2),
        "h": ParagraphStyle("h", fontName="DV-B", fontSize=11, leading=14, textColor=INK,
                            spaceBefore=12, spaceAfter=5),
        "body": ParagraphStyle("body", **base),
        "bold": ParagraphStyle("bold", **{**base, "fontName": "DV-B"}),
        "small": ParagraphStyle("small", fontName="DV", fontSize=8, leading=10.5, textColor=INK2),
        "th": ParagraphStyle("th", fontName="DV-B", fontSize=8, leading=10, textColor=INK2),
        "arrow": ParagraphStyle("arrow", fontName="DV-B", fontSize=12, leading=14,
                                textColor=ACCENT, alignment=TA_CENTER),
        "bullet": ParagraphStyle("bullet", **{**base, "leftIndent": 9, "bulletIndent": 0,
                                              "spaceAfter": 2.5}),
        "swot_h": ParagraphStyle("swot_h", fontName="DV-B", fontSize=10.5, leading=13.5,
                                 textColor=INK, spaceAfter=5),
        "swot_bullet": ParagraphStyle("swot_bullet", **{**base, "fontSize": 10, "leading": 14,
                                                        "leftIndent": 10, "bulletIndent": 0,
                                                        "spaceAfter": 4}),
    }


def heat_cell_style(m):
    return ParagraphStyle(f"hc{m}", fontName="DV-B", fontSize=8.5, leading=10.5,
                          alignment=TA_CENTER, textColor=HEAT_TEXT[m])


def build_story(data, S, W):
    meta, story = data["meta"], []

    # --- шапка ---
    story.append(P(meta["candidate"], S["name"]))
    story.append(P(meta["candidate_title"], S["sub"]))
    if meta.get("contacts"):
        story.append(P(meta["contacts"], S["small"]))
    story.append(Spacer(1, 4))
    story.append(P(f"Отклик на вакансию: {meta['position']} — {meta['company']}", S["bold"]))
    if meta.get("demo"):
        story.append(P("ДЕМО: компания и вакансия вымышлены, документ показывает формат", S["small"]))
    story.append(Spacer(1, 8))

    # --- тезис ---
    th = data["thesis"]
    box = Table([[[Paragraph(f"<b>Работодатель ищет:</b> {escape(th['employer_seeks'])}", S["body"]),
                   Spacer(1, 4),
                   Paragraph(f"<b>Чем я могу быть полезен:</b> {escape(th['candidate_value'])}",
                             S["body"])]]], colWidths=[W])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT),
                             ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(box)

    # --- мост ---
    story.append(P("Мост: от задачи компании к моему опыту", S["h"]))
    cw = [56 * mm, 8 * mm, 66 * mm, 8 * mm, W - 138 * mm]
    rows = [[P("ЗАДАЧА КОМПАНИИ", S["th"]), "", P("МОЙ ОПЫТ", S["th"]), "",
             P("ЧТО ЭТО ДАЁТ КОМПАНИИ", S["th"])]]
    for b in data["bridge"]:
        rows.append([P(b["task"], S["bold"]), P("→", S["arrow"]), P(b["experience"], S["body"]),
                     P("→", S["arrow"]), P(b["effect"], S["body"])])
    bridge = Table(rows, colWidths=cw, repeatRows=1)
    bridge.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.5, GRID),
                                ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(bridge)

    # --- карта совпадения ---
    pct = weighted_match(data["heatmap"])
    story.append(P("Карта совпадения с требованиями вакансии", S["h"]))
    story.append(Paragraph(f"<b>Совпадение с задачей: {round(pct * 100)}%</b> "
                           f"<font color='#52514e'>(взвешено по важности требований)</font>", S["body"]))
    story.append(Spacer(1, 3))
    p = min(max(pct, 0.001), 0.999)
    bar = Table([["", ""]], colWidths=[W * p, W * (1 - p)], rowHeights=[4 * mm])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), ACCENT), ("BACKGROUND", (1, 0), (1, 0), GRID),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(bar)
    story.append(Spacer(1, 8))

    hcw = [54 * mm, 27 * mm, 32 * mm, W - 113 * mm]
    hrows = [[P("ТРЕБОВАНИЕ", S["th"]), P("ВАЖНОСТЬ", S["th"]), P("СОВПАДЕНИЕ", S["th"]),
              P("ОСНОВАНИЕ", S["th"])]]
    cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for i, r in enumerate(data["heatmap"], start=1):
        m = r["match"]
        hrows.append([P(r["requirement"], S["body"]), P(r["importance"], S["small"]),
                      P(f"{m} · {MATCH_LABEL[m]}", heat_cell_style(m)), P(r["basis"], S["small"])])
        cmds.append(("BACKGROUND", (2, i), (2, i), colors.HexColor(HEAT[m])))
    heat = Table(hrows, colWidths=hcw, repeatRows=1)
    heat.setStyle(TableStyle(cmds))
    story.append(heat)
    story.append(Spacer(1, 8))

    # легенда шкалы
    legend = Table([[Paragraph(f"{m} · {MATCH_LABEL[m]}", heat_cell_style(m)) for m in (5, 4, 3, 2, 1, 0)]],
                   colWidths=[W / 6] * 6)
    lcmds = [("LINEAFTER", (0, 0), (-2, -1), 1, colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for j, m in enumerate((5, 4, 3, 2, 1, 0)):
        lcmds.append(("BACKGROUND", (j, 0), (j, 0), colors.HexColor(HEAT[m])))
    legend.setStyle(TableStyle(lcmds))
    story.append(legend)
    story.append(Spacer(1, 3))
    story.append(P("Шкала: 5 — прямой подтверждённый опыт, 0 — подтверждения нет. Веса: критично ×4, "
                   "важно ×3, средне ×2, второстепенно ×1.", S["small"]))

    # --- SWOT ---
    story.append(PageBreak())
    story.append(P("SWOT: как я встраиваюсь в компанию", S["h"]))
    sw = data["swot"]
    cells = {}
    for q in "SWOT":
        items = [P(SWOT_TITLES[q], S["swot_h"])]
        items += [Paragraph(escape(t), S["swot_bullet"], bulletText="•") for t in sw[q]]
        cells[q] = items
    cw2 = W / 2
    grid = Table([[cells["S"], cells["W"]], [cells["O"], cells["T"]]], colWidths=[cw2, cw2])
    gcmds = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("BOX", (0, 0), (-1, -1), 0.5, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.5, GRID),
             ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]
    for (c, r), q in (((0, 0), "S"), ((1, 0), "W"), ((0, 1), "O"), ((1, 1), "T")):
        gcmds.append(("LINEABOVE", (c, r), (c, r), 3, colors.HexColor(SWOT_COLORS[q])))
    grid.setStyle(TableStyle(gcmds))
    story.append(grid)

    if data.get("closing"):
        story.append(Spacer(1, 12))
        end = Table([[Paragraph(escape(data["closing"]), S["body"])]], colWidths=[W])
        end.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT),
                                 ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        story.append(end)
    return story


def build(data, out_path):
    register_fonts()
    S = make_styles()
    W = A4[0] - 2 * MARGIN
    meta = data["meta"]
    footer_left = f"{meta['candidate']} · {meta['company']} · {fmt_date(meta['date'])}"

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 12 * mm, A4[0] - MARGIN, 12 * mm)
        canvas.setFont("DV", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 8 * mm, footer_left)
        canvas.drawRightString(A4[0] - MARGIN, 8 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(out_path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=16 * mm,
                          title=f"{meta['candidate']} — {meta['position']}",
                          author=meta["candidate"], subject=f"Отклик: {meta['company']}")
    frame = Frame(MARGIN, 16 * mm, W, A4[1] - MARGIN - 16 * mm, id="main",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="a4", frames=[frame], onPage=on_page)])
    doc.build(build_story(data, S, W))
    return doc.page


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    with open(args.json_path, encoding="utf-8") as f:
        data = json.load(f)
    errors, warnings = validate(data)
    for w in warnings:
        print(f"ВНИМАНИЕ: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    pages = build(data, args.out)
    print(f"OK: {args.out} · страниц: {pages} · совпадение {round(weighted_match(data['heatmap']) * 100)}%")


if __name__ == "__main__":
    main()
