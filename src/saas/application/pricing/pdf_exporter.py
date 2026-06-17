"""Format an ExportResult as a wide-format PDF (reportlab, no HTML engine).

Layout:
  - Document header: tenant name + "Informe de tarifas cruzadas" + export date.
  - One section per zone: heading "Temporada N de T · DD/MM/YYYY – DD/MM/YYYY".
  - Per-section table (wide format, same columns as CSV):
      ACRISS | Categoría | Días | Rec. total | Rec./día | <provider…>
  - No provider colours, no badges, no calculation pipeline.  Legible, printable.

Page: A4 landscape (wider than portrait; fits up to 5 providers comfortably).
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from itertools import groupby
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .export_service import ExportResult, ExportRow

# ── Page geometry ─────────────────────────────────────────────────────────────

_PAGE = landscape(A4)           # (841.89, 595.28) pt
_MARGIN = 15 * mm
_USABLE_W = _PAGE[0] - 2 * _MARGIN

# Fixed column widths (pt). ACRISS holds a 4-char code and Categoría wraps,
# so both are kept tight to hand the freed width to the provider columns —
# that is where the model text needs room.
_W_ACRISS = 16 * mm
_W_DIAS   = 14 * mm
_W_REC_T  = 30 * mm
_W_REC_D  = 30 * mm
_W_CAT    = 56 * mm             # longest category name ~ "Estándar Premium Automático" (wraps)
_W_FIXED  = _W_ACRISS + _W_CAT + _W_DIAS + _W_REC_T + _W_REC_D

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(v: Optional[Decimal]) -> str:
    """Spanish money format: dot thousands, comma decimals (1.046,82)."""
    if v is None:
        return "—"
    s = f"{v:,.2f}"  # en-US grouping: "1,046.82"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _date_label(d: Optional[date]) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


_MODEL_MAXLEN = 30  # provider model truncation; wider provider columns now fit more


def _truncate(s: str, n: int = _MODEL_MAXLEN) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ── Styles ────────────────────────────────────────────────────────────────────

def _build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ExportTitle",
            parent=base["Normal"],
            fontSize=13,
            leading=16,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "ExportSubtitle",
            parent=base["Normal"],
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#666666"),
        ),
        "section": ParagraphStyle(
            "ExportSection",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            fontName="Helvetica-Bold",
            spaceBefore=4 * mm,
        ),
        "empty": ParagraphStyle(
            "ExportEmpty",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#666666"),
        ),
        # Category cell with a models sub-line (rendered once per ACRISS group).
        "cat": ParagraphStyle(
            "ExportCat",
            parent=base["Normal"],
            fontSize=6.5,
            leading=8,
            fontName="Helvetica",
        ),
        # Provider cell: right-aligned price with a small grey model sub-line
        # (rendered once per ACRISS group).
        "prov": ParagraphStyle(
            "ExportProv",
            parent=base["Normal"],
            fontSize=6.5,
            leading=8,
            fontName="Helvetica",
            alignment=TA_RIGHT,
        ),
    }


def _prov_cell(price: str, model: str, style: ParagraphStyle, *, bold: bool = False) -> Paragraph:
    """Provider price with a small grey model sub-line (truncated).

    bold: render the price in bold (this provider set the recommended base).
    """
    # Base price: bold, dark. Non-base: muted grey (info, but low-noise).
    price_html = (
        f"<b>{escape(price)}</b>" if bold
        else f'<font color="#9a9a9a">{escape(price)}</font>'
    )
    # Model on top as a small grey label (uppercased to normalise), price below.
    return Paragraph(
        f'<font size="5" color="#888888">{escape(_truncate(model).upper())}</font><br/>'
        f"{price_html}",
        style,
    )


def _cat_cell(categoria: str, examples: str, style: ParagraphStyle) -> Paragraph:
    """Category name with a small grey models sub-line ('FIAT 500, … O SIMILAR')."""
    return Paragraph(
        f"{escape(categoria)}<br/>"
        f'<font size="5.5" color="#888888">{escape(examples.upper())}</font>',
        style,
    )


# ── Table styling ─────────────────────────────────────────────────────────────

_HEADER_BG  = colors.HexColor("#1a1a2e")
_ROW_ALT    = colors.HexColor("#f4f4f6")
_GRID_COLOR = colors.HexColor("#d4d4d8")
_PROV_GREY  = colors.HexColor("#9a9a9a")   # non-base provider prices: present but muted
_INK        = colors.HexColor("#1a1a2e")   # base provider price: dark, like the rest

_N_FIXED = 5   # ACRISS, Categoría, Días, Rec. total, Rec./día — providers follow


def _table_style(
    groups: list[tuple[int, int]],
    extra_cmds: Optional[list] = None,
) -> TableStyle:
    """Style for one zone table.

    groups: (first_row, last_row) table-coordinate ranges, one per ACRISS
    category. Within each group the ACRISS and Categoría cells are merged
    (printed once) and the whole band gets a single background colour that
    alternates white/grey between consecutive categories — so each category
    reads as one block instead of a wall of repeated codes.

    extra_cmds: per-cell commands appended last (e.g. bold the base provider's
    price cell on a given row).
    """
    cmds = [
        # Header row
        ("BACKGROUND",    (0, 0), (-1, 0),  _HEADER_BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  7),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        ("VALIGN",        (0, 0), (-1, 0),  "MIDDLE"),
        # Data rows
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 6.5),
        # Días (col 2) and Rec. total (col 3) in bold — the two figures the
        # client reads first.
        ("FONTNAME",      (2, 1), (3, -1), "Helvetica-Bold"),
        # Provider prices are reference info: mute them grey by default; the
        # base provider's cell is overridden back to dark below (extra_cmds).
        ("TEXTCOLOR",     (_N_FIXED, 1), (-1, -1), _PROV_GREY),
        # Numeric columns right-aligned (Días, Rec.total, Rec./día, providers)
        ("ALIGN",         (2, 1), (-1, -1), "RIGHT"),
        # Centre everything vertically so the merged ACRISS/Categoría labels
        # sit in the middle of their block.
        ("VALIGN",        (0, 1), (-1, -1), "MIDDLE"),
        # Padding
        ("TOPPADDING",    (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 0.25, _GRID_COLOR),
    ]
    for gi, (start, end) in enumerate(groups):
        bg = colors.white if gi % 2 == 0 else _ROW_ALT
        cmds.append(("BACKGROUND", (0, start), (-1, end), bg))
        if end > start:
            cmds.append(("SPAN", (0, start), (0, end)))  # ACRISS column
            cmds.append(("SPAN", (1, start), (1, end)))  # Categoría column
    if extra_cmds:
        cmds.extend(extra_cmds)
    return TableStyle(cmds)


# ── Exporter ──────────────────────────────────────────────────────────────────

class PdfExporter:
    def export(self, result: ExportResult, *, tenant_name: str = "") -> bytes:
        """Return PDF bytes for all zones in result.

        tenant_name: shown in the document header (white-label; never the
        internal product name).
        """
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=_PAGE,
            leftMargin=_MARGIN,
            rightMargin=_MARGIN,
            topMargin=_MARGIN,
            bottomMargin=_MARGIN,
            title=f"Tarifas — {tenant_name}" if tenant_name else "Tarifas",
        )

        st = _build_styles()
        story = []

        # Document header.
        title_text = tenant_name or "Informe de tarifas cruzadas"
        story.append(Paragraph(title_text, st["title"]))
        story.append(Paragraph(
            f"Informe de tarifas cruzadas · {date.today().strftime('%d/%m/%Y')}",
            st["subtitle"],
        ))
        story.append(Spacer(1, 4 * mm))

        if not result.rows:
            story.append(Paragraph("Sin datos disponibles.", st["empty"]))
            doc.build(story)
            return buf.getvalue()

        # Provider column width: split remaining space evenly.
        n_prov = len(result.providers)
        prov_w = max(22 * mm, (_USABLE_W - _W_FIXED) / max(n_prov, 1))
        col_widths = [_W_ACRISS, _W_CAT, _W_DIAS, _W_REC_T, _W_REC_D] + [prov_w] * n_prov

        col_headers = (
            ["ACRISS", "Categoría", "Días", "Total (€)", "Día (€)"]
            + [p.capitalize() for p in result.providers]
        )

        # One section per zone (rows are already ordered zone 0, 1, 2…).
        for zone_idx, zone_rows_iter in groupby(result.rows, key=lambda r: r.zone_index):
            zone_rows: list[ExportRow] = list(zone_rows_iter)
            first = zone_rows[0]
            n_zones = result.total_zones or _total_zones(result.rows)

            # Section heading.
            heading = f"Temporada {zone_idx + 1} de {n_zones}"
            if first.zone_desde and first.zone_hasta:
                heading += (
                    f"  ·  {_date_label(first.zone_desde)}"
                    f" – {_date_label(first.zone_hasta)}"
                )
            story.append(Paragraph(heading, st["section"]))
            story.append(Spacer(1, 2 * mm))

            # Table data. ACRISS + Categoría (with the models sub-line) print
            # once per group; the merged cells and group banding do the rest.
            # Provider models show once per group (first row); the base
            # provider's price is bold on every row of its cell.
            table_data = [col_headers]
            groups: list[tuple[int, int]] = []   # (first_row, last_row) per category
            base_bold_cmds: list = []
            prev_code: Optional[str] = None
            group_start = 1
            for i, row in enumerate(zone_rows):
                r = i + 1  # table row (header is row 0)
                first_of_group = row.acriss_code != prev_code
                if first_of_group:
                    if prev_code is not None:
                        groups.append((group_start, r - 1))
                    group_start = r
                    prev_code = row.acriss_code
                    acriss_cell: object = row.acriss_code
                    cat_cell: object = (
                        _cat_cell(row.categoria, row.catalog_examples, st["cat"])
                        if row.catalog_examples
                        else row.categoria
                    )
                else:
                    acriss_cell = ""
                    cat_cell = ""

                prov_cells: list[object] = []
                for pj, p in enumerate(result.providers):
                    price = _fmt(row.provider_prices.get(p))
                    is_base = p == row.base_provider
                    model = row.provider_models.get(p, "")
                    if first_of_group and model:
                        prov_cells.append(_prov_cell(price, model, st["prov"], bold=is_base))
                    else:
                        prov_cells.append(price)
                        if is_base:
                            col = _N_FIXED + pj
                            base_bold_cmds.append(
                                ("FONTNAME", (col, r), (col, r), "Helvetica-Bold")
                            )
                            # Override the muted grey back to dark for the base.
                            base_bold_cmds.append(
                                ("TEXTCOLOR", (col, r), (col, r), _INK)
                            )

                table_data.append(
                    [
                        acriss_cell,
                        cat_cell,
                        str(row.duracion_dias),
                        _fmt(row.recomendado_total),
                        _fmt(row.recomendado_per_day),
                    ]
                    + prov_cells
                )
            groups.append((group_start, len(zone_rows)))  # close the final group

            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            tbl.setStyle(_table_style(groups, base_bold_cmds))
            story.append(tbl)
            story.append(Spacer(1, 6 * mm))

        doc.build(story)
        return buf.getvalue()


def _total_zones(rows: list[ExportRow]) -> int:
    """Number of distinct zone_index values in the row list."""
    return len({r.zone_index for r in rows})
