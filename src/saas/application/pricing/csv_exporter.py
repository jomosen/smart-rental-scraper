"""Format an ExportResult as wide-format CSV bytes.

Columns (fixed then one per provider in result.providers order):
  zona_index, zona_desde, zona_hasta, acriss_code, categoria,
  duracion_dias, recomendado_total, recomendado_per_day,
  <provider_key_1>, <provider_key_2>, ...

Encoding: utf-8-sig (BOM) so Excel on Windows opens it correctly.
Decimal values are formatted to 2 decimal places; absent prices → empty string.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Optional

from .export_service import ExportResult


def _fmt(v: Optional[Decimal]) -> str:
    return f"{v:.2f}" if v is not None else ""


class CsvExporter:
    def export(self, result: ExportResult) -> bytes:
        buf = io.StringIO()
        headers = [
            "zona_index", "zona_desde", "zona_hasta",
            "acriss_code", "categoria", "duracion_dias",
            "recomendado_total", "recomendado_per_day",
            *result.providers,
        ]
        writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\r\n")
        writer.writeheader()
        for row in result.rows:
            writer.writerow({
                "zona_index": row.zone_index,
                "zona_desde": row.zone_desde.isoformat() if row.zone_desde else "",
                "zona_hasta": row.zone_hasta.isoformat() if row.zone_hasta else "",
                "acriss_code": row.acriss_code,
                "categoria": row.categoria,
                "duracion_dias": row.duracion_dias,
                "recomendado_total": _fmt(row.recomendado_total),
                "recomendado_per_day": _fmt(row.recomendado_per_day),
                **{pkey: _fmt(row.provider_prices.get(pkey)) for pkey in result.providers},
            })
        return buf.getvalue().encode("utf-8-sig")
