"""Format A table renderer — pure function, no DB or session dependencies."""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import rich.box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..price_query.dtos import FormatARow, FormatATable

_QUERY_TITLES = {
    "provider": "Tarifario por proveedor",
    "average": "Tarifario medio del mercado",
    "minimum": "Tarifario mínimo del mercado",
}


def format_table(
    table: FormatATable,
    query_type: str,
    tenant_name: str,
    extra_context: dict | None = None,
) -> str:
    """Render FormatATable as a human-readable string."""
    ctx = extra_context or {}
    buf = io.StringIO()
    console = Console(file=buf, highlight=False, markup=False)

    d1_str, d2_str = table.metadata.get("date_range", ("", ""))
    d1 = date.fromisoformat(d1_str) if d1_str else None
    d2 = date.fromisoformat(d2_str) if d2_str else None
    same_year = d1 is not None and d2 is not None and d1.year == d2.year
    period_str = _format_header_period(d1, d2) if d1 and d2 else ""

    title = _QUERY_TITLES.get(query_type, query_type)
    header_lines = [
        title,
        f"Tenant: {tenant_name} · Periodo: {period_str}",
    ]
    if query_type == "provider":
        prov = ctx.get("provider", "")
        loc = ctx.get("location", "")
        rate = ctx.get("rate", "")
        header_lines.append(f"Proveedor: {prov} · Ubicación: {loc} · Tarifa: {rate}")

    console.print(Panel("\n".join(header_lines)))

    show_coverage = query_type in ("average", "minimum")
    num_subs = ctx.get("num_subscriptions", 0)
    group_names: dict[str, str] = ctx.get("group_names", {})

    if not table.rows:
        warning = table.metadata.get("warning", "Sin datos en el rango pedido.")
        console.print(
            f"\nSin datos disponibles para el rango pedido. Razón: {warning}\n",
            soft_wrap=True,
        )
    else:
        durations = list(table.rows[0].prices_by_duration.keys())

        groups_seen: list[str] = []
        seen_set: set[str] = set()
        for row in table.rows:
            if row.client_group_code not in seen_set:
                groups_seen.append(row.client_group_code)
                seen_set.add(row.client_group_code)

        for code in groups_seen:
            group_display = group_names.get(code, "")
            header = f"Grupo: {code}"
            if group_display:
                header += f" ({group_display})"
            console.print(f"\n{header}")

            t = Table(box=rich.box.SQUARE, show_header=True, header_style="bold", padding=(0, 1))
            t.add_column("Tramo", min_width=17)
            for dur in durations:
                t.add_column(f"{dur}d", justify="right", min_width=6)
            if show_coverage:
                t.add_column("Cob.", justify="center", min_width=6)

            for row in (r for r in table.rows if r.client_group_code == code):
                tramo = _format_tramo(row.period_start, row.period_end, same_year)
                cells = [tramo]
                for dur in durations:
                    price = row.prices_by_duration.get(dur)
                    cells.append(f"{price:.2f}" if price is not None else "—")
                if show_coverage:
                    cells.append(_format_coverage(row.coverage_by_duration, durations, num_subs))
                t.add_row(*cells)

            console.print(t)

    currency = table.metadata.get("currency", "")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print("\n" + "─" * 6)
    console.print("Detalles:")
    if currency:
        console.print(f"  Moneda: {currency}")
    console.print(f"  Generado: {generated}")
    if show_coverage:
        console.print(f"  Suscripciones consultadas: {num_subs}")

    return buf.getvalue()


def _format_header_period(d1: date, d2: date) -> str:
    return f"{d1.day:02d}/{d1.month:02d}/{d1.year} – {d2.day:02d}/{d2.month:02d}/{d2.year}"


def _format_tramo(start: date, end: date, same_year: bool) -> str:
    if same_year:
        return f"{start.day:02d}/{start.month:02d} – {end.day:02d}/{end.month:02d}"
    return (
        f"{start.day:02d}/{start.month:02d}/{start.year}"
        f" – "
        f"{end.day:02d}/{end.month:02d}/{end.year}"
    )


def _format_coverage(
    coverage_by_duration: dict[int, int] | None,
    durations: list[int],
    num_subs: int,
) -> str:
    if coverage_by_duration is None:
        return "—"
    values = [coverage_by_duration.get(d, 0) for d in durations]
    if not values:
        return f"0/{num_subs}"
    lo, hi = min(values), max(values)
    if lo == hi:
        return f"{lo}/{num_subs}"
    return f"{lo}-{hi}/{num_subs}"
