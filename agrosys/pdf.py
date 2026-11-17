"""PDF of a shopping list with the cheapest market for each product."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fpdf import FPDF

GREEN = (31, 92, 74)
GREY = (110, 118, 112)


def _t(text) -> str:
    """Core PDF fonts are Latin-1: keep Spanish accents, replace anything else."""
    return str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1")


def _money(v) -> str:
    return f"${v:,.0f}".replace(",", ".")


class _Doc(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*GREEN)
        self.cell(0, 8, "AGROCAST - Cheapest purchase list", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*GREEN)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GREY)
        self.multi_cell(0, 3.6, _t("Wholesale prices published by DANE - SIPSA (daily bulletin and monthly series). Retail "
                                    "prices are usually higher. Forecasts are estimates, not guarantees. "
                                    f"Page {self.page_no()}"), align="C")


def shopping_list_pdf(shopping_list, lines, totals, user_name: str) -> bytes:
    now = datetime.now(ZoneInfo("America/Bogota"))
    pdf = _Doc(orientation="L", format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 5.5, _t(f"List: {shopping_list.name}    Prepared for: {user_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5.5, _t(f"Generated: {now:%Y-%m-%d %H:%M} (Colombia)    "
                        f"City filter: {shopping_list.city or 'all markets'}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    headers = ["Product", "Quantity", "Cheapest market", "Price date", "Price / unit", "Cost", "Median cost",
               "You save", "3-month outlook"]
    widths = [48, 18, 50, 32, 24, 24, 24, 22, 35]
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_fill_color(232, 241, 226)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, h, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8.2)
    ordered = sorted(lines, key=lambda l: -(l.get("saving") or 0))
    for line in ordered:
        p = line["product"]
        unit = p.unit.replace("1 ", "").replace("kilogramo", "kg")
        if line.get("cheapest") is None:
            cells = [p.name, f"{line['quantity']:g} {unit}", "No recent price", "", "", "", "", "", ""]
        else:
            best = line["cheapest"]
            src = "today" if line["source"] == "daily" else "month"
            o = line["outlook"]
            outlook = f"{o.label} ({float(o.change_3m_pct):+.1f}%)" if o else "No forecast"
            cells = [p.name, f"{line['quantity']:g} {unit}", best.market, f"{line['date']:%Y-%m-%d} ({src})",
                     _money(line["cheapest_price"]), _money(line["cost_cheapest"]), _money(line["cost_median"]),
                     _money(line["saving"]), outlook]
        for text, w in zip(cells, widths):
            pdf.cell(w, 6.5, _t(text)[:40], border=1)
        pdf.ln()
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(sum(widths[:5]), 7, "Total", border=1)
    pdf.cell(widths[5], 7, _money(totals["cheapest"]), border=1)
    pdf.cell(widths[6], 7, _money(totals["median"]), border=1)
    pdf.cell(widths[7], 7, _money(totals["saving"]), border=1)
    pdf.cell(widths[8], 7, "", border=1)
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(0, 4.5, _t(
        "How to read it: 'Cheapest market' is the wholesale market with the lowest price on the latest day published by "
        "DANE (fresh produce) or in the latest month (other products). 'Median cost' is the cost at the middle market, "
        "and 'You save' is the difference. The 3-month outlook comes from the AGROCAST forecast of the national price: "
        "Buy now = expected to rise, Wait = expected to fall."))
    return bytes(pdf.output())
