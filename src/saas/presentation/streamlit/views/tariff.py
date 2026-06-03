"""Tariff tab: per-provider hierarchical price matrix (ACRISS → groups × duration).

Hierarchy per zone:
  ▶ ACRISS header row  (secondary background, full colspan)
      Group subrow     (external_code + example_models + price per duration)
      ...
  ▶ Summary row        (min / med / avg / max) — only when 2+ groups share the ACRISS

Zone navigation uses ◀ ▶ buttons backed by session_state.
"""
from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from ..queries import (
    DURATION_BRACKET,
    fetch_tariff_table,
    get_active_provider_codes,
    get_acriss_codes_with_display_names,
)

_CSS = """
<style>
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] {
    gap: 2px !important;
}
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] [data-testid="column"] {
    padding-left: 1px !important;
    padding-right: 1px !important;
    min-width: 0 !important;
    flex: 0 0 auto !important;
}
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
table.tariff-table th.cat-col { text-align: left; min-width: 200px; }

/* ACRISS header row */
table.tariff-table tr.acriss-header td {
    background: #e8eaf6;
    font-weight: 700;
    font-size: 0.95em;
    padding: 7px 12px;
    border: 1px solid #c5cae9;
    color: #3949ab;
}

/* Group subrow */
table.tariff-table tr.group-row td.category {
    padding: 5px 10px 5px 24px;
    vertical-align: top;
    border: 1px solid #ddd;
}
table.tariff-table tr.group-row td.category .ext-code {
    font-weight: 600;
    font-size: 0.92em;
}
table.tariff-table tr.group-row td.category .models {
    font-size: 0.78em;
    color: #888;
    margin-top: 2px;
}

/* Summary row */
table.tariff-table tr.summary-row td {
    background: #e3f2fd;
    border: 1px solid #bbdefb;
    padding: 5px 8px;
}
table.tariff-table tr.summary-row td.label {
    font-weight: 700;
    font-size: 0.82em;
    color: #1565c0;
    padding-left: 24px;
    vertical-align: middle;
}

/* Price cells (shared by group-row and summary-row) */
table.tariff-table td.price-cell {
    text-align: center;
    vertical-align: top;
    border: 1px solid #ddd;
    padding: 5px 8px;
}
table.tariff-table td.price-cell .total {
    font-weight: 600;
    font-size: 1em;
    white-space: nowrap;
}
table.tariff-table td.price-cell .per-day {
    font-size: 0.76em;
    color: #888;
    margin-top: 2px;
    white-space: nowrap;
}
table.tariff-table td.price-cell.empty {
    color: #ccc;
    text-align: center;
    vertical-align: middle;
}
table.tariff-table tr.summary-row td.price-cell {
    background: #e3f2fd;
    border: 1px solid #bbdefb;
}
table.tariff-table tr.summary-row td.price-cell .total {
    font-size: 0.88em;
    color: #1565c0;
}
table.tariff-table tr.summary-row td.price-cell .per-day {
    color: #64b5f6;
}
</style>
"""


def _build_tariff_html(zone_df: pd.DataFrame, durations: list[int]) -> str:
    n_cols = 1 + len(durations)

    header_cells = ["<th class='cat-col'>Categoría / Grupo</th>"] + [
        f"<th>{d}d</th>" for d in durations
    ]
    header = f"<tr>{''.join(header_cells)}</tr>"

    def _min_total(sub: pd.DataFrame) -> float:
        d1 = sub[sub["duration_days"] == 1]
        return float((d1 if not d1.empty else sub)["total_price"].min())

    acriss_codes = sorted(zone_df["acriss_code"].unique(),
                          key=lambda c: _min_total(zone_df[zone_df["acriss_code"] == c]))

    rows: list[str] = []

    for code in acriss_codes:
        code_df = zone_df[zone_df["acriss_code"] == code]
        display_name = _html.escape(str(code_df["acriss_display_name"].iloc[0]))
        has_pending = bool(code_df["pending_review"].any())
        badge = "🔍 " if has_pending else ""

        rows.append(
            f"<tr class='acriss-header'>"
            f"<td colspan='{n_cols}'>"
            f"{badge}{display_name} — {_html.escape(code)}"
            f"</td></tr>"
        )

        groups_sorted = sorted(
            code_df["external_code"].unique(),
            key=lambda ec: _min_total(code_df[code_df["external_code"] == ec]),
        )

        summary_totals: dict[int, list[float]] = {d: [] for d in durations}

        for ext_code in groups_sorted:
            group_df = code_df[code_df["external_code"] == ext_code]
            models_raw = group_df["example_models"].iloc[0]
            scraped_tx = group_df["transmission"].iloc[0]
            acriss_tx = group_df["acriss_transmission"].iloc[0]
            if pd.notna(scraped_tx) and scraped_tx:
                transmission_label = (
                    " (Manual)" if str(scraped_tx).lower().startswith("m")
                    else " (Automático)"
                )
            elif pd.notna(acriss_tx) and acriss_tx:
                transmission_label = (
                    " (Manual)" if str(acriss_tx).upper().startswith("M")
                    else " (Automático)"
                )
            else:
                transmission_label = ""
            models_text = (
                _html.escape(str(models_raw) if pd.notna(models_raw) else "")
                + _html.escape(transmission_label)
            )
            pending = bool(group_df["pending_review"].iloc[0])
            g_badge = "🔍 " if pending else ""

            dur_lookup: dict[int, tuple[float, float]] = {}
            for _, row in group_df.iterrows():
                d = int(row["duration_days"])
                dur_lookup[d] = (float(row["price_per_day"]), float(row["total_price"]))
                summary_totals[d].append(float(row["total_price"]))

            cat_td = (
                f"<td class='category'>"
                f"<div class='ext-code'>{g_badge}{_html.escape(ext_code)}</div>"
                f"<div class='models'>{models_text}</div>"
                f"</td>"
            )
            price_tds = []
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

            rows.append(f"<tr class='group-row'>{cat_td}{''.join(price_tds)}</tr>")

        if len(groups_sorted) >= 2:
            summary_tds: list[str] = []
            for d in durations:
                vals = summary_totals[d]
                if vals:
                    mn = min(vals)
                    med = float(pd.Series(vals).median())
                    avg = sum(vals) / len(vals)
                    mx = max(vals)
                    summary_tds.append(
                        f"<td class='price-cell'>"
                        f"<div class='total'>{mn:.0f} / {med:.0f} / {avg:.0f} / {mx:.0f} €</div>"
                        f"<div class='per-day'>min · med · avg · máx</div>"
                        f"</td>"
                    )
                else:
                    summary_tds.append("<td class='price-cell empty'>—</td>")

            rows.append(
                f"<tr class='summary-row'>"
                f"<td class='label'>Resumen ({len(groups_sorted)} grupos)</td>"
                f"{''.join(summary_tds)}"
                f"</tr>"
            )

    return (
        f"<table class='tariff-table'>"
        f"<thead>{header}</thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table>"
    )


