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

from reportlab.lib import colors
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

# Fixed column widths (pt)
_W_ACRISS = 28 * mm
_W_DIAS   = 14 * mm
_W_REC_T  = 30 * mm
_W_REC_D  = 30 * mm
_W_CAT    = 82 * mm             # longest category name ~ "Estándar Premium Automático"
_W_FIXED  = _W_ACRISS + _W_CAT + _W_DIAS + _W_REC_T + _W_REC_D

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(v: Optional[Decimal]) -> str:
    return f"{v:.2f}" if v is not None else "—"


def _date_label(d: Optional[date]) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


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
    }


# ── Table styling ─────────────────────────────────────────────────────────────

_HEADER_BG  = colors.HexColor("#1a1a2e")
_ROW_ALT    = colors.HexColor("#f4f4f6")
_GRID_COLOR = colors.HexColor("#d4d4d8")


def _table_style() -> TableStyle:
    return TableStyle([
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
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _ROW_ALT]),
        # Numeric columns right-aligned (Días, Rec.total, Rec./día, providers)
        ("ALIGN",         (2, 1), (-1, -1), "RIGHT"),
        # Padding
        ("TOPPADDING",    (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 0.25, _GRID_COLOR),
    ])


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
            ["ACRISS", "Categoría", "Días", "Rec. total (€)", "Rec./día (€)"]
            + [p.capitalize() for p in result.providers]
        )

        # One section per zone (rows are already ordered zone 0, 1, 2…).
        for zone_idx, zone_rows_iter in groupby(result.rows, key=lambda r: r.zone_index):
            zone_rows: list[ExportRow] = list(zone_rows_iter)
            first = zone_rows[0]
            n_zones = _total_zones(result.rows)

            # Section heading.
            heading = f"Temporada {zone_idx + 1} de {n_zones}"
            if first.zone_desde and first.zone_hasta:
                heading += (
                    f"  ·  {_date_label(first.zone_desde)}"
                    f" – {_date_label(first.zone_hasta)}"
                )
            story.append(Paragraph(heading, st["section"]))
            story.append(Spacer(1, 2 * mm))

            # Table data.
            table_data = [col_headers]
            for row in zone_rows:
                table_data.append(
                    [
                        row.acriss_code,
                        row.categoria,
                        str(row.duracion_dias),
                        _fmt(row.recomendado_total),
                        _fmt(row.recomendado_per_day),
                    ]
                    + [_fmt(row.provider_prices.get(p)) for p in result.providers]
                )

            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            tbl.setStyle(_table_style())
            story.append(tbl)
            story.append(Spacer(1, 6 * mm))

        doc.build(story)
        return buf.getvalue()


def _total_zones(rows: list[ExportRow]) -> int:
    """Number of distinct zone_index values in the row list."""
    return len({r.zone_index for r in rows})
