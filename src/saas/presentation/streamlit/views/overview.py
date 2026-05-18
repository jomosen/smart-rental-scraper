"""Overview tab: pivot table of avg prices per ACRISS code × provider.

Shows top-level metrics, a pivot table with optional 🔍 badge for pending-review
rows, and a drill-down section with the concrete vehicle models behind each code.
"""
from __future__ import annotations

import streamlit as st

from ..filters import Filters
from ..queries import fetch_market_overview, fetch_pvc_details, get_acriss_codes_with_display_names


def render_overview(filters: Filters) -> None:
    df = fetch_market_overview(
        filters.pickup_date,
        filters.duration_days,
        filters.providers,
        filters.acriss_categories,
        filters.include_pending_review,
    )

    if df.empty:
        st.warning(
            "No hay datos para los filtros seleccionados. "
            "Prueba a cambiar la fecha de pickup o la duración."
        )
        return

    # ── Métricas rápidas ──────────────────────────────────────────────────
    n_codes = df["acriss_code"].nunique()
    n_providers = df["provider_code"].nunique()
    comparable = (
        df.groupby("acriss_code")["provider_code"]
        .nunique()
        .pipe(lambda s: (s >= 2).sum())
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Categorías ACRISS con datos", n_codes)
    col2.metric("Providers", n_providers)
    col3.metric("Comparables (≥ 2 providers)", comparable)

    st.divider()

    # ── Tabla pivot: código ACRISS × provider ─────────────────────────────
    # Mark pending-review codes with a 🔍 badge in the display name.
    pending_codes = set(df.loc[df["has_pending_review"], "acriss_code"])
    df = df.copy()
    df["display_name"] = df.apply(
        lambda r: (
            f"🔍 {r['display_name']}" if r["acriss_code"] in pending_codes
            else r["display_name"]
        ),
        axis=1,
    )

    pivot = df.pivot_table(
        index=["acriss_code", "display_name"],
        columns="provider_name",
        values="avg_price_per_day",
        aggfunc="first",
    ).reset_index()
    pivot.columns.name = None

    # Build column_config: price columns formatted as € numbers.
    provider_names = df["provider_name"].unique().tolist()
    col_config = {
        "acriss_code": st.column_config.TextColumn("Código ACRISS", width="small"),
        "display_name": st.column_config.TextColumn("Categoría"),
    }
    for pname in provider_names:
        if pname in pivot.columns:
            col_config[pname] = st.column_config.NumberColumn(
                pname,
                format="%.2f €",
                help=f"Precio medio/día de {pname} para la fecha y duración seleccionadas.",
            )

    st.subheader(
        f"Precio medio/día — pickup {filters.pickup_date}, {filters.duration_days} días"
    )
    st.dataframe(
        pivot,
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
    )

    if pending_codes:
        st.caption(
            "🔍 Categorías marcadas con este icono tienen uno o más grupos de vehículos "
            "pendientes de revisión manual. Los precios son orientativos."
        )

    # ── Drill-down: PVCs concretos ────────────────────────────────────────
    st.divider()
    st.subheader("Detalle por modelo")

    available_codes = sorted(df["acriss_code"].unique().tolist())
    code_to_label = {
        code: f"{code} — {name}"
        for code, name in get_acriss_codes_with_display_names()
    }
    selected_code = st.selectbox(
        "Categoría a inspeccionar",
        options=available_codes,
        index=0,
        format_func=lambda code: code_to_label.get(code, code),
    )

    if selected_code:
        details = fetch_pvc_details(
            selected_code,
            filters.pickup_date,
            filters.duration_days,
            filters.providers,
            filters.include_pending_review,
        )
        if details.empty:
            st.info("Sin datos de detalle para esta categoría con los filtros actuales.")
        else:
            detail_col_config = {
                "provider_code": st.column_config.TextColumn("Provider"),
                "external_code": st.column_config.TextColumn("Código interno"),
                "example_models": st.column_config.TextColumn("Modelos"),
                "seats": st.column_config.NumberColumn("Plazas", format="%d"),
                "current_price_per_day": st.column_config.NumberColumn(
                    "Precio/día", format="%.2f €"
                ),
                "pending_review": st.column_config.CheckboxColumn("En revisión 🔍"),
                "classification_confidence": st.column_config.NumberColumn(
                    "Confianza", format="%.2f"
                ),
            }
            st.dataframe(
                details,
                use_container_width=True,
                hide_index=True,
                column_config=detail_col_config,
            )
