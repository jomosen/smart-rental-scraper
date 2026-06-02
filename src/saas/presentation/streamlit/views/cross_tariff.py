"""Cross-tariff view: recommended price from up to 3 providers per ACRISS code.

Layout mirrors the interactive proto (vista_cruzada.html):
  - Provider selection (multiselect, max 3)
  - Base aggregation control (min / med / avg / max)
  - Master calendar selector (whose zones drive the time partitioning)
  - Rounding selector
  - Rules panel (expander): global default + per-category overrides
  - Zone navigation (prev/next)
  - HTML price grid
  - Cell breakdown expander (replaces the proto's drawer)
  - Save-as-rule button (persists to pricing_rules via PricingRuleRepository)

Interaction model differences vs. proto
----------------------------------------
Streamlit sandboxes HTML, so cell onclick is not available.  The "Desglose"
(breakdown) is shown in a collapsible section below the grid; the user selects
ACRISS code + duration from dropdowns.

Data flow
---------
  fetch_cross_tariff_table() → raw DataFrame (all providers, all their zones)
  _align_to_master()         → restrict to master provider's zones
  compute_cell()             → CellResult per (acriss_code, duration, zone)
  _build_grid_html()         → HTML string for st.markdown
"""
from __future__ import annotations

import html as _html
from datetime import date
from decimal import Decimal
from functools import partial

import pandas as pd
import streamlit as st

from src.saas.application.pricing.cross_tariff_calc import (
    CellResult,
    DroppedProvider,
    ProviderContribution,
    RuleConfig,
    compute_cell,
)
from src.saas.infrastructure.persistence.engine import super_engine
from src.saas.infrastructure.persistence.repositories import PricingRuleRepository
from src.saas.infrastructure.persistence.session import super_session

from ..queries import (
    DURATION_BRACKET,
    fetch_cross_tariff_table,
    get_active_provider_codes,
    get_operator_tenant_id,
)

# ── CSS (extends tariff.py style; kept self-contained) ──────────────────────
_CSS = """
<style>
table.ct-table {
    border-collapse: collapse;
    width: 100%;
    font-family: inherit;
    font-size: 0.88em;
    line-height: 1.35;
}
table.ct-table th {
    background: #f0f2f6;
    padding: 9px 10px;
    text-align: center;
    border: 1px solid #ddd;
    white-space: nowrap;
    font-size: 0.86em;
    font-weight: 600;
    color: #5a6577;
}
table.ct-table th.cat-col { text-align: left; min-width: 160px; }

/* ACRISS header */
table.ct-table tr.acriss-hdr td {
    background: #eef2fd;
    font-weight: 700;
    font-size: 0.92em;
    padding: 7px 14px;
    border: 1px solid #d5e0fb;
    color: #3949ab;
}
table.ct-table tr.acriss-hdr td.dur-hdr {
    text-align: center;
    font-size: 0.80em;
    font-weight: 600;
    color: #6b83cc;
    padding: 7px 8px;
    white-space: nowrap;
}

/* Recommended row */
table.ct-table tr.rec-row td.cat {
    padding: 6px 10px 6px 22px;
    border: 1px solid #e6e9f0;
    font-weight: 600;
    vertical-align: top;
}
table.ct-table tr.rec-row td.cat .models {
    font-size: 0.76em;
    color: #97a0b0;
    font-weight: 400;
    margin-top: 3px;
    line-height: 1.6;
}
table.ct-table tr.rec-row td.cat .pname {
    font-weight: 600;
    color: #5a6577;
}
table.ct-table tr.rec-row td.cat .tx {
    color: #b0b8c8;
    font-size: 0.92em;
}
table.ct-table td.cell {
    padding: 7px 10px;
    border: 1px solid #e6e9f0;
    text-align: right;
    min-width: 96px;
    vertical-align: top;
}
table.ct-table td.cell .rec {
    font-weight: 700;
    font-size: 1.0em;
    white-space: nowrap;
}
table.ct-table td.cell .day {
    font-size: 0.76em;
    color: #97a0b0;
    white-space: nowrap;
}
table.ct-table td.cell .badge-row {
    font-size: 0.70em;
    margin-top: 3px;
    display: flex;
    gap: 3px;
    justify-content: flex-end;
    flex-wrap: wrap;
}
table.ct-table td.cell .basebadge {
    display: inline-block;
    margin-top: 3px;
    background: #f0f2f6;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 0.73em;
    color: #7a8498;
    white-space: nowrap;
}
table.ct-table td.cell.empty { color: #ccc; text-align: center; }

/* Flag badges */
.fbadge {
    border-radius: 4px;
    padding: 1px 5px;
    font-weight: 600;
    font-size: 0.68em;
    white-space: nowrap;
}
.fbadge.cov  { background:#f0f2f6; color:#7a8498; font-size: 0.80em; padding: 2px 7px; }
.fbadge.clamp{ background:#fdf5e6; color:#c9871f; }
.fbadge.anom { background:#fcebed; color:#d63a4e; }

/* Market summary row */
table.ct-table tr.mkt-row td {
    background: #f4f5f8;
    border: 1px solid #e0e2e8;
    padding: 6px 10px;
    text-align: right;
    font-size: 0.82em;
}
table.ct-table tr.mkt-row td.cat {
    text-align: left;
    padding: 6px 10px 6px 22px;
    font-weight: 600;
    color: #3b4561;
}
table.ct-table tr.mkt-row .mm { font-weight: 600; }
</style>
"""

