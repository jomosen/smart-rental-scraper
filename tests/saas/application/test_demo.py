"""Tests for the demo CLI and format_table formatter.

Unit tests (TestFormatTable): pure function, no DB required.
Integration-style tests (test_cli_*): mock PriceQueryService and session stack.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.saas.application.demo.cli import main
from src.saas.application.demo.formatter import format_table
from src.saas.application.price_query.dtos import FormatARow, FormatATable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    code: str,
    start: date,
    end: date,
    prices: dict,
    coverage: dict | None = None,
) -> FormatARow:
    return FormatARow(
        client_group_code=code,
        period_start=start,
        period_end=end,
        prices_by_duration=prices,
        coverage_by_duration=coverage,
    )


def _table(rows, metadata=None) -> FormatATable:
    return FormatATable(
        rows=rows,
        metadata=metadata or {"date_range": ("2026-05-01", "2026-08-31")},
    )


def _make_mock_session(tenant_name: str = "Test Tenant", sub_count: int = 1) -> MagicMock:
    session = MagicMock()
    tenant = MagicMock()
    tenant.name = tenant_name
    session.get.return_value = tenant
    session.scalar.return_value = sub_count
    session.scalars.return_value.all.return_value = []
    return session


def _mock_ctx(mock_session: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Unit tests — format_table
# ---------------------------------------------------------------------------


def test_format_table_provider_renders_one_table_per_group():
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 7, 14), {7: Decimal("45.00")}),
        _row("C", date(2026, 5, 1), date(2026, 7, 14), {7: Decimal("55.00")}),
    ])
    output = format_table(tbl, "provider", "Acme")
    assert "Grupo: B" in output
    assert "Grupo: C" in output


def test_format_table_average_includes_coverage_column():
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 8, 31),
             {7: Decimal("45.00")}, {7: 2}),
    ])
    output = format_table(tbl, "average", "Acme", {"num_subscriptions": 2})
    assert "Cob." in output


def test_format_table_provider_omits_coverage_column():
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 8, 31), {7: Decimal("45.00")}),
    ])
    output = format_table(tbl, "provider", "Acme")
    assert "Cob." not in output


def test_format_table_renders_em_dash_for_null_prices():
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 8, 31), {7: None}),
    ])
    output = format_table(tbl, "provider", "Acme")
    assert "—" in output


def test_format_table_collapses_year_in_period_when_same_year():
    # date_range within one year → Tramo column shows DD/MM without year
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 7, 14), {7: Decimal("45.00")}),
    ], metadata={"date_range": ("2026-05-01", "2026-08-31")})
    output = format_table(tbl, "provider", "Acme")
    # Tramo cell appears without year
    assert "01/05 – 14/07" in output
    # Full year format for that specific tramo must not appear in the table rows
    assert "01/05/2026 – 14/07/2026" not in output


def test_format_table_shows_coverage_range_when_cells_differ():
    # dur 7 covered by 2 providers, dur 14 only by 1 → "1-2/2"
    tbl = _table([
        _row("B", date(2026, 5, 1), date(2026, 8, 31),
             {7: Decimal("45.00"), 14: Decimal("38.00")},
             {7: 2, 14: 1}),
    ])
    output = format_table(tbl, "average", "Acme", {"num_subscriptions": 2})
    assert "1-2/2" in output


def test_format_table_shows_empty_data_message_when_no_rows():
    tbl = _table(
        rows=[],
        metadata={
            "date_range": ("2026-05-01", "2026-08-31"),
            "warning": "No active subscription for this tuple.",
        },
    )
    output = format_table(tbl, "average", "Acme")
    assert "Sin datos" in output
    assert "No active subscription for this tuple." in output


# ---------------------------------------------------------------------------
# Integration-style tests — CLI (PriceQueryService mocked)
# ---------------------------------------------------------------------------


def test_cli_fails_when_tenant_id_not_uuid(capsys):
    rc = main([
        "--tenant-id", "not-a-uuid",
        "--query", "average",
        "--client-groups", "B",
        "--date-range", "2026-05-01:2026-08-31",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "UUID" in captured.err or "tenant-id" in captured.err.lower()


def test_cli_fails_when_date_range_inverted(capsys):
    rc = main([
        "--tenant-id", str(uuid.uuid4()),
        "--query", "average",
        "--client-groups", "B",
        "--date-range", "2026-08-31:2026-05-01",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "fecha" in captured.err.lower() or "date" in captured.err.lower()


def test_cli_fails_when_query_provider_without_provider_args(capsys):
    rc = main([
        "--tenant-id", str(uuid.uuid4()),
        "--query", "provider",
        "--client-groups", "B",
        "--date-range", "2026-05-01:2026-08-31",
        # --provider, --location, --rate intentionally absent
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "--provider" in captured.err


def test_cli_warns_when_provider_args_with_average_query(capsys):
    tid = str(uuid.uuid4())
    mock_session = _make_mock_session()
    empty_result = FormatATable(
        rows=[], metadata={"date_range": ("2026-05-01", "2026-08-31")}
    )

    with patch("src.saas.application.demo.cli.app_engine"), \
         patch("src.saas.application.demo.cli.make_session_factory"), \
         patch("src.saas.application.demo.cli.tenant_context",
               return_value=_mock_ctx(mock_session)), \
         patch("src.saas.application.demo.cli.PriceQueryService") as MockSvc:

        mock_svc = MagicMock()
        MockSvc.return_value = mock_svc
        mock_svc.get_market_average_tariff.return_value = empty_result

        rc = main([
            "--tenant-id", tid,
            "--query", "average",
            "--client-groups", "B",
            "--date-range", "2026-05-01:2026-08-31",
            "--provider", "prov_a",
        ])

    captured = capsys.readouterr()
    assert "ignorados" in captured.err.lower() or "advertencia" in captured.err.lower()


def test_cli_invokes_correct_service_method_per_query_type(capsys):
    cases = [
        ("average", "get_market_average_tariff", []),
        ("minimum", "get_market_minimum_tariff", []),
        (
            "provider",
            "get_provider_tariff",
            ["--provider", "prov_a", "--location", "MAD", "--rate", "STD"],
        ),
    ]
    for query_type, method_name, extra_args in cases:
        mock_session = _make_mock_session()
        empty_result = FormatATable(
            rows=[], metadata={"date_range": ("2026-05-01", "2026-08-31")}
        )

        with patch("src.saas.application.demo.cli.app_engine"), \
             patch("src.saas.application.demo.cli.make_session_factory"), \
             patch("src.saas.application.demo.cli.tenant_context",
                   return_value=_mock_ctx(mock_session)), \
             patch("src.saas.application.demo.cli.PriceQueryService") as MockSvc:

            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc
            getattr(mock_svc, method_name).return_value = empty_result

            rc = main(
                [
                    "--tenant-id", str(uuid.uuid4()),
                    "--query", query_type,
                    "--client-groups", "B",
                    "--date-range", "2026-05-01:2026-08-31",
                ]
                + extra_args
            )

            getattr(mock_svc, method_name).assert_called_once(), (
                f"expected {method_name} to be called for --query={query_type}"
            )
