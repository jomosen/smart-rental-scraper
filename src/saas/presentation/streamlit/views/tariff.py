"""Tariff tab: per-provider price matrix (ACRISS × duration) for a selected zone.

One HTML table per zone with:
  - Rows = ACRISS category (code + display_name + aggregated example_models)
  - Columns = rental durations from DURATION_BRACKET
  - Cells = total price (large) + €/day (small label)

Zone navigation uses ◀ ▶ buttons backed by session_state.
"""
from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from ..filters import Filters
from ..queries import (
    DURATION_BRACKET,
    fetch_tariff_table,
    get_active_provider_codes,
)

_CSS = """
<style>
table.tariff-table {
    border-collapse: collapse;
    width: 100%;
    font-family: inherit;
    font-size: 0.9em;
}
table.tariff-table th {
    background: #f0f2f6;
    padding: 8px 12px;
    text-align: center;
    border: 1px solid #ddd;
    white-space: nowrap;
}
table.tariff-table th.cat-col { text-align: left; min-width: 180px; }
table.tariff-table td {
    padding: 6px 10px;
    vertical-align: top;
    border: 1px solid #ddd;
}
table.tariff-table td.category .acriss {
    font-weight: 600;
    font-size: 1em;
}
table.tariff-table td.category .models {
    font-size: 0.78em;
    color: #888;
    margin-top: 2px;
}
table.tariff-table td.price-cell { text-align: center; }
table.tariff-table td.price-cell .total {
    font-weight: 600;
    font-size: 1em;
    white-space: nowrap;
}
table.tariff-table td.price-cell .per-day {
    font-size: 0.78em;
    color: #888;
    margin-top: 2px;
    white-space: nowrap;
}
table.tariff-table td.price-cell.empty {
    color: #ccc;
    text-align: center;
    vertical-align: middle;
}
</style>
"""


def _aggregate_models(series: pd.Series) -> str:
    """Collect distinct model names from a Series of comma-separated strings."""
    seen: list[str] = []
    for cell in series.dropna():
        for part in str(cell).split(","):
            name = part.strip()
            if name and name not in seen:
                seen.append(name)
    if not seen:
        return ""
    display = ", ".join(seen[:3])
    return display + "…" if len(seen) > 3 else display


def _build_tariff_html(zone_df: pd.DataFrame) -> str:
    """Build an HTML table for a single zone (all ACRISS codes in one table)."""
    durations = list(DURATION_BRACKET)
    acriss_codes = sorted(zone_df["acriss_code"].unique())

    header_cells = ["<th class='cat-col'>Categoría</th>"] + [
        f"<th>{d}d</th>" for d in durations
    ]
    header = f"<tr>{''.join(header_cells)}</tr>"

    rows: list[str] = []
    for code in acriss_codes:
        code_df = zone_df[zone_df["acriss_code"] == code]
        display_name = _html.escape(str(code_df["display_name"].iloc[0]))
        has_pending = bool(code_df["has_pending_review"].iloc[0])

        models_text = _html.escape(_aggregate_models(code_df["example_models"]))
        badge = "🔍 " if has_pending else ""

        cat_td = (
            f"<td class='category'>"
            f"<div class='acriss'>{badge}{_html.escape(code)} — {display_name}</div>"
            f"<div class='models'>{models_text}</div>"
            f"</td>"
        )

        # Build lookup: duration_days → (price_per_day, total_price)
        dur_lookup: dict[int, tuple[float, float]] = {}
        for _, row in code_df.iterrows():
            dur_lookup[int(row["duration_days"])] = (
                float(row["price_per_day"]),
                float(row["total_price"]),
            )

        price_tds: list[str] = []
        for d in durations:
            if d in dur_lookup:
                ppd, total = dur_lookup[d]
                price_tds.append(
                    f"<td class='price-cell'>"
                    f"<div class='total'>{total:.2f} €</div>"
                    f"<div class='per-day'>{ppd:.2f} €/d</div>"
                    f"</td>"
                )
            else:
                price_tds.append("<td class='price-cell empty'>—</td>")

        rows.append(f"<tr>{cat_td}{''.join(price_tds)}</tr>")

    return (
        f"<table class='tariff-table'>"
        f"<thead>{header}</thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table>"
    )


def render_tariff(filters: Filters) -> None:
    providers = get_active_provider_codes()
    if not providers:
        st.info("No hay providers activos con datos clasificados.")
        return

    selected_provider = st.selectbox(
        "Provider",
        options=providers,
        key="tariff_provider_select",
    )

    # Reset zone index when provider changes so we don't land out-of-bounds.
    if st.session_state.get("_tariff_last_provider") != selected_provider:
        st.session_state["tariff_zone_idx"] = 0
        st.session_state["_tariff_last_provider"] = selected_provider

    df = fetch_tariff_table(
        provider_code=selected_provider,
        acriss_codes=filters.acriss_categories,
        durations=DURATION_BRACKET,
        include_pending_review=filters.include_pending_review,
    )

    if df.empty:
        st.warning(
            "No hay datos de tarifa para este provider con los filtros actuales. "
            "Prueba a desactivar el filtro ACRISS o a incluir categorías en revisión."
        )
        return

    # Unique zones ordered by start_date
    zones = (
        df[["start_date", "end_date"]]
        .drop_duplicates()
        .sort_values("start_date")
        .reset_index(drop=True)
    )
    n_zones = len(zones)

    idx = int(st.session_state.get("tariff_zone_idx", 0))
    idx = max(0, min(idx, n_zones - 1))

    # Zone navigator: ◀ [label] ▶
    col_prev, col_label, col_next = st.columns([1, 6, 1])
    with col_prev:
        if st.button("◀", disabled=(idx == 0), key="tariff_prev_zone"):
            st.session_state["tariff_zone_idx"] = idx - 1
            st.rerun()
    with col_label:
        z = zones.iloc[idx]
        start_s = z["start_date"].strftime("%d %b %Y")
        end_s = z["end_date"].strftime("%d %b %Y")
        st.markdown(f"#### {start_s} – {end_s} &nbsp; · &nbsp; zona {idx + 1} / {n_zones}")
    with col_next:
        if st.button("▶", disabled=(idx == n_zones - 1), key="tariff_next_zone"):
            st.session_state["tariff_zone_idx"] = idx + 1
            st.rerun()

    # Filter to selected zone
    zone_df = df[
        (df["start_date"] == z["start_date"]) & (df["end_date"] == z["end_date"])
    ]

    n_codes = zone_df["acriss_code"].nunique()
    st.caption(
        f"{selected_provider} · {n_codes} categoría(s) · "
        f"precio total (€) con €/día debajo"
    )

    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_build_tariff_html(zone_df), unsafe_allow_html=True)

    pending_codes = sorted(
        zone_df.loc[zone_df["has_pending_review"], "acriss_code"].unique()
    )
    if pending_codes:
        st.caption(
            "🔍 Grupos pendientes de revisión manual en: "
            + ", ".join(pending_codes)
            + ". Los precios son orientativos."
        )
