"""Report assembly (deterministic insights) and CSV / Excel / PDF generation, all in memory."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..money import fmt
from ..util import day_start
from . import analytics as an
from . import anomaly
from . import goals as gs

CSV_HEADER = ["Date", "Description", "Type", "Category", "Amount", "Currency", "Notes"]


def _major(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    major, frac = divmod(abs(minor), 100)
    return f"{sign}{major}.{frac:02d}"


def _safe_cell(v: str) -> str:
    """Neutralise spreadsheet formula injection."""
    return "'" + v if v and v[0] in "=+-@\t\r" else v


def tx_filter(uid, date_from: date | None, date_to: date | None) -> dict:
    f: dict = {"user_id": uid}
    if date_from or date_to:
        f["date"] = {}
        if date_from:
            f["date"]["$gte"] = day_start(date_from)
        if date_to:
            f["date"]["$lt"] = day_start(date_to) + timedelta(days=1)
    return f


def csv_stream(db, uid, date_from, date_to, limit: int):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    yield buf.getvalue()
    cur = db.transactions.find(tx_filter(uid, date_from, date_to)).sort([("date", 1), ("_id", 1)]).limit(limit).batch_size(500)
    for d in cur:
        buf.seek(0)
        buf.truncate()
        w.writerow([d["date"].date().isoformat(), _safe_cell(d["description"]), d["type"], d["category"], _major(d["amount"]),
                    d.get("currency", "INR"), _safe_cell(d.get("notes") or "")])
        yield buf.getvalue()


def excel_bytes(db, uid, date_from, date_to, limit: int) -> bytes:
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Transactions")
    ws.append(CSV_HEADER)
    for d in db.transactions.find(tx_filter(uid, date_from, date_to)).sort([("date", 1), ("_id", 1)]).limit(limit).batch_size(500):
        ws.append([d["date"].date().isoformat(), _safe_cell(d["description"]), d["type"], d["category"], Decimal(d["amount"]) / 100,
                   d.get("currency", "INR"), _safe_cell(d.get("notes") or "")])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def monthly_report(db, user: dict, period: str, today: date | None = None) -> dict:
    uid, cur = user["_id"], user.get("currency", "INR")
    today = today or datetime.now(timezone.utc).date()
    dash = an.dashboard(db, uid, period, today)
    t = dash["totals"]
    perf = an.budget_performance(db, uid, period)
    goals = [gs.goal_view(g, today) for g in db.financial_goals.find({"user_id": uid}).limit(100)]
    prev = dash["vs_previous_month"]
    insights = []
    if not dash["has_transactions"] or (t["income"] == 0 and t["net_expense"] == 0):
        insights.append(f"No transactions were recorded for {period}.")
    else:
        if t["savings_pct"] is not None:
            word = "saved" if t["net_cash_flow"] >= 0 else "overspent by"
            insights.append(f"In {period} you earned {fmt(t['income'], cur)}, spent {fmt(t['net_expense'], cur)} and {word} {fmt(abs(t['net_cash_flow']), cur)} ({t['savings_pct']}% of income).")
        else:
            insights.append(f"You spent {fmt(t['net_expense'], cur)} with no recorded income in {period}.")
        if dash["categories"]:
            top = dash["categories"][0]
            insights.append(f"Your largest spending category was {top['category']} at {fmt(top['amount'], cur)} ({top['pct']}% of spending).")
        if prev and prev["expense_change_pct"] is not None:
            direction = "up" if prev["expense_change"] > 0 else "down" if prev["expense_change"] < 0 else "unchanged"
            insights.append(f"Spending is {direction} {abs(prev['expense_change_pct'])}% ({fmt(abs(prev['expense_change']), cur)}) versus the previous month.")
        fv = dash["fixed_variable"]
        if fv["fixed_pct"] is not None:
            insights.append(f"{fv['fixed_pct']}% of spending was fixed commitments ({fmt(fv['fixed'], cur)}); {fmt(fv['variable'], cur)} was variable.")
    if perf["exists"]:
        if perf["overspent_categories"]:
            insights.append("Over budget in: " + ", ".join(perf["overspent_categories"]) + ".")
        else:
            insights.append(f"All budgeted categories were within budget ({perf['utilization_pct']}% of the total budget used).")
    an_res = anomaly.detect(db, uid, cur)
    n_an = len(an_res["large_transactions"]) + len(an_res["category_spikes"]) + len(an_res["possible_duplicates"])
    if n_an:
        insights.append(f"{n_an} item(s) are flagged for review on the Analytics page (unusual amounts, spikes or possible duplicates).")
    return {"period": period, "currency": cur, "generated_for": user["name"], "totals": t, "categories": dash["categories"], "fixed_variable": dash["fixed_variable"],
            "vs_previous_month": prev, "budget": perf, "goals": goals, "insights": insights,
            "health_score": an.health_score(db, uid, today),
            "disclaimer": "Generated from your imported and manually entered transactions. Figures are not financial advice."}


def pdf_bytes(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    cur = rep["currency"]
    styles = getSampleStyleSheet()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title=f"Financial report {rep['period']}")
    story = [Paragraph(f"Monthly Financial Report - {rep['period']}", styles["Title"]), Paragraph(f"Prepared for {rep['generated_for']}", styles["Normal"]), Spacer(1, 8)]

    def table(data, widths=None):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                               ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]))
        return t

    t = rep["totals"]
    story += [Paragraph("Summary", styles["Heading2"]), table([
        ["Measure", "Amount"], ["Income", fmt(t["income"], cur)], ["Spending (net of refunds)", fmt(t["net_expense"], cur)],
        ["Net savings", fmt(t["net_cash_flow"], cur)], ["Savings rate", f"{t['savings_pct']}%" if t["savings_pct"] is not None else "n/a"]], [90 * mm, 70 * mm]), Spacer(1, 8)]
    if rep["categories"]:
        story += [Paragraph("Spending by category", styles["Heading2"]),
                  table([["Category", "Amount", "Share"]] + [[c["category"], fmt(c["amount"], cur), f"{c['pct']}%" if c["pct"] is not None else ""] for c in rep["categories"]], [70 * mm, 50 * mm, 40 * mm]), Spacer(1, 8)]
    b = rep["budget"]
    if b["exists"]:
        story += [Paragraph("Budget performance", styles["Heading2"]),
                  table([["Category", "Budget", "Actual", "Remaining"]] + [[r["category"], fmt(r["budget"], cur), fmt(r["actual"], cur), fmt(r["remaining"], cur)] for r in b["rows"]]), Spacer(1, 8)]
    if rep["goals"]:
        story += [Paragraph("Savings goals", styles["Heading2"]),
                  table([["Goal", "Saved", "Target", "Progress"]] + [[g["name"], fmt(g["current_amount"], cur), fmt(g["target_amount"], cur), f"{g['progress_pct']}%"] for g in rep["goals"]]), Spacer(1, 8)]
    story.append(Paragraph("Insights", styles["Heading2"]))
    story += [Paragraph("- " + i, styles["Normal"]) for i in rep["insights"]]
    hs = rep["health_score"]
    if hs["score"] is not None:
        story += [Spacer(1, 6), Paragraph(f"Financial health score: {hs['score']}/100", styles["Heading3"]), Paragraph(hs["note"], styles["Italic"])]
    story += [Spacer(1, 10), Paragraph(rep["disclaimer"], styles["Italic"])]
    doc.build(story)
    return out.getvalue()

