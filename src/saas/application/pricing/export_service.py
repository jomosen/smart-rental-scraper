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


@dataclass
class ExportResult:
    rows: list[ExportRow]
    providers: list[str]   # ordered — same order as passed to build_rows
    durations: list[int]


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
    ) -> ExportResult:
        """Build wide-format export rows for ALL zones.

        The caller is responsible for fetching df once (e.g. via
        fetch_cross_tariff_dataframe); this method never touches the database.
        Cells where SummaryCell.empty=True (no data for that category+duration
        in the zone) are omitted from the output.
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

        # One call to discover total zone count, then iterate the rest.
        first: CrossTariffPayload = assemble_cross_tariff(**kwargs, zone_index=0)
        n_zones = first.zone.total
        if n_zones == 0:
            return ExportResult(rows=[], providers=providers, durations=durations)

        payloads: list[CrossTariffPayload] = [first]
        for z in range(1, n_zones):
            payloads.append(assemble_cross_tariff(**kwargs, zone_index=z))

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
                        provider_prices={
                            pkey: _min_price_for_provider(cat, pkey, cell.duration)
                            for pkey in providers
                        },
                    ))

        return ExportResult(rows=rows, providers=providers, durations=durations)
