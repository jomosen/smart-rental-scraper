"""Unit tests for CsvExporter.

No database, no I/O.  Constructs ExportResult directly and verifies the
generated CSV bytes for headers, row count, values, and encoding.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from src.saas.application.pricing.csv_exporter import CsvExporter
from src.saas.application.pricing.export_service import ExportResult, ExportRow

D = Decimal

JAN1  = date(2025, 1, 1)
JAN31 = date(2025, 1, 31)
FEB1  = date(2025, 2, 1)
FEB28 = date(2025, 2, 28)


def _result(rows, providers=None) -> ExportResult:
    return ExportResult(
        rows=rows,
        providers=providers or ["centauro", "solcar"],
        durations=[3, 7],
    )


def _row(zone_index=0, acriss="EDMR", dur=3, rec=D("84.15"), *, zone_desde=JAN1, zone_hasta=JAN31, prices=None) -> ExportRow:
    return ExportRow(
        zone_index=zone_index,
        zone_desde=zone_desde,
        zone_hasta=zone_hasta,
        acriss_code=acriss,
        categoria="Económico Manual",
        duracion_dias=dur,
        recomendado_total=rec,
        recomendado_per_day=rec / dur,
        provider_prices=prices or {"centauro": D("90.00"), "solcar": D("85.00")},
    )


def _parse(csv_bytes: bytes) -> list[dict]:
    text = csv_bytes.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


class TestCsvHeaders:
    def test_fixed_columns_present(self):
        rows = _parse(CsvExporter().export(_result([_row()])))
        assert rows
        assert "zona_index" in rows[0]
        assert "zona_desde" in rows[0]
        assert "zona_hasta" in rows[0]
        assert "acriss_code" in rows[0]
        assert "categoria" in rows[0]
        assert "duracion_dias" in rows[0]
        assert "recomendado_total" in rows[0]
        assert "recomendado_per_day" in rows[0]

    def test_provider_columns_added_in_order(self):
        result = _result([_row()], providers=["centauro", "solcar", "victoria"])
        rows = _parse(CsvExporter().export(result))
        assert "centauro" in rows[0]
        assert "solcar" in rows[0]
        assert "victoria" in rows[0]
        # Verify the column order via the raw header line.
        header = csv_bytes_header(CsvExporter().export(result))
        assert header.index("centauro") < header.index("solcar") < header.index("victoria")

    def test_no_extra_provider_columns(self):
        result = _result([_row()], providers=["centauro"])
        rows = _parse(CsvExporter().export(result))
        assert "solcar" not in rows[0]
        assert "victoria" not in rows[0]


def csv_bytes_header(b: bytes) -> list[str]:
    first_line = b.decode("utf-8-sig").splitlines()[0]
    return first_line.split(",")


class TestCsvRows:
    def test_one_row_per_export_row(self):
        export_rows = [
            _row(zone_index=0, dur=3),
            _row(zone_index=0, dur=7),
            _row(zone_index=1, zone_desde=FEB1, zone_hasta=FEB28, dur=3),
        ]
        parsed = _parse(CsvExporter().export(_result(export_rows)))
        assert len(parsed) == 3

    def test_empty_result_produces_header_only(self):
        parsed = _parse(CsvExporter().export(_result([])))
        assert parsed == []

    def test_zone_dates_as_iso(self):
        parsed = _parse(CsvExporter().export(_result([_row()])))
        assert parsed[0]["zona_desde"] == "2025-01-01"
        assert parsed[0]["zona_hasta"] == "2025-01-31"

    def test_zone_index_value(self):
        export_rows = [
            _row(zone_index=0),
            _row(zone_index=1, zone_desde=FEB1, zone_hasta=FEB28),
        ]
        parsed = _parse(CsvExporter().export(_result(export_rows)))
        assert parsed[0]["zona_index"] == "0"
        assert parsed[1]["zona_index"] == "1"


class TestCsvValues:
    def test_decimal_formatted_to_two_places(self):
        parsed = _parse(CsvExporter().export(_result([_row(rec=D("84.1"))])))
        assert parsed[0]["recomendado_total"] == "84.10"

    def test_per_day_derived_value(self):
        row = _row(dur=3, rec=D("90.00"))
        parsed = _parse(CsvExporter().export(_result([row])))
        assert parsed[0]["recomendado_per_day"] == "30.00"

    def test_provider_prices_in_columns(self):
        row = _row(prices={"centauro": D("90.00"), "solcar": D("85.00")})
        parsed = _parse(CsvExporter().export(_result([row])))
        assert parsed[0]["centauro"] == "90.00"
        assert parsed[0]["solcar"] == "85.00"

    def test_missing_provider_price_is_empty_string(self):
        row = _row(prices={"centauro": D("90.00"), "solcar": None})
        parsed = _parse(CsvExporter().export(_result([row])))
        assert parsed[0]["solcar"] == ""

    def test_acriss_and_categoria_preserved(self):
        parsed = _parse(CsvExporter().export(_result([_row(acriss="CDAR")])))
        assert parsed[0]["acriss_code"] == "CDAR"
        assert parsed[0]["categoria"] == "Económico Manual"


class TestCsvEncoding:
    def test_bytes_start_with_utf8_bom(self):
        csv_bytes = CsvExporter().export(_result([_row()]))
        assert csv_bytes[:3] == b"\xef\xbb\xbf"

    def test_utf8_sig_decodable(self):
        csv_bytes = CsvExporter().export(_result([_row()]))
        # Must not raise
        decoded = csv_bytes.decode("utf-8-sig")
        assert "zona_index" in decoded

    def test_accented_characters_round_trip(self):
        row = ExportRow(
            zone_index=0, zone_desde=JAN1, zone_hasta=JAN31,
            acriss_code="MDAR", categoria="Pequeño Manual",
            duracion_dias=3, recomendado_total=D("50.00"),
            recomendado_per_day=D("16.67"),
            provider_prices={"centauro": D("52.00"), "solcar": D("50.00")},
        )
        parsed = _parse(CsvExporter().export(_result([row])))
        assert parsed[0]["categoria"] == "Pequeño Manual"
