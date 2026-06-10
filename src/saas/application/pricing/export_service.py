"""Export service for the cross-tariff pricing view.

Produces wide-format rows (one per zone × category × duration) ready for CSV
or PDF formatting.  No I/O: the caller fetches the DataFrame once and passes it
in; this service iterates all zones by calling assemble_cross_tariff N times
over the same df — N assemblies, zero extra queries.

Provider column value = minimum non-missing total across all groups for that
provider + duration (the same value collapse_intra_provider would use).  This
gives every provider a price whenever they have data for the cell, regardless
of which provider determined the aggregate base.  If a provider has no data for
a cell its column is None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

import pandas as pd

from .cross_tariff_assembler import CategoryView, CrossTariffPayload, assemble_cross_tariff


@dataclass
class ExportRow:
    zone_index: int
    zone_desde: Optional[date]
    zone_hasta: Optional[date]
    acriss_code: str
    categoria: str
    duracion_dias: int
    recomendado_total: Optional[Decimal]
    recomendado_per_day: Optional[Decimal]
    # Keyed by provider_key in the order given to build_rows.
    provider_prices: dict[str, Optional[Decimal]] = field(default_factory=dict)
    # Curated example models for the category ("Fiat 500, … o similar"). Same
    # string the web shows under the category name; "" when none. Repeated on
    # every duration row of a category; the PDF renders it once per group.
    catalog_examples: str = ""
    # Per-provider model name(s) for the category, keyed by provider_key
    # ("" when the provider has no model). Constant across durations; the PDF
    # renders it once per group (truncated). Full string here — callers truncate.
    provider_models: dict[str, str] = field(default_factory=dict)
    # Provider whose price set the recommended base for this cell (is_base in
    # the assembler), or None when no single provider is the base (e.g. avg/med
    # aggregation). The PDF bolds this provider's price.
    base_provider: Optional[str] = None


@dataclass
class ExportResult:
    rows: list[ExportRow]
    providers: list[str]   # ordered — same order as passed to build_rows
    durations: list[int]
    # Real number of zones for the (provider, location) tuple, independent of how
    # many were exported. Keeps "Temporada N de T" correct on a partial export.
    total_zones: int = 0


def _min_price_for_provider(
    category: CategoryView,
    provider_key: str,
    duration: int,
) -> Optional[Decimal]:
    """Minimum non-missing total for provider_key at this duration across all its groups."""
    best: Optional[Decimal] = None
    for prow in category.providers:
        if prow.provider_key != provider_key:
            continue
        for pcell in prow.cells:
            if pcell.duration == duration and not pcell.missing and pcell.total is not None:
                if best is None or pcell.total < best:
                    best = pcell.total
    return best


def _models_for_provider(category: CategoryView, provider_key: str) -> str:
    """Distinct model name(s) the provider lists for this category, in row order."""
    seen: list[str] = []
    for prow in category.providers:
        if prow.provider_key != provider_key:
            continue
        m = (prow.models or "").strip()
        if m and m not in seen:
            seen.append(m)
    return " / ".join(seen)


def _base_provider_for(category: CategoryView, duration: int) -> Optional[str]:
    """Provider whose price is the base (is_base) at this duration, if any.

    Mirrors the value shown in provider_prices: among the is_base cells, the one
    with the lowest total. None when no provider is flagged as base (avg/med).
    """
    best_key: Optional[str] = None
    best_total: Optional[Decimal] = None
    for prow in category.providers:
        for pcell in prow.cells:
            if (
                pcell.duration == duration and pcell.is_base
                and not pcell.missing and pcell.total is not None
            ):
                if best_total is None or pcell.total < best_total:
                    best_total = pcell.total
                    best_key = prow.provider_key
    return best_key


class PricingExportService:
    def build_rows(
        self,
        df: pd.DataFrame,
        providers: list[str],
        master: str,
        base: str,
        round_mode: str,
        global_rule: dict,
        category_rules: dict[str, dict],
        durations: list[int],
        examples: dict[str, list[str]],
        zone_from: Optional[int] = None,
        zone_to: Optional[int] = None,
    ) -> ExportResult:
        """Build wide-format export rows for a range of zones (default: all).

        The caller is responsible for fetching df once (e.g. via
        fetch_cross_tariff_dataframe); this method never touches the database.
        Cells where SummaryCell.empty=True (no data for that category+duration
        in the zone) are omitted from the output.

        zone_from / zone_to: inclusive 0-based zone range to export. None means
        the open end (first / last). Out-of-range values are clamped; a reversed
        range is normalised. ExportResult.total_zones always carries the real
        zone count so callers can render "Temporada N de T" correctly.
        """
        if df.empty or not providers:
            return ExportResult(rows=[], providers=providers, durations=durations)

        kwargs: dict = dict(
            df=df,
            providers=providers,
            master=master,
            base=base,
            round_mode=round_mode,
            global_rule=global_rule,
            category_rules=category_rules,
            durations=durations,
            examples=examples,
        )

        # One call to discover total zone count, then iterate the requested range.
        first: CrossTariffPayload = assemble_cross_tariff(**kwargs, zone_index=0)
        n_zones = first.zone.total
        if n_zones == 0:
            return ExportResult(rows=[], providers=providers, durations=durations)

        lo = 0 if zone_from is None else max(0, min(zone_from, n_zones - 1))
        hi = n_zones - 1 if zone_to is None else max(0, min(zone_to, n_zones - 1))
        if lo > hi:
            lo, hi = hi, lo

        payloads: list[CrossTariffPayload] = [
            first if z == 0 else assemble_cross_tariff(**kwargs, zone_index=z)
            for z in range(lo, hi + 1)
        ]

        rows: list[ExportRow] = []
        for payload in payloads:
            zm = payload.zone
            for cat in payload.categories:
                for cell in cat.cells:
                    if cell.empty:
                        continue
                    rows.append(ExportRow(
                        zone_index=zm.index,
                        zone_desde=zm.date_from,
                        zone_hasta=zm.date_to,
                        acriss_code=cat.acriss_code,
                        categoria=cat.view_label,
                        duracion_dias=cell.duration,
                        recomendado_total=cell.recommended_total,
                        recomendado_per_day=cell.recommended_per_day,
                        catalog_examples=cat.catalog_examples,
                        provider_prices={
                            pkey: _min_price_for_provider(cat, pkey, cell.duration)
                            for pkey in providers
                        },
                        provider_models={
                            pkey: _models_for_provider(cat, pkey)
                            for pkey in providers
                        },
                        base_provider=_base_provider_for(cat, cell.duration),
                    ))

        return ExportResult(
            rows=rows, providers=providers, durations=durations, total_zones=n_zones,
        )
