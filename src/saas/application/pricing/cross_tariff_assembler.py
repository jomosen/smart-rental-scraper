"""Assemble the cross-tariff grid from raw observations into a view model.

This module holds the logic that is shared by every presentation of the
cross-tariff grid (Streamlit back-office and the client API):

  - Naming derived from the ACRISS code (`code_label`) and the segment scale
    used to order categories (`TIER_ORDER`). Ref: docs/segmentos_acriss.md.
  - Zone alignment to a master provider (`master_zones`, `filter_zone`).
  - Per-cell recommendation via `compute_cell` (the validated calculation core
    in cross_tariff_calc.py) over a zone (`build_cell_results`).
  - A top-level `assemble_cross_tariff` that turns all of the above into a tree
    of dataclasses ready for serialization.

No I/O: callers pass an already-fetched DataFrame plus config. Decimals are
preserved throughout; presentations format/serialize them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

import pandas as pd

from .cross_tariff_calc import CellResult, RuleConfig, compute_cell

# ── Naming + ordering (single source; mirrors the proto's viewLabel) ─────────
# Segment scale with Elites interleaved. Ref: docs/segmentos_acriss.md §"LA ESCALA".
TIER_ORDER = "MNEHCDIJSRPULW"

_SEG_NAMES: dict[str, str] = {
    "M": "Pequeño",      "N": "Pequeño Premium",
    "E": "Económico",    "H": "Económico Premium",
    "C": "Compacto",     "D": "Compacto Premium",
    "I": "Mediano",      "J": "Mediano Premium",
    "S": "Estándar",     "R": "Estándar Premium",
    "P": "Premium",      "U": "Premium Grande",
    "L": "Lujo",         "W": "Lujo Grande",
}
# Body types with a visible suffix; D/B (sedan/hatch) contribute nothing.
_BODY_NAMES: dict[str, str] = {
    "F": "SUV", "W": "Familiar", "T": "Descapotable",
    "M": "Monovolumen", "E": "Coupé", "L": "Gran Coupé",
}

# Intra-segment body ordering for cars/SUVs (lower = shown first):
# turismo < SUV < familiar < monovolumen < descapotable < coupé < gran coupé.
BODY_ORDER: dict[str, int] = {
    "D": 0, "B": 0, "F": 1, "W": 2, "M": 3, "T": 4, "E": 5, "L": 6,
}

# Passenger vans (body V): the 1st letter encodes SEATS, not tier — see
# docs/segmentos_acriss.md §"FURGONETAS PASAJERO" (regla de plazas). Never "Lujo".
_VAN_NAMES: dict[str, str] = {
    "S": "Furgoneta 7 plazas",
    "P": "Furgoneta 8 plazas",
    "L": "Furgoneta 9 plazas",
    "R": "Furgoneta Premium 7-8 plazas",
    "U": "Furgoneta Premium 8 plazas",
    "W": "Furgoneta Premium 9 plazas",
}

# Fuel ordering within an otherwise-identical code: combustion < hybrid < electric.
_FUEL_ORDER: dict[str, int] = {"R": 0, "D": 0, "H": 1, "I": 1, "E": 2, "C": 2}

# Provider colors by selection order — mirrors the Streamlit palette.
_PROV_PALETTE = ["#d63a4e", "#3a63d6", "#1f9d6b", "#f59e0b", "#7c3aed", "#0891b2"]


def _powertrain_parts(trans: str, fuel: str) -> list[str]:
    """Transmission + powertrain words: electric absorbs the gearbox; hybrid is a suffix."""
    if fuel in ("E", "C"):
        return ["Eléctrico"]
    parts = ["Manual" if trans == "M" else "Automático"]
    if fuel in ("H", "I"):
        parts.append("Híbrido")
    return parts


def code_label(code: str) -> str:
    """Human-readable view label from a 4-char ACRISS code.

    Three branches (ref docs/segmentos_acriss.md):
      - Scooter (body Y, 2-wheelers): just "Scooter".
      - Passenger van (body V): the 1st letter encodes SEATS, not tier
        (regla de plazas) → "Furgoneta N plazas" + transmission. Never "Lujo".
      - Cars / SUVs: [segment] [body?] [transmission/electric] [hybrid?].
    """
    if len(code) < 4:
        return code
    seg, body, trans, fuel = code[0], code[1], code[2], code[3]

    if body == "Y":
        return "Scooter"

    if body == "V":
        base = _VAN_NAMES.get(seg, "Furgoneta")
        return " ".join([base, *_powertrain_parts(trans, fuel)])

    segment = _SEG_NAMES.get(seg, seg)
    body_sfx = _BODY_NAMES.get(body, "")
    return " ".join(p for p in [segment, body_sfx, *_powertrain_parts(trans, fuel)] if p)


def prov_color(provider_code: str, providers: list[str]) -> str:
    try:
        return _PROV_PALETTE[providers.index(provider_code) % len(_PROV_PALETTE)]
    except ValueError:
        return "#97a0b0"


def category_sort_key(
    code: str, cell_results: dict[tuple[str, int], CellResult] | None = None
) -> tuple[int, int, int, int, int]:
    """Stable, semantic category ordering (price plays no part). Ref docs/segmentos_acriss.md.

    Three blocks via the leading element:
      0 = cars / SUVs, 1 = passenger vans (body V), 2 = scooters (body Y).
    Within cars/SUVs: segment scale (MNEHCDIJSRPULW), then body
    (turismo<SUV<familiar<monovolumen<descapotable<coupé<gran coupé), then
    transmission (M<A), then fuel (R<H<E).
    Within vans: the same letter scale (S<R<P<U<L<W ≈ seats ascending, premium
    next to its pair), then transmission, then fuel.
    `cell_results` is accepted for call-site compatibility but unused.
    """
    seg = code[0] if code else ""
    body = code[1] if len(code) > 1 else ""
    trans = code[2] if len(code) > 2 else ""
    fuel = code[3] if len(code) > 3 else ""

    tier = TIER_ORDER.index(seg) if seg in TIER_ORDER else len(TIER_ORDER)
    tx_rank = 0 if trans == "M" else 1
    fuel_rank = _FUEL_ORDER.get(fuel, 0)

    if body == "Y":  # scooters — very last
        return (2, tier, 0, 0, 0)
    if body == "V":  # passenger vans — after all cars/SUVs, by seat-scale letter
        return (1, tier, tx_rank, fuel_rank, 0)
    body_rank = BODY_ORDER.get(body, 99)
    return (0, tier, body_rank, tx_rank, fuel_rank)


# ── Zone alignment ───────────────────────────────────────────────────────────

def master_zones(df: pd.DataFrame, master: str) -> pd.DataFrame:
    """Unique (start_date, end_date) rows from the master provider, sorted."""
    mdf = df[df["provider_code"] == master][["start_date", "end_date"]].drop_duplicates()
    return mdf.sort_values("start_date").reset_index(drop=True)


def filter_zone(
    df: pd.DataFrame,
    master: str,
    start: date,
    end: date,
    master_rep_date: date,
) -> pd.DataFrame:
    """Rows for one navigation step: master's exact zone + others covering its rep date."""
    master_rows = df[
        (df["provider_code"] == master)
        & (df["start_date"] == start)
        & (df["end_date"] == end)
    ]
    if master_rows["acriss_code"].nunique() == 0:
        return pd.DataFrame(columns=df.columns)

    def _covering(grp: pd.DataFrame) -> pd.DataFrame:
        covering = grp[
            (grp["start_date"] <= master_rep_date)
            & (grp["end_date"] >= master_rep_date)
        ]
        if covering.empty:
            return covering
        covering = covering.copy()
        covering["_dist"] = (
            pd.to_datetime(covering["representative_date"]) - pd.Timestamp(master_rep_date)
        ).abs()
        return (
            covering.sort_values("_dist")
            .drop_duplicates(subset=["acriss_code", "external_code", "duration_days"])
            .drop(columns=["_dist"])
        )

    parts = [master_rows]
    for pcode in df["provider_code"].unique():
        if pcode == master:
            continue
        parts.append(_covering(df[df["provider_code"] == pcode]))
    return pd.concat(parts, ignore_index=True)


