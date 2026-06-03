"""Overview tab: pivot table of avg prices per ACRISS code × provider."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from ..queries import (
    fetch_market_overview,
    fetch_pvc_details,
    get_acriss_codes_with_display_names,
    get_active_provider_codes,
    get_pickup_date_range,
)

_DURATION_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 14, 21, 28]
_DEFAULT_DURATION_INDEX = 6  # 7 días


def render_overview() -> None:
    # ── Filtros inline ────────────────────────────────────────────────────────
    c_date, c_dur, c_prov, c_cat, c_pending = st.columns([1.2, 0.8, 1.5, 1.5, 1.0], gap="small")

    min_date, max_date = get_pickup_date_range()
    default_pickup = min(max_date, date.today() + timedelta(days=30))

    with c_date:
        pickup_date = st.date_input(
            "Fecha de pickup",
            value=default_pickup,
            min_value=min_date,
            max_value=max_date,
        )
    with c_dur:
        duration_days = int(st.selectbox(
            "Duración (días)",
            options=_DURATION_OPTIONS,
            index=_DEFAULT_DURATION_INDEX,
        ))
    with c_prov:
        all_providers = get_active_provider_codes()
        selected_providers = tuple(st.multiselect(
            "Providers",
            options=all_providers,
            default=all_providers,
        ))
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
        )
        acriss_categories = tuple(selected_codes) if selected_codes else None
    with c_pending:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        include_pending = st.checkbox(
            "Incl. revisión 🔍",
            value=True,
            help=(
                "Los grupos 'en revisión' tienen una clasificación ACRISS tentativa "
                "que aún no ha sido confirmada."
            ),
        )

    # ── Datos ─────────────────────────────────────────────────────────────────
    df = fetch_market_overview(
        pickup_date,
        duration_days,
        selected_providers,
        acriss_categories,
        include_pending,
    )

    if df.empty:
        st.warning(
            "No hay datos para los filtros seleccionados. "
            "Prueba a cambiar la fecha de pickup o la duración."
        )
        return

    # ── Métricas rápidas ──────────────────────────────────────────────────────
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

    # ── Tabla pivot: código ACRISS × provider ─────────────────────────────────
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

    st.subheader(f"Precio medio/día — pickup {pickup_date}, {duration_days} días")
    st.dataframe(pivot, use_container_width=True, hide_index=True, column_config=col_config)

    if pending_codes:
        st.caption(
            "🔍 Categorías marcadas con este icono tienen uno o más grupos de vehículos "
            "pendientes de revisión manual. Los precios son orientativos."
        )

    # ── Drill-down: PVCs concretos ────────────────────────────────────────────
    st.divider()
    st.subheader("Detalle por modelo")

    available_codes = sorted(df["acriss_code"].unique().tolist())
    selected_code = st.selectbox(
        "Categoría a inspeccionar",
        options=available_codes,
        index=0,
        format_func=lambda code: code_to_label.get(code, code),
    )

    if selected_code:
        details = fetch_pvc_details(
            selected_code,
            pickup_date,
            duration_days,
            selected_providers,
            include_pending,
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
