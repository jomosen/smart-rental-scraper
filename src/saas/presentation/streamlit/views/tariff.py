"""Tariff tab: per-provider price matrix (ACRISS × zone × duration).

Shows one section per ACRISS category, each with a pivot table whose rows are
homogeneous zones (date ranges) and columns are rental durations.  Cells hold
the MIN price/day across all PVCs sharing that ACRISS code in that zone.
"""
from __future__ import annotations

import streamlit as st

from ..filters import Filters
from ..queries import (
    _DEFAULT_DURATIONS,
    fetch_tariff_table,
    get_active_provider_codes,
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

    df = fetch_tariff_table(
        provider_code=selected_provider,
        acriss_codes=filters.acriss_categories,
        durations=_DEFAULT_DURATIONS,
        include_pending_review=filters.include_pending_review,
    )

    if df.empty:
        st.warning(
            "No hay datos de tarifa para este provider con los filtros actuales. "
            "Prueba a desactivar el filtro ACRISS o a incluir categorías en revisión."
        )
        return

    st.caption(
        f"Precio mínimo/día (€) por zona y duración · {selected_provider} "
        f"· {df['acriss_code'].nunique()} categoría(s)"
    )

    for acriss_code, group in df.groupby("acriss_code", sort=False):
        display_name = group["display_name"].iloc[0]
        has_pending = bool(group["has_pending_review"].any())

        if has_pending:
            st.subheader(f"🔍 {acriss_code} — {display_name}")
        else:
            st.subheader(f"{acriss_code} — {display_name}")

        pivot = group.pivot_table(
            index=["start_date", "end_date"],
            columns="duration_days",
            values="price_per_day",
            aggfunc="first",
        ).reset_index()
        pivot.columns.name = None

        pivot["Período"] = pivot.apply(
            lambda r: (
                f"{r['start_date'].strftime('%d/%m')} → {r['end_date'].strftime('%d/%m')}"
            ),
            axis=1,
        )
        pivot = pivot.drop(columns=["start_date", "end_date"])

        duration_cols = [c for c in pivot.columns if isinstance(c, int)]
        pivot = pivot[["Período"] + duration_cols]

        col_config: dict = {
            "Período": st.column_config.TextColumn("Período", width="medium"),
        }
        for d in duration_cols:
            col_config[d] = st.column_config.NumberColumn(
                f"{d}d",
                format="%.2f €",
                help=f"Precio mínimo/día para {d} día(s) de alquiler.",
            )

        st.dataframe(
            pivot,
            use_container_width=True,
            hide_index=True,
            column_config=col_config,
        )

        if has_pending:
            st.caption(
                "🔍 Esta categoría tiene uno o más grupos pendientes de revisión manual. "
                "Los precios son orientativos."
            )

        st.divider()