# ── Cell results over a zone ────────────────────────────────────────────────

def build_cell_results(
    zone_df: pd.DataFrame,
    providers: list[str],
    master: str,
    master_rep_date: date,
    base: str,
    global_rule: dict,
    category_rules: dict[str, dict],
    round_mode: str,
) -> dict[tuple[str, int], CellResult]:
    """Compute a CellResult for every (acriss_code, duration) present in the zone."""
    results: dict[tuple[str, int], CellResult] = {}
    if zone_df.empty:
        return results

    for code in zone_df["acriss_code"].unique():
        code_df = zone_df[zone_df["acriss_code"] == code]
        rule_dict = category_rules.get(code, global_rule)
        is_default = code not in category_rules
        rule = RuleConfig(
            op=rule_dict["op"],
            val=Decimal(str(rule_dict["val"])),
            mode=rule_dict["mode"],
            floor_mode=rule_dict["floor"],
            ceiling_mode=rule_dict["ceiling"],
        )

        for dur in code_df["duration_days"].unique():
            dur_df = code_df[code_df["duration_days"] == dur]
            provider_groups: dict[str, list[Decimal]] = {}
            provider_inferred: dict[str, bool] = {}

            for pcode in providers:
                prows = dur_df[dur_df["provider_code"] == pcode]
                if not prows.empty:
                    provider_groups[pcode] = [Decimal(str(v)) for v in prows["total_price"]]
                    row_rep = prows["representative_date"].iloc[0]
                    provider_inferred[pcode] = (
                        pd.Timestamp(row_rep).date() != master_rep_date
                        if pcode != master
                        else False
                    )

            missing = {p for p in providers if p not in provider_groups}
            results[(code, int(dur))] = compute_cell(
                acriss_code=code,
                duration=int(dur),
                provider_groups=provider_groups,
                stale_providers=set(),
                stale_days={},
                missing_providers=missing,
                rule=rule,
                base=base,
                round_mode=round_mode,
                rule_is_default=is_default,
                total_providers=len(providers),
                provider_inferred=provider_inferred,
            )

    return results