def render_tariff() -> None:
    providers = get_active_provider_codes()
    if not providers:
        st.info("No hay providers activos con datos clasificados.")
        return

    # ── Filtros inline ────────────────────────────────────────────────────────
    c_prov, c_cat, c_pending = st.columns([1.0, 2.0, 1.0], gap="small")

    with c_prov:
        selected_provider = st.selectbox(
            "Provider",
            options=providers,
            key="tariff_provider_select",
        )
    with c_cat:
        codes_with_names = get_acriss_codes_with_display_names()
        code_to_label = {code: f"{name} — {code}" for code, name in codes_with_names}
        all_codes = [code for code, _ in codes_with_names]
        selected_codes = st.multiselect(
            "Categorías ACRISS",
            options=all_codes,
            default=[],
            format_func=lambda code: code_to_label.get(code, code),
            help="Vacío = todas las categorías.",
            key="tariff_acriss_filter",
        )
        acriss_categories = tuple(selected_codes) if selected_codes else None
    with c_pending:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        include_pending = st.checkbox(
            "Incl. revisión 🔍",
            value=True,
            key="tariff_pending",
        )

    if st.session_state.get("_tariff_last_provider") != selected_provider:
        st.session_state["tariff_zone_idx"] = 0
        st.session_state["_tariff_last_provider"] = selected_provider

    df = fetch_tariff_table(
        provider_code=selected_provider,
        acriss_codes=acriss_categories,
        durations=DURATION_BRACKET,
        include_pending_review=include_pending,
    )

    if df.empty:
        st.warning(
            "No hay datos de tarifa para este provider con los filtros actuales. "
            "Prueba a desactivar el filtro ACRISS o a incluir categorías en revisión."
        )
        return

    zones = (
        df[["start_date", "end_date"]]
        .drop_duplicates()
        .sort_values("start_date")
        .reset_index(drop=True)
    )
    n_zones = len(zones)

    idx = int(st.session_state.get("tariff_zone_idx", 0))
    idx = max(0, min(idx, n_zones - 1))

    col_nav, col_label = st.columns([0.10, 0.90], gap="small")
    with col_nav:
        col_prev, col_next = st.columns(2, gap="small")
        with col_prev:
            if st.button("◀", disabled=(idx == 0), key="tariff_prev_zone"):
                st.session_state["tariff_zone_idx"] = idx - 1
                st.rerun()
        with col_next:
            if st.button("▶", disabled=(idx == n_zones - 1), key="tariff_next_zone"):
                st.session_state["tariff_zone_idx"] = idx + 1
                st.rerun()
    with col_label:
        z = zones.iloc[idx]
        start_s = z["start_date"].strftime("%d %b %Y")
        end_s = z["end_date"].strftime("%d %b %Y")
        st.markdown(f"#### {start_s} – {end_s} &nbsp; · &nbsp; zona {idx + 1} / {n_zones}")

    zone_df = df[
        (df["start_date"] == z["start_date"]) & (df["end_date"] == z["end_date"])
    ]

    rep_dates = sorted(zone_df["representative_date"].dropna().unique())
    if len(rep_dates) == 1:
        rep_label = pd.Timestamp(rep_dates[0]).strftime("%d %b %Y")
    elif len(rep_dates) > 1:
        rep_label = (
            f"{pd.Timestamp(rep_dates[0]).strftime('%d %b')} – "
            f"{pd.Timestamp(rep_dates[-1]).strftime('%d %b %Y')}"
        )
    else:
        rep_label = "—"

    n_codes = zone_df["acriss_code"].nunique()
    n_groups = zone_df["external_code"].nunique()
    st.caption(
        f"{selected_provider} · {n_codes} categoría(s) · {n_groups} grupo(s) · "
        f"fecha repr.: {rep_label} · total (€) con €/día"
    )

    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_build_tariff_html(zone_df, list(DURATION_BRACKET)), unsafe_allow_html=True)

    pending_codes = sorted(
        zone_df.loc[zone_df["pending_review"], "acriss_code"].unique()
    )
    if pending_codes:
        st.caption(
            "🔍 Grupos pendientes de revisión manual en: "
            + ", ".join(pending_codes)
            + ". Los precios son orientativos."
        )