_NF2 = "{:.2f}"
_NF0 = "{:.0f}"


def _fmt(v: Decimal) -> str:
    return _NF2.format(float(v)) + " €"


def _fmt_day(v: Decimal) -> str:
    return _NF2.format(float(v)) + " €/d"


# ── Session-state helpers ────────────────────────────────────────────────────

def _ss(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _default_rule() -> dict:
    return {"op": "sub", "val": 1.0, "mode": "pct", "floor": "auto", "ceiling": "max"}


# ── Zone alignment ───────────────────────────────────────────────────────────

def _master_zones(df: pd.DataFrame, master: str) -> pd.DataFrame:
    """Return one synthetic zone per unique representative_date of the master provider.

    Each zone groups all master groups scraped on the same representative_date,
    using min(start_date) / max(end_date) for the display label only.
    Navigating by representative_date guarantees that every master group
    appears in exactly one zone step.
    """
    mdf = (
        df[df["provider_code"] == master]
        .groupby("representative_date", as_index=False)
        .agg(start_date=("start_date", "min"), end_date=("end_date", "max"))
        .sort_values("representative_date")
        .reset_index(drop=True)
    )
    return mdf  # columns: representative_date, start_date, end_date


def _filter_zone(df: pd.DataFrame, master: str, rep_date: date,
                 start: date, end: date) -> pd.DataFrame:
    """Return all rows relevant to one synthetic zone.

    Master rows: all groups with exactly this representative_date.
    Non-master rows: groups whose representative_date falls within [start, end]
                     (the span of the master's synthetic zone) and whose
                     ACRISS code appears in the master snapshot.
    """
    master_rows = df[
        (df["provider_code"] == master) &
        (df["representative_date"] == rep_date)
    ]
    acriss_in_zone = set(master_rows["acriss_code"].unique())
    if not acriss_in_zone:
        return pd.DataFrame(columns=df.columns)

    def _in_window(grp):
        return grp[
            (grp["representative_date"] >= start) &
            (grp["representative_date"] <= end) &
            (grp["acriss_code"].isin(acriss_in_zone))
        ]

    parts = [master_rows]
    for pcode in df["provider_code"].unique():
        if pcode == master:
            continue
        parts.append(_in_window(df[df["provider_code"] == pcode]))

    return pd.concat(parts, ignore_index=True)


# ── Build CellResult map from a zone DataFrame ──────────────────────────────

def _build_cell_results(
    zone_df: pd.DataFrame,
    providers: list[str],
    base: str,
    global_rule: dict,
    category_rules: dict[str, dict],
    round_mode: str,
) -> dict[tuple[str, int], CellResult]:
    """Compute CellResult for every (acriss_code, duration) in the zone."""
    results: dict[tuple[str, int], CellResult] = {}
    if zone_df.empty:
        return results

    acriss_codes = zone_df["acriss_code"].unique()
    durations = zone_df["duration_days"].unique()

    for code in acriss_codes:
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

        for dur in durations:
            dur_df = code_df[code_df["duration_days"] == dur]
            # Build provider_groups: each provider may have multiple groups (same ACRISS)
            provider_groups: dict[str, list[Decimal]] = {}
            for pcode in providers:
                prows = dur_df[dur_df["provider_code"] == pcode]
                if not prows.empty:
                    provider_groups[pcode] = [Decimal(str(v)) for v in prows["total_price"]]

            missing = {p for p in providers if p not in provider_groups}
            result = compute_cell(
                acriss_code=code,
                duration=int(dur),
                provider_groups=provider_groups,
                stale_providers=set(),   # TODO(D5): wire heartbeats for staleness
                stale_days={},
                missing_providers=missing,
                rule=rule,
                base=base,
                round_mode=round_mode,
                rule_is_default=is_default,
                total_providers=len(providers),
            )
            results[(code, int(dur))] = result

    return results


# ── HTML grid ────────────────────────────────────────────────────────────────

def _build_grid_html(
    cell_results: dict[tuple[str, int], CellResult],
    zone_df: pd.DataFrame,
    durations: list[int],
    n_providers: int,
) -> str:
    if zone_df.empty:
        return "<p style='color:#97a0b0'>Sin datos para esta zona.</p>"

    dur_cells = "".join(f"<td class='dur-hdr'>{d}d</td>" for d in durations)

    acriss_meta = (
        zone_df[["acriss_code", "acriss_display_name", "pending_review"]]
        .drop_duplicates("acriss_code")
        .set_index("acriss_code")
    )
    codes = sorted(acriss_meta.index, key=lambda c: float(
        cell_results.get((c, 7), CellResult(c, 7, None, None, None, None, None, None, None)).rec_total or 9999
    ))

    # Collect example_models per ACRISS code.
    # Each line: "Provider · models (Transmission)", sorted cheapest group first.
    code_to_models: dict[str, str] = {}
    ref_dur = 7
    for code in codes:
        code_df = zone_df[zone_df["acriss_code"] == code].dropna(subset=["example_models"])
        avail_durs = code_df["duration_days"].unique()
        dur = ref_dur if ref_dur in avail_durs else (avail_durs[0] if len(avail_durs) else None)
        ref_df = code_df[code_df["duration_days"] == dur] if dur is not None else code_df

        seen_keys: set = set()
        lines: list[tuple[float, str]] = []
        for _, row in ref_df.sort_values("total_price").iterrows():
            key = (str(row["provider_code"]), str(row["example_models"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            pname = str(row["provider_code"]).capitalize()
            models_escaped = _html.escape(str(row["example_models"]).strip())
            price = float(row["total_price"]) if pd.notna(row["total_price"]) else 9999.0

            # Resolve transmission: prefer scraped value, fall back to ACRISS char
            tx: str | None = None
            if pd.notna(row.get("transmission")) and row["transmission"]:
                tx = "Manual" if str(row["transmission"]).lower().startswith("m") else "Auto"
            elif pd.notna(row.get("acriss_transmission")) and row["acriss_transmission"]:
                at = str(row["acriss_transmission"]).upper()
                tx = "Manual" if at == "M" else ("Auto" if at in ("A", "B", "D") else None)

            tx_html = f" <span class='tx'>({tx})</span>" if tx else ""
            line = (
                f"<span class='pname'>{_html.escape(pname)}</span>"
                f" · {models_escaped}{tx_html}"
            )
            lines.append((price, line))

        code_to_models[code] = "<br>".join(ln for _, ln in lines)

    rows: list[str] = []
    for code in codes:
        meta = acriss_meta.loc[code]
        has_pending = bool(meta["pending_review"])
        badge = "🔍 " if has_pending else ""
        rows.append(
            f"<tr class='acriss-hdr'>"
            f"<td>{badge}{_html.escape(str(meta['acriss_display_name']))} — {_html.escape(code)}</td>"
            f"{dur_cells}</tr>"
        )

        # Recommended row — category cell includes example models
        models_html = code_to_models.get(code, "")
        models_div = f"<div class='models'>{models_html}</div>" if models_html else ""
        cells = [f"<td class='cat'>{models_div}</td>"]
        for dur in durations:
            cr = cell_results.get((code, dur))
            if cr is None or cr.rec_total is None:
                cells.append("<td class='cell empty'>—</td>")
                continue

            flags_html = ""
            badges = []
            if cr.flags.clamped:
                badges.append(f"<span class='fbadge clamp'>{cr.flags.clamped}</span>")
            if cr.flags.degraded:
                badges.append("<span class='fbadge anom'>anomalía descartada</span>")
            if badges:
                flags_html = f"<div class='badge-row'>{''.join(badges)}</div>"

            cells.append(
                f"<td class='cell'>"
                f"<div class='rec'>{_fmt(cr.rec_total)}</div>"
                f"<div class='day'>{_fmt_day(cr.rec_per_day)}</div>"
                f"<div class='basebadge'>base {_fmt(cr.base_total)}</div>"
                f"{flags_html}"
                f"</td>"
            )
        rows.append(f"<tr class='rec-row'>{''.join(cells)}</tr>")

        # Market summary row
        mkt_cells = ["<td class='cat'>Mercado</td>"]
        for dur in durations:
            cr = cell_results.get((code, dur))
            if cr is None or not cr.present:
                mkt_cells.append("<td></td>")
                continue
            totals = [p.total for p in cr.present]
            lo, hi = min(totals), max(totals)
            if round(float(lo)) == round(float(hi)):
                rng = _NF0.format(float(lo)) + " €"
            else:
                rng = f"{_NF0.format(float(lo))}–{_NF0.format(float(hi))} €"
            mkt_cells.append(f"<td class='mkt-row'><span class='mm'>{rng}</span></td>")
        rows.append(f"<tr class='mkt-row'>{''.join(mkt_cells)}</tr>")

    return f"<table class='ct-table'><tbody>{''.join(rows)}</tbody></table>"


# ── Cell breakdown (replaces drawer) ────────────────────────────────────────

def _render_breakdown(cr: CellResult, provider_names: dict[str, str]) -> None:
    """Render step-by-step pipeline for one cell inside an expander."""
    st.markdown(f"**{cr.acriss_code} · {cr.duration}d**")

    st.markdown("**Proveedores**")
    for p in cr.present:
        who = provider_names.get(p.provider_code, p.provider_code)
        stale_note = f" *(stale {p.stale_days}d)*" if p.stale else ""
        base_note = " ← base" if p.is_base else ""
        st.markdown(
            f"- **{who}**{stale_note}: {_fmt(p.total)} / {_fmt_day(p.per_day)}{base_note}"
        )
    for d in cr.dropped:
        who = provider_names.get(d.provider_code, d.provider_code)
        total_note = f" ({_fmt(d.total)})" if d.total else ""
        st.markdown(f"- ~~{who}~~{total_note} — *{d.reason}*")

    if cr.base_total is None:
        st.warning("Sin datos suficientes para esta celda.")
        return

    st.markdown("**Pipeline de cálculo**")
    rule = cr.rule_used
    rule_lbl = "global" if cr.rule_is_default else "propia"
    op_txt = ("−" if rule.op == "sub" else "+") + f" {rule.val}{('%' if rule.mode == 'pct' else ' €')}"

    rows = [
        ("Base (agregado de mercado)", _fmt(cr.base_total)),
        (f"Regla {op_txt} [{rule_lbl}]", _fmt(cr.adjusted_total)),
    ]
    if cr.flags.clamped:
        fv = _fmt(cr.floor_val) if cr.floor_val else "—"
        cv = _fmt(cr.ceiling_val) if cr.ceiling_val else "—"
        rows.append((
            f"Clamp {cr.flags.clamped} (suelo {fv} · techo {cv})",
            _fmt(cr.clamped_total),
        ))
    else:
        fv = _fmt(cr.floor_val) if cr.floor_val else "—"
        cv = _fmt(cr.ceiling_val) if cr.ceiling_val else "—"
        rows.append((f"Suelo {fv} · techo {cv} → ok", _fmt(cr.clamped_total)))

    flip_note = " *(guard activado)*" if cr.round_flip else ""
    rows.append((f"Redondeo{flip_note}", _fmt(cr.rec_total)))
    rows.append(("**Total recomendado**", f"**{_fmt(cr.rec_total)}**"))
    rows.append(("Equivale a", _fmt_day(cr.rec_per_day)))

    df = pd.DataFrame(rows, columns=["Paso", "Valor"])
    st.table(df)

    flags = []
    if cr.flags.coverage < cr.flags.total_providers:
        flags.append(f"cobertura {cr.flags.coverage}/{cr.flags.total_providers}")
    if cr.flags.clamped:
        flags.append(f"clamped:{cr.flags.clamped}")
    if cr.flags.stale:
        flags.append("stale")
    if cr.flags.degraded:
        flags.append("anomalía descartada")
    if cr.flags.inferred:
        flags.append("precio inferido de zona")
    if flags:
        st.caption("Flags: " + " · ".join(flags))


# ── Rules panel ──────────────────────────────────────────────────────────────

def _render_rules_panel(available_codes: list[tuple[str, str]]) -> None:
    """Edit global default rule + per-category overrides in session state."""
    global_rule = _ss("ct_global_rule", _default_rule())
    cat_rules: dict[str, dict] = _ss("ct_category_rules", {})

    with st.expander("Reglas de pricing", expanded=True):
        st.markdown("**Regla global por defecto**")
        _rule_row_ui("_default", global_rule, available_codes, is_default=True)
        st.session_state["ct_global_rule"] = global_rule

        if cat_rules:
            st.markdown("**Overrides por categoría**")
        to_delete = []
        for code, rule in list(cat_rules.items()):
            label = next((lbl for c, lbl in available_codes if c == code), code)
            col_del, col_rule = st.columns([0.08, 0.92])
            with col_del:
                if st.button("✕", key=f"del_rule_{code}", help=f"Eliminar regla de {code}"):
                    to_delete.append(code)
            with col_rule:
                st.markdown(f"**{code}** — {label}")
                _rule_row_ui(code, rule, available_codes, is_default=False)
        for code in to_delete:
            del cat_rules[code]

        # Add override
        unused = [(c, lbl) for c, lbl in available_codes if c not in cat_rules]
        if unused:
            st.markdown("---")
            add_col, btn_col = st.columns([0.7, 0.3])
            with add_col:
                sel = st.selectbox(
                    "Añadir override por categoría",
                    options=[c for c, _ in unused],
                    format_func=lambda c: f"{c} — {next(lbl for cc, lbl in unused if cc == c)}",
                    key="ct_add_cat_sel",
                )
            with btn_col:
                st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
                if st.button("Añadir", key="ct_add_cat_btn"):
                    cat_rules[sel] = dict(global_rule)
                    st.rerun()

    st.session_state["ct_category_rules"] = cat_rules


def _rule_row_ui(key: str, rule: dict, _available_codes, is_default: bool) -> None:
    c1, c2, c3, c4, c5 = st.columns([0.18, 0.22, 0.18, 0.22, 0.20])
    with c1:
        rule["op"] = st.selectbox(
            "Operación", ["sub", "add"],
            index=0 if rule["op"] == "sub" else 1,
            format_func=lambda x: "− restar" if x == "sub" else "+ sumar",
            key=f"op_{key}",
        )
    with c2:
        rule["val"] = st.number_input(
            "Valor", value=float(rule["val"]), step=0.5, min_value=0.0,
            key=f"val_{key}",
        )
    with c3:
        rule["mode"] = st.selectbox(
            "Modo", ["pct", "abs"],
            index=0 if rule["mode"] == "pct" else 1,
            format_func=lambda x: "%" if x == "pct" else "€",
            key=f"mode_{key}",
        )
    with c4:
        rule["floor"] = st.selectbox(
            "Suelo", ["auto", "cost", "none"],
            index=["auto", "cost", "none"].index(rule["floor"]),
            format_func=lambda x: {"auto": "auto (hist.)", "cost": "coste+margen", "none": "sin suelo"}[x],
            key=f"floor_{key}",
        )
    with c5:
        rule["ceiling"] = st.selectbox(
            "Techo", ["max", "none"],
            index=0 if rule["ceiling"] == "max" else 1,
            format_func=lambda x: "máx de mercado" if x == "max" else "sin techo",
            key=f"ceil_{key}",
        )


# ── Main render ──────────────────────────────────────────────────────────────

def render_cross_tariff() -> None:
    all_providers = get_active_provider_codes()
    if not all_providers:
        st.info("No hay providers activos con datos clasificados.")
        return

    # ── Controls ────────────────────────────────────────────────────────────
    col_prov, col_base, col_master, col_round = st.columns([1.6, 1.0, 1.1, 0.9])

    with col_prov:
        st.markdown("**Proveedores** (máx. 3)")
        selected = st.multiselect(
            "Proveedores",
            options=all_providers,
            default=_ss("ct_providers", all_providers[:min(3, len(all_providers))]),
            max_selections=3,
            label_visibility="collapsed",
            key="ct_providers_widget",
        )
        if selected != st.session_state.get("ct_providers"):
            st.session_state["ct_providers"] = selected
            st.session_state.pop("ct_zone_idx", None)

    providers: list[str] = st.session_state.get("ct_providers", selected)

    with col_base:
        st.markdown("**Base de precio**")
        base = st.radio(
            "Base",
            ["min", "med", "avg", "max"],
            horizontal=True,
            index=["min", "med", "avg", "max"].index(_ss("ct_base", "min")),
            format_func=lambda x: {"min": "mín", "med": "med", "avg": "avg", "max": "máx"}[x],
            label_visibility="collapsed",
            key="ct_base_widget",
        )
        st.session_state["ct_base"] = base

    with col_master:
        st.markdown("**Calendario maestro**")
        master_options = providers if providers else all_providers[:1]
        master_default = _ss("ct_master", master_options[0] if master_options else "")
        if master_default not in master_options:
            master_default = master_options[0]
        master = st.selectbox(
            "Maestro",
            options=master_options,
            index=master_options.index(master_default),
            label_visibility="collapsed",
            key="ct_master_widget",
        )
        st.session_state["ct_master"] = master

    with col_round:
        st.markdown("**Redondeo**")
        round_opts = ["0", "0.99", "0.90", "0.50", "1"]
        round_labels = {"0": "sin redondeo", "0.99": "a ,99", "0.90": "a ,90",
                        "0.50": "a ,50", "1": "a entero"}
        round_mode = st.selectbox(
            "Redondeo",
            options=round_opts,
            index=round_opts.index(_ss("ct_round", "0")),
            format_func=lambda x: round_labels[x],
            label_visibility="collapsed",
            key="ct_round_widget",
        )
        st.session_state["ct_round"] = round_mode

    if not providers:
        st.warning("Selecciona al menos un proveedor.")
        return

    # ── Load data ────────────────────────────────────────────────────────────
    df = fetch_cross_tariff_table(
        provider_codes=tuple(providers),
        acriss_codes=None,
        durations=DURATION_BRACKET,
        include_pending_review=True,
    )

    if df.empty:
        st.warning("Sin datos para los proveedores seleccionados. Lanza el pipeline primero.")
        return

    # ── Rules panel ──────────────────────────────────────────────────────────
    available_codes = sorted(
        df[["acriss_code", "acriss_display_name"]].drop_duplicates().values.tolist(),
        key=lambda x: x[0],
    )
    _render_rules_panel(available_codes)

    global_rule: dict = st.session_state.get("ct_global_rule", _default_rule())
    cat_rules: dict[str, dict] = st.session_state.get("ct_category_rules", {})

    # ── Zone navigation ──────────────────────────────────────────────────────
    zones = _master_zones(df, master)
    if zones.empty:
        st.warning(f"El proveedor maestro ({master}) no tiene zonas activas.")
        return

    n_zones = len(zones)
    idx = int(st.session_state.get("ct_zone_idx", 0))
    idx = max(0, min(idx, n_zones - 1))

    nav_prev, nav_label, nav_next = st.columns([0.06, 0.88, 0.06])
    with nav_prev:
        if st.button("◀", disabled=(idx == 0), key="ct_zone_prev"):
            st.session_state["ct_zone_idx"] = idx - 1
            st.rerun()
    with nav_next:
        if st.button("▶", disabled=(idx == n_zones - 1), key="ct_zone_next"):
            st.session_state["ct_zone_idx"] = idx + 1
            st.rerun()
    with nav_label:
        z = zones.iloc[idx]
        start_s = pd.Timestamp(z["start_date"]).strftime("%d %b %Y")
        end_s = pd.Timestamp(z["end_date"]).strftime("%d %b %Y")
        rep_s = pd.Timestamp(z["representative_date"]).strftime("%d %b %Y")
        st.markdown(f"#### {start_s} – {end_s} &nbsp;·&nbsp; zona {idx + 1} / {n_zones}")

    zone_df = _filter_zone(
        df, master,
        rep_date=z["representative_date"],
        start=z["start_date"],
        end=z["end_date"],
    )

    st.caption(
        f"Maestro: **{master}** · {', '.join(providers)} · "
        f"fecha repr.: {rep_s} · precios en total (€) + €/día derivado"
    )

    # ── Compute grid ─────────────────────────────────────────────────────────
    cell_results = _build_cell_results(
        zone_df=zone_df,
        providers=providers,
        base=base,
        global_rule=global_rule,
        category_rules=cat_rules,
        round_mode=round_mode,
    )

    # ── Render grid ──────────────────────────────────────────────────────────
    st.markdown(_CSS, unsafe_allow_html=True)
    grid_html = _build_grid_html(
        cell_results=cell_results,
        zone_df=zone_df,
        durations=list(DURATION_BRACKET),
        n_providers=len(providers),
    )
    st.markdown(grid_html, unsafe_allow_html=True)

    # ── Legend ───────────────────────────────────────────────────────────────
    st.caption(
        "🔢 cobertura parcial (proveedores con dato) &nbsp;·&nbsp; "
        "🟡 clamp suelo/techo &nbsp;·&nbsp; "
        "🟣 stale &nbsp;·&nbsp; "
        "🔴 anomalía descartada"
    )

    # ── Cell breakdown ───────────────────────────────────────────────────────
    with st.expander("Desglose de celda"):
        if not cell_results:
            st.info("No hay resultados calculados para esta zona.")
        else:
            breakdown_codes = sorted({c for c, _ in cell_results})
            breakdown_durs = sorted({d for _, d in cell_results})
            bc1, bc2 = st.columns(2)
            with bc1:
                sel_code = st.selectbox("Categoría ACRISS", breakdown_codes,
                                        key="ct_breakdown_code")
            with bc2:
                sel_dur = st.selectbox("Duración (días)", breakdown_durs,
                                       key="ct_breakdown_dur")
            cr = cell_results.get((sel_code, sel_dur))
            if cr:
                provider_names = {p: p for p in providers}
                _render_breakdown(cr, provider_names)
            else:
                st.info("Sin resultado para esta combinación.")

    # ── Footer actions ───────────────────────────────────────────────────────
    st.markdown("---")
    col_note, col_csv, col_save = st.columns([0.6, 0.2, 0.2])

    with col_note:
        st.caption(
            "**Preview en vivo.** Al guardar se persiste como regla versionada. "
            "El botón Exportar CSV es un stub pendiente de implementar."
        )
    with col_csv:
        if st.button("Exportar CSV", key="ct_export_csv"):
            st.info("Exportación CSV pendiente de implementar (stub).")
    with col_save:
        if st.button("💾 Guardar como regla", key="ct_save_rule", type="primary"):
            _save_rule(providers, master, base, round_mode, global_rule, cat_rules)


def _save_rule(
    providers: list[str],
    master: str,
    base: str,
    round_mode: str,
    global_rule: dict,
    cat_rules: dict[str, dict],
) -> None:
    tenant_id = get_operator_tenant_id()
    if tenant_id is None:
        st.error(
            "No hay tenants en la base de datos. "
            "Crea un tenant antes de guardar una regla de pricing."
        )
        return

    formula = {
        "providers": providers,
        "base_aggregation": base,
        "master_provider": master,
        "rounding": round_mode,
        "global_rule": global_rule,
        "category_overrides": cat_rules,
    }

    engine = super_engine()
    session_factory = partial(super_session, engine)
    try:
        with session_factory() as session:
            repo = PricingRuleRepository(session)
            rule = repo.save(
                tenant_id=tenant_id,
                name="Tarifa cruzada",
                formula_jsonb=formula,
                acriss_code=None,
            )
            session.commit()
        st.success(f"Regla guardada — versión {rule.version} (id: {rule.id})")
    except Exception as exc:
        st.error(f"Error al guardar la regla: {exc}")