# ── View-model DTOs ──────────────────────────────────────────────────────────

@dataclass
class ProviderCell:
    duration: int
    total: Optional[Decimal]
    per_day: Optional[Decimal]
    is_base: bool
    anomaly: bool
    missing: bool


@dataclass
class ProviderRow:
    provider_key: str
    models: str
    transmission: Optional[str]   # "manual" | "automatic" | None
    inferred: bool
    cells: list[ProviderCell]


@dataclass
class SummaryCell:
    duration: int
    recommended_total: Optional[Decimal]
    recommended_per_day: Optional[Decimal]
    market_min: Optional[Decimal]
    market_max: Optional[Decimal]
    empty: bool
    # Calculation pipeline (for the drawer): market base → rule → clamp → round.
    market_base: Optional[Decimal] = None      # aggregate before the rule
    after_rule: Optional[Decimal] = None        # after ± rule, before clamp
    clamped: bool = False                       # floor/ceiling fired
    clamp_bound: Optional[Decimal] = None        # the bound it was clamped to
    rounded: Optional[Decimal] = None            # final value after rounding (== recommended_total)
    round_flip: bool = False                     # rounding guard nudged it back across the base
    # Per-cell flags (not just the per-category union).
    stale: bool = False
    inferred: bool = False
    partial: bool = False


@dataclass
class CategoryView:
    acriss_code: str
    view_label: str
    catalog_examples: str
    flags: dict[str, bool]
    cells: list[SummaryCell]
    providers: list[ProviderRow]


@dataclass
class ProviderMeta:
    key: str
    name: str
    color: str
    is_master: bool


@dataclass
class ZoneMeta:
    index: int
    total: int
    date_from: Optional[date]
    date_to: Optional[date]


@dataclass
class CrossTariffPayload:
    durations: list[int]
    providers: list[ProviderMeta]
    zone: ZoneMeta
    master_rep_date: Optional[date]
    categories: list[CategoryView]


def _derive_transmission(transmission, acriss_transmission) -> Optional[str]:
    if pd.notna(transmission) and transmission:
        return "manual" if str(transmission).lower().startswith("m") else "automatic"
    if pd.notna(acriss_transmission) and acriss_transmission:
        at = str(acriss_transmission).upper()
        if at == "M":
            return "manual"
        if at in ("A", "B", "D"):
            return "automatic"
    return None


# Transmission tokens embedded in provider example_models text. Stripped from
# the displayed models because the category name already states the transmission
# (e.g. "Compacto Automático"). Longest alternatives first; optional trailing dot.
_TX_TOKENS = re.compile(r"(?i)\b(autom[aá]tic[oa]|auto|aut|manual|man)\b\.?")


