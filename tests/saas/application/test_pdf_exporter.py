"""Unit tests for PdfExporter.

No database, no I/O.  Constructs ExportResult directly and verifies the
generated PDF bytes structurally (valid PDF header, non-trivial size, section
content present in stream).

Content-search tests use the `uncompressed_pdf` fixture which sets
reportlab.rl_config.compress=0 so plain text is searchable in the raw bytes.
Structural tests (size, header) run against normal compressed output.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.saas.application.pricing.export_service import ExportResult, ExportRow
from src.saas.application.pricing.pdf_exporter import PdfExporter


@pytest.fixture()
def uncompressed_pdf():
    """Disable FlateDecode page compression so PDF body text is literal in bytes.

    reportlab 4 uses rl_config.pageCompression (not the old .compress).
    """
    import reportlab.rl_config as _cfg
    original = _cfg.pageCompression
    _cfg.pageCompression = 0
    yield
    _cfg.pageCompression = original

D = Decimal

JAN1  = date(2025, 1, 1);  JAN31 = date(2025, 1, 31)
FEB1  = date(2025, 2, 1);  FEB28 = date(2025, 2, 28)


def _row(zone_index=0, dur=3, *, zone_desde=JAN1, zone_hasta=JAN31) -> ExportRow:
    return ExportRow(
        zone_index=zone_index,
        zone_desde=zone_desde,
        zone_hasta=zone_hasta,
        acriss_code="EDMR",
        categoria="Económico Manual",
        duracion_dias=dur,
        recomendado_total=D("84.15"),
        recomendado_per_day=D("28.05"),
        provider_prices={"centauro": D("90.00"), "solcar": D("85.00")},
    )


def _result(rows, providers=None) -> ExportResult:
    return ExportResult(
        rows=rows,
        providers=providers or ["centauro", "solcar"],
        durations=[3],
    )


def _export(rows=None, providers=None, tenant_name="Test Tenant") -> bytes:
    if rows is None:
        rows = [_row()]
    return PdfExporter().export(_result(rows, providers), tenant_name=tenant_name)


class TestPdfStructure:
    def test_output_is_valid_pdf(self):
        b = _export()
        assert b[:4] == b"%PDF", "Expected PDF to start with %PDF signature"

    def test_output_is_non_trivial(self):
        b = _export()
        assert len(b) > 2_000, f"PDF seems too small: {len(b)} bytes"

    def test_more_rows_produce_larger_pdf(self):
        one_row = _export([_row()])
        many_rows = _export([_row(dur=d) for d in [1, 2, 3, 5, 7, 14]])
        assert len(many_rows) > len(one_row)

    def test_empty_result_still_produces_valid_pdf(self):
        b = PdfExporter().export(_result([]), tenant_name="Acme")
        assert b[:4] == b"%PDF"
        assert len(b) > 500


class TestPdfContent:
    def test_tenant_name_in_metadata(self):
        """ASCII tenant name appears in PDF title metadata (uncompressed field)."""
        b = _export(tenant_name="AcmeRentals")
        # Title metadata is stored uncompressed in the PDF object dictionary.
        assert b"AcmeRentals" in b

    def test_acriss_code_in_stream(self, uncompressed_pdf):
        """ACRISS code appears in the uncompressed PDF body."""
        b = _export([_row()])
        assert b"EDMR" in b

    def test_zone_date_in_stream(self, uncompressed_pdf):
        """Zone date range DD/MM/YYYY appears in the section heading."""
        b = _export([_row(zone_desde=JAN1, zone_hasta=JAN31)])
        assert b"01/01/2025" in b
        assert b"31/01/2025" in b

    def test_multiple_zones_both_dates_present(self, uncompressed_pdf):
        rows = [
            _row(zone_index=0, zone_desde=JAN1, zone_hasta=JAN31),
            _row(zone_index=1, zone_desde=FEB1, zone_hasta=FEB28),
        ]
        b = _export(rows)
        assert b"01/01/2025" in b
        assert b"01/02/2025" in b

    def test_provider_names_in_stream(self, uncompressed_pdf):
        """Capitalized provider names appear in column headers."""
        b = _export(providers=["centauro", "solcar"])
        assert b"Centauro" in b
        assert b"Solcar" in b


class TestPdfWhiteLabel:
    def test_no_internal_product_name(self, uncompressed_pdf):
        """'Rentator' must never appear in customer-facing PDFs."""
        b = _export(tenant_name="Cliente Demo")
        assert b"Rentator" not in b
        assert b"rentator" not in b

    def test_empty_tenant_name_falls_back_gracefully(self):
        b = PdfExporter().export(_result([_row()]), tenant_name="")
        assert b[:4] == b"%PDF"


class TestPdfMultiZone:
    def test_two_zones_pdf_larger_than_one_zone(self):
        one = _export([_row(zone_index=0)])
        two = _export([
            _row(zone_index=0, zone_desde=JAN1, zone_hasta=JAN31),
            _row(zone_index=1, zone_desde=FEB1, zone_hasta=FEB28),
        ])
        assert len(two) > len(one)
