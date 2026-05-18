"""Timeline tab: price evolution per ACRISS code over the scraped date range."""
from __future__ import annotations

import streamlit as st

from ..filters import Filters
from ..queries import fetch_timeline, get_acriss_codes_with_display_names

try:
    import plotly.express as px
    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False


def render_timeline(filters: Filters) -> None:
    codes_with_names = get_acriss_codes_with_display_names()
    if not codes_with_names:
        st.info("No hay categorías ACRISS con datos en la base de datos.")
        return

    code_to_label = {code: f"{code} — {name}" for code, name in codes_with_names}
    available_codes = [code for code, _ in codes_with_names]
    selected_code = st.selectbox(
        "Categoría ACRISS a visualizar",
        options=available_codes,
        index=0,
        format_func=lambda code: code_to_label.get(code, code),
        key="timeline_code_selector",
    )

    if not selected_code:
        st.info("Selecciona una categoría para ver su evolución.")
        return

    df = fetch_timeline(
        selected_code,
        filters.duration_days,
        filters.providers,
        filters.include_pending_review,
    )

    if df.empty:
        st.warning(
            "Sin datos de evolución para esta categoría con los filtros actuales. "
            "Comprueba que haya scrapes para los providers seleccionados."
        )
        return

    st.subheader(
        f"Evolución de precio — {selected_code}, {filters.duration_days} días"
    )

    if _PLOTLY_AVAILABLE:
        fig = px.line(
            df,
            x="pickup_date",
            y="price_per_day",
            color="provider_name",
            markers=True,
            title=f"Precio/día por fecha de pickup — {selected_code}",
            labels={
                "pickup_date": "Fecha de pickup",
                "price_per_day": "Precio/día (€)",
                "provider_name": "Provider",
            },
        )
        fig.update_layout(
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning(
            "plotly no está instalado. Instálalo con `pip install plotly` para ver el gráfico."
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

    with st.expander("Ver datos en tabla"):
        col_config = {
            "pickup_date": st.column_config.DateColumn("Fecha pickup"),
            "provider_code": st.column_config.TextColumn("Provider"),
            "provider_name": st.column_config.TextColumn("Nombre provider"),
            "price_per_day": st.column_config.NumberColumn("Precio/día", format="%.2f €"),
        }
        st.dataframe(df, use_container_width=True, hide_index=True, column_config=col_config)