def _strip_transmission(text: str) -> str:
    cleaned = _TX_TOKENS.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)        # collapse double spaces
    cleaned = re.sub(r"\s+([,/])", r"\1", cleaned)   # no space before , or /
    return cleaned.strip(" ,/")


def _build_catalog_examples(code: str, examples: dict[str, list[str]]) -> str:
    items = examples.get(code, [])[:3]
    if not items:
        return ""
    return ", ".join(items) + " o similar"


def _build_provider_rows(
    code: str,
    zone_df: pd.DataFrame,
    cell_results: dict[tuple[str, int], CellResult],
    providers: list[str],
    durations: list[int],
) -> list[ProviderRow]:
    code_df = zone_df[zone_df["acriss_code"] == code]

    # Which provider determined the base, and which were anomaly-dropped, per duration.
    base_per_dur: dict[int, set[str]] = {}
    anomaly_per_dur: dict[int, set[str]] = {}
    inferred_by_provider: dict[str, bool] = {}
    for dur in durations:
        cr = cell_results.get((code, dur))
        if cr:
            base_per_dur[dur] = {pc.provider_code for pc in cr.present if pc.is_base}
            anomaly_per_dur[dur] = {d.provider_code for d in cr.dropped if d.reason == "anomaly"}
            for pc in cr.present:
                if pc.inferred:
                    inferred_by_provider[pc.provider_code] = True
        else:
            base_per_dur[dur] = set()
            anomaly_per_dur[dur] = set()

    # Stable group order: provider position, then model name.
    prov_order = {p: i for i, p in enumerate(providers)}
    groups = (
        code_df[["provider_code", "external_code", "example_models",
                 "transmission", "acriss_transmission"]]
        .drop_duplicates(["provider_code", "external_code"])
        .copy()
    )
    groups["_pi"] = groups["provider_code"].map(lambda p: prov_order.get(p, 999))
    groups["_mi"] = groups["example_models"].fillna("").str.lower()
    groups = groups.sort_values(["_pi", "_mi"]).drop(columns=["_pi", "_mi"])

    rows: list[ProviderRow] = []
    for _, grp in groups.iterrows():
        pcode = str(grp["provider_code"])
        ext_code = grp["external_code"]
        model_name = (
            _strip_transmission(str(grp["example_models"]).strip())
            if pd.notna(grp["example_models"]) else ""
        )

        cells: list[ProviderCell] = []
        for dur in durations:
            row_data = code_df[
                (code_df["provider_code"] == pcode)
                & (code_df["external_code"] == ext_code)
                & (code_df["duration_days"] == dur)
            ]
            if row_data.empty:
                cells.append(ProviderCell(dur, None, None, False, False, True))
                continue

            total = Decimal(str(row_data["total_price"].iloc[0]))
            per_day = total / dur if dur else total

            # is_base: provider determined the base AND this group is the provider's min total.
            is_base = False
            if pcode in base_per_dur.get(dur, set()):
                all_totals = code_df[
                    (code_df["provider_code"] == pcode)
                    & (code_df["duration_days"] == dur)
                ]["total_price"]
                if not all_totals.empty:
                    is_base = abs(float(total) - float(all_totals.min())) < 0.001

            cells.append(ProviderCell(
                duration=dur,
                total=total,
                per_day=per_day,
                is_base=is_base,
                anomaly=pcode in anomaly_per_dur.get(dur, set()),
                missing=False,
            ))

        rows.append(ProviderRow(
            provider_key=pcode,
            models=model_name,
            transmission=_derive_transmission(grp.get("transmission"), grp.get("acriss_transmission")),
            inferred=inferred_by_provider.get(pcode, False),
            cells=cells,
        ))
    return rows


def assemble_cross_tariff(
    df: pd.DataFrame,
    providers: list[str],
    master: str,
    base: str,
    round_mode: str,
    global_rule: dict,
    category_rules: dict[str, dict],
    durations: list[int],
    examples: dict[str, list[str]],
    zone_index: Optional[int] = None,
    today: Optional[date] = None,
) -> CrossTariffPayload:
    """Build the full cross-tariff view model from raw observations and config.

    zone_index: which of the master's zones to render. Defaults to the zone
    covering `today` (or `date.today()`), falling back to the first zone.
    """
    today = today or date.today()
    prov_meta = [
        ProviderMeta(key=p, name=p, color=prov_color(p, providers), is_master=(p == master))
        for p in providers
    ]

    zones = master_zones(df, master) if not df.empty else pd.DataFrame()
    n_zones = len(zones)
    if n_zones == 0:
        return CrossTariffPayload(
            durations=list(durations), providers=prov_meta,
            zone=ZoneMeta(0, 0, None, None), master_rep_date=None, categories=[],
        )

    # Pick zone: explicit index (clamped), else the one covering today, else 0.
    if zone_index is not None:
        idx = max(0, min(int(zone_index), n_zones - 1))
    else:
        idx = 0
        for i in range(n_zones):
            z = zones.iloc[i]
            if z["start_date"] <= today <= z["end_date"]:
                idx = i
                break

    z = zones.iloc[idx]
    z_start, z_end = z["start_date"], z["end_date"]

    master_rep_dates = sorted(
        df[
            (df["provider_code"] == master)
            & (df["start_date"] == z_start)
            & (df["end_date"] == z_end)
        ]["representative_date"].unique()
    )
    master_rep_date = (
        pd.Timestamp(master_rep_dates[0]).date() if master_rep_dates else z_start
    )

    zone_df = filter_zone(df, master, z_start, z_end, master_rep_date)
    cell_results = build_cell_results(
        zone_df, providers, master, master_rep_date,
        base, global_rule, category_rules, round_mode,
    )

    codes = sorted(
        zone_df["acriss_code"].unique(),
        key=lambda c: category_sort_key(c, cell_results),
    )

    categories: list[CategoryView] = []
    for code in codes:
        # Summary cells + union of flags across durations.
        summary_cells: list[SummaryCell] = []
        f_partial = f_clamped = f_stale = f_anom = f_inf = False
        for dur in durations:
            cr = cell_results.get((code, dur))
            if cr is None or cr.rec_total is None:
                summary_cells.append(SummaryCell(dur, None, None, None, None, True))
                if cr is not None:
                    if cr.flags.coverage < cr.flags.total_providers:
                        f_partial = True
                    if cr.flags.degraded:
                        f_anom = True
                continue
            totals = [p.total for p in cr.present]
            mkt_min = min(totals) if totals else None
            mkt_max = max(totals) if totals else None
            clamp_kind = cr.flags.clamped  # "floor" | "ceiling" | None
            clamp_bound = (
                cr.floor_val if clamp_kind == "floor"
                else cr.ceiling_val if clamp_kind == "ceiling"
                else None
            )
            summary_cells.append(SummaryCell(
                duration=dur,
                recommended_total=cr.rec_total,
                recommended_per_day=cr.rec_per_day,
                market_min=mkt_min,
                market_max=mkt_max,
                empty=False,
                market_base=cr.base_total,
                after_rule=cr.adjusted_total,
                clamped=clamp_kind is not None,
                clamp_bound=clamp_bound,
                rounded=cr.rec_total,
                round_flip=cr.round_flip,
                stale=cr.flags.stale,
                inferred=cr.flags.inferred,
                partial=cr.flags.coverage < cr.flags.total_providers,
            ))
            if cr.flags.coverage < cr.flags.total_providers:
                f_partial = True
            if cr.flags.clamped:
                f_clamped = True
            if cr.flags.stale:
                f_stale = True
            if cr.flags.degraded:
                f_anom = True
            if cr.flags.inferred:
                f_inf = True

        categories.append(CategoryView(
            acriss_code=code,
            view_label=code_label(code),
            catalog_examples=_build_catalog_examples(code, examples),
            flags={
                "partial_coverage": f_partial, "clamped": f_clamped,
                "stale": f_stale, "anomaly": f_anom, "inferred": f_inf,
            },
            cells=summary_cells,
            providers=_build_provider_rows(code, zone_df, cell_results, providers, durations),
        ))

    return CrossTariffPayload(
        durations=list(durations),
        providers=prov_meta,
        zone=ZoneMeta(index=idx, total=n_zones, date_from=z_start, date_to=z_end),
        master_rep_date=master_rep_date,
        categories=categories,
    )
