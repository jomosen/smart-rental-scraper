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
import streamlit.components.v1 as _components
from sqlalchemy import text

from src.saas.application.pricing.cross_tariff_calc import (
    CellResult,
    DroppedProvider,
    ProviderContribution,
    RuleConfig,
    compute_cell,
)
# Shared, presentation-agnostic logic — single source consumed by both the
# Streamlit back-office and the client API (see application/pricing/cross_tariff_assembler).
from src.saas.application.pricing.cross_tariff_assembler import (
    build_cell_results as _build_cell_results,
    category_sort_key as _category_sort_key,
    code_label as _code_label,
    filter_zone as _filter_zone,
    master_zones as _master_zones,
    prov_color as _prov_color,
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

/* ── Nav button pair: collapse gap in nested column blocks ── */
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] {
    gap: 2px !important;
}
[data-testid="stHorizontalBlock"] [data-testid="stHorizontalBlock"] [data-testid="column"] {
    padding-left: 1px !important;
    padding-right: 1px !important;
    min-width: 0 !important;
    flex: 0 0 auto !important;
}

/* ── ACRISS category band ── */
table.ct-table tr.acriss-hdr { cursor: pointer; }
table.ct-table tr.acriss-hdr td {
    background: #eef2fd;
    border: 1px solid #d5e0fb;
    padding: 9px 14px;
}
table.ct-table tr.acriss-hdr td.cat-col {
    text-align: left;
    font-weight: 700;
    font-size: 0.92em;
    font-family: 'Fraunces', serif;
    color: #3949ab;
    min-width: 200px;
    white-space: nowrap;
}
table.ct-table tr.acriss-hdr td.dur-hdr {
    text-align: right;
    font-size: 0.80em;
    font-weight: 600;
    color: #6b83cc;
    padding: 9px 10px;
    white-space: nowrap;
    min-width: 90px;
}

/* Chevron SVG — angle-down; JS rotates 180deg on expand via style.transform */
.chev-wrap {
    display: inline-flex; align-items: center; margin-right: 8px;
    color: #6b83cc; user-select: none; vertical-align: middle;
    transition: transform 0.18s ease;
}

/* ACRISS code chip — shown in summary row left cell (not in the band) */
.acode {
    font-size: 0.83em; letter-spacing: .03em;
    background: #fff; border: 1px solid #d5e0fb;
    border-radius: 5px; padding: 1px 7px;
    color: #3949ab; font-weight: 600; margin-right: 5px;
}

/* ── Recommended row (first inside each category) ── */
table.ct-table tr.close-row td {
    background: #f7f9fc;
    border: 1px solid #e0e2e8;
}
table.ct-table td.cat.close {
    padding: 8px 14px 8px 18px;
    text-align: left;
    vertical-align: top;
}
table.ct-table td.cell.close {
    padding: 7px 10px;
    text-align: right;
    vertical-align: middle;
    min-width: 90px;
}
table.ct-table td.cell.close .rec {
    font-weight: 700;
    font-size: 1.0em;
    line-height: 18px;
    white-space: nowrap;
}
table.ct-table td.cell.close .sub-line {
    color: #97a0b0;
    font-size: 0.74em;
    line-height: 15px;
    margin-top: 2px;
    white-space: nowrap;
}
table.ct-table td.cell.close.empty { color: #ccc; text-align: center; }

/* Flag badges (small colored squares/dots) */
.rowbadges {
    display: flex; gap: 5px; align-items: center;
    flex-wrap: wrap; min-height: 14px;
}
.catexamples {
    color: #97a0b0; font-size: 11px; margin-top: 5px; line-height: 1.35;
}
.b { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
.b.cov   { background: #97a0b0; }
.b.clamp { background: #c9871f; }
.b.stale { background: #7a6cd6; }
.b.anom  { background: #d63a4e; }
.b.inf   { background: #3a63d6; border-radius: 50%; }

/* ── Provider subrows ── */
table.ct-table tr.prov-row td {
    border-top: 1px solid #f0f2f6;
    background: #fff;
}
table.ct-table tr.prov-row:hover td { background: #fafbfd; }
table.ct-table td.cat.prov {
    padding: 7px 14px 7px 18px;
    text-align: left;
    white-space: nowrap;
    vertical-align: middle;
}
.pdot  { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
.pname { font-weight: 600; font-size: 0.82em; color: #1b2230; }
.pmodel { color: #5a6577; font-size: 0.78em; margin-left: 6px; }
.tx { color: #97a0b0; }
.rtag {
    font-size: 0.68em; text-transform: uppercase; letter-spacing: .06em; font-weight: 600;
    border-radius: 4px; padding: 1px 5px; margin-left: 6px; vertical-align: middle;
}
.rtag.stale { background: #efecfb; color: #7a6cd6; }
.rtag.inf   { background: #eef2fd; color: #3a63d6; }
table.ct-table td.cell.sub {
    padding: 7px 10px;
    border-top: 1px solid #f0f2f6;
    text-align: right;
    vertical-align: top;
    min-width: 90px;
}
table.ct-table td.cell.sub .rec { font-weight: 500; font-size: 0.87em; white-space: nowrap; }
table.ct-table td.cell.sub .day { color: #97a0b0; font-size: 0.73em; white-space: nowrap; }
table.ct-table td.cell.sub.base-hit .rec { font-weight: 700; }
table.ct-table td.cell.sub.empty { color: #ccc; text-align: center; }
</style>
"""

_GRID_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Fraunces'
    ':opsz,wght@9..144,500;9..144,600'
    '&family=Hanken+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">'
)
_GRID_BODY_CSS = (
    "<style>"
    "body{margin:0;font-family:'Hanken Grotesk',sans-serif;"
    "font-size:14px;line-height:1.45;color:#1b2230}"
    "</style>"
)
_GRID_SCRIPT = """<script>
(function () {
  function resize() {
    window.parent.postMessage(
      { isStreamlitMessage: true, type: 'streamlit:setFrameHeight',
        height: document.body.scrollHeight + 4 }, '*');
  }
  document.addEventListener('click', function (e) {
    var hdr = e.target.closest('tr.acriss-hdr');
    if (!hdr) return;
    var code = hdr.getAttribute('data-code');
    if (!code) return;
    var rows = Array.from(document.querySelectorAll('tr[data-cat="' + code + '"]'));
    if (!rows.length) return;
    var expanding = rows[0].style.display === 'none';
    rows.forEach(function (r) { r.style.display = expanding ? '' : 'none'; });
    var wrap = hdr.querySelector('.chev-wrap');
    if (wrap) wrap.style.transform = expanding ? 'rotate(180deg)' : '';
    resize();
  });
  var toggleBtn = document.getElementById('ct-toggle-all');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      var allProv = Array.from(document.querySelectorAll('tr.prov-row'));
      var doExpand = allProv.some(function (r) { return r.style.display === 'none'; });
      allProv.forEach(function (r) { r.style.display = doExpand ? '' : 'none'; });
      document.querySelectorAll('tr.acriss-hdr .chev-wrap').forEach(function (w) {
        w.style.transform = doExpand ? 'rotate(180deg)' : '';
      });
      this.innerHTML = doExpand ? '&#9662; Colapsar todo' : '&#9658; Expandir todo';
      resize();
    });
  }
  window.addEventListener('load', function () { setTimeout(resize, 80); });
}());
</script>"""

def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _fmt_num(value: float, decimals: int = 2) -> str:
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _fmt(v: Decimal) -> str:
    return _fmt_num(float(v)) + " €"


def _fmt_day(v: Decimal) -> str:
    return _fmt_num(float(v)) + " €/d"


# ── Session-state helpers ────────────────────────────────────────────────────

def _ss(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _default_rule() -> dict:
    return {"op": "sub", "val": 1.0, "mode": "pct", "floor": "auto", "ceiling": "max"}


# Zone alignment, cell-result building and ACRISS naming/ordering now live in
# application/pricing/cross_tariff_assembler (imported above as _master_zones,
# _filter_zone, _build_cell_results, _code_label, _category_sort_key) so the API
# and this view share one implementation.

# Inline SVG angle-down chevron — no unicode chars; JS rotates 180° on expand.
_SVG_CHEVRON = (
    "<svg class='chev-svg' width='11' height='11' viewBox='0 0 24 24' "
    "fill='none' stroke='currentColor' stroke-width='2.5' "
    "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
    "<path d='M6 9l6 6 6-6'/></svg>"
)


# ── HTML grid ────────────────────────────────────────────────────────────────

def _build_grid_html(
    cell_results: dict[tuple[str, int], CellResult],
    zone_df: pd.DataFrame,
    durations: list[int],
    providers: list[str],
    examples: dict[str, list[str]],
) -> str:
    if zone_df.empty:
        return "<p style='color:#97a0b0'>Sin datos para esta zona.</p>"

    acriss_meta = (
        zone_df[["acriss_code", "acriss_display_name", "pending_review"]]
        .drop_duplicates("acriss_code")
        .set_index("acriss_code")
    )
    codes = sorted(acriss_meta.index, key=lambda c: _category_sort_key(c, cell_results))

    rows: list[str] = []
    for code in codes:
        meta = acriss_meta.loc[code]
        has_pending = bool(meta["pending_review"])
        badge = "🔍 " if has_pending else ""

        # ── ACRISS band: name only (chip moves to summary row) + dur headers ──
        dur_ths = "".join(f"<td class='dur-hdr'>{d}d</td>" for d in durations)
        rows.append(
            f"<tr class='acriss-hdr' data-code='{code}'>"
            f"<td class='cat-col'>"
            f"<span class='chev-wrap'>{_SVG_CHEVRON}</span>"
            f"{badge}{_html.escape(_code_label(code))}</td>"
            f"{dur_ths}</tr>"
        )

        # ── Recommended row ── badges (union of all durations) in left cell; inline values
        _cov = _clamp = _stale = _deg = _inf = False
        for _dur in durations:
            _cr = cell_results.get((code, _dur))
            if _cr is None:
                continue
            if _cr.flags.coverage < _cr.flags.total_providers:
                _cov = True
            if _cr.flags.clamped:
                _clamp = True
            if _cr.flags.stale:
                _stale = True
            if _cr.flags.degraded:
                _deg = True
            if _cr.flags.inferred:
                _inf = True
        _cat_badges = "".join([
            "<span class='b cov' title='cobertura parcial'></span>" if _cov else "",
            "<span class='b clamp' title='suelo/techo'></span>" if _clamp else "",
            "<span class='b stale' title='stale'></span>" if _stale else "",
            "<span class='b anom' title='anomalía descartada'></span>" if _deg else "",
            "<span class='b inf' title='inferido'></span>" if _inf else "",
        ])

        # Left cell: chip + badges in .rowbadges, catalog examples below
        exs = examples.get(code, [])
        exs_html = (
            f"<div class='catexamples'>{_html.escape(', '.join(exs[:4]))} o similar</div>"
            if exs else ""
        )
        rowbadges = (
            f"<div class='rowbadges'>"
            f"<span class='acode'>{_html.escape(code)}</span>"
            f"{_cat_badges}"
            f"</div>"
        )
        rec_cells = [f"<td class='cat close'>{rowbadges}{exs_html}</td>"]
        for dur in durations:
            cr = cell_results.get((code, dur))
            if cr is None or cr.rec_total is None:
                rec_cells.append("<td class='cell close empty'>—</td>")
                continue

            if cr.present:
                totals = [p.total for p in cr.present]
                lo, hi = min(totals), max(totals)
                rng = (
                    _fmt_num(float(lo), 0) + " €"
                    if round(float(lo)) == round(float(hi))
                    else f"{_fmt_num(float(lo), 0)}–{_fmt_num(float(hi), 0)} €"
                )
            else:
                rng = "—"

            rec_cells.append(
                f"<td class='cell close'>"
                f"<div class='rec'>{_fmt(cr.rec_total)}</div>"
                f"<div class='sub-line'>{_fmt_day(cr.rec_per_day)} · {rng}</div>"
                f"</td>"
            )
        rows.append(f"<tr class='close-row'>{''.join(rec_cells)}</tr>")

        # ── Provider subrows ──
        code_df = zone_df[zone_df["acriss_code"] == code]

        # Which provider has is_base=True per duration — single source of truth from CellResult.
        # For min/max, compute_cell sets is_base on the winning ProviderContribution.
        # For avg/med, no provider has is_base=True → no highlight.
        base_providers_per_dur: dict[int, set[str]] = {}
        present_providers_per_dur: dict[int, set[str]] = {}
        for dur in durations:
            cr = cell_results.get((code, dur))
            if cr:
                base_providers_per_dur[dur] = {pc.provider_code for pc in cr.present if pc.is_base}
                present_providers_per_dur[dur] = {pc.provider_code for pc in cr.present}
            else:
                base_providers_per_dur[dur] = set()
                present_providers_per_dur[dur] = set()

        # Stable sort: by provider position in `providers`, then by model name alphabetically.
        # Never sort by price — price varies per column and stable identity matters.
        prov_order = {p: i for i, p in enumerate(providers)}
        groups = (
            code_df[["provider_code", "external_code", "example_models", "transmission", "acriss_transmission"]]
            .drop_duplicates(["provider_code", "external_code"])
            .copy()
        )
        groups["_pi"] = groups["provider_code"].map(lambda p: prov_order.get(p, 999))
        groups["_mi"] = groups["example_models"].fillna("").str.lower()
        groups = groups.sort_values(["_pi", "_mi"]).drop(columns=["_pi", "_mi"])

        for _, grp in groups.iterrows():
            pcode = str(grp["provider_code"])
            ext_code = grp["external_code"]
            model_name = str(grp["example_models"]) if pd.notna(grp["example_models"]) else ""

            tx: str | None = None
            if pd.notna(grp.get("transmission")) and grp["transmission"]:
                tx = "Manual" if str(grp["transmission"]).lower().startswith("m") else "Auto"
            elif pd.notna(grp.get("acriss_transmission")) and grp["acriss_transmission"]:
                at = str(grp["acriss_transmission"]).upper()
                tx = "Manual" if at == "M" else ("Auto" if at in ("A", "B", "D") else None)
            tx_html = f" <span class='tx'>({tx})</span>" if tx else ""

            # stale/inferred tags come from CellResult, not from zone_df raw data
            is_stale = False
            is_inferred = False
            for dur in durations:
                cr = cell_results.get((code, dur))
                if cr is None:
                    continue
                for pc in cr.present:
                    if pc.provider_code == pcode:
                        is_stale = is_stale or pc.stale
                        is_inferred = is_inferred or pc.inferred
                        break

            tags_html = "".join([
                "<span class='rtag stale'>stale</span>" if is_stale else "",
                "<span class='rtag inf'>inferido</span>" if is_inferred else "",
            ])
            color = _prov_color(pcode, providers)
            left_cell = (
                f"<td class='cat prov'>"
                f"<span class='pdot' style='background:{color}'></span>"
                f"<span class='pname'>{_html.escape(pcode.capitalize())}</span>"
                f"<span class='pmodel'>{_html.escape(model_name.strip())}{tx_html}</span>"
                f"{tags_html}"
                f"</td>"
            )

            dur_tds = []
            for dur in durations:
                row_data = code_df[
                    (code_df["provider_code"] == pcode) &
                    (code_df["external_code"] == ext_code) &
                    (code_df["duration_days"] == dur)
                ]
                if row_data.empty:
                    dur_tds.append("<td class='cell sub empty'>—</td>")
                    continue

                total = float(row_data["total_price"].iloc[0])
                per_day = total / dur if dur else total

                # Highlight logic — derived entirely from CellResult:
                # (a) this provider is marked is_base for this duration, and
                # (b) this specific group is the one collapse_intra_provider picked
                #     (i.e. its total == min of all groups for this provider × duration).
                is_base_row = False
                if pcode in base_providers_per_dur.get(dur, set()):
                    all_prov_totals = code_df[
                        (code_df["provider_code"] == pcode) &
                        (code_df["duration_days"] == dur)
                    ]["total_price"]
                    if not all_prov_totals.empty:
                        is_base_row = abs(total - float(all_prov_totals.min())) < 0.001

                if is_base_row:
                    hit_style = (
                        f"box-shadow:inset 3px 0 0 {color};"
                        f"background:{_hex_rgba(color, 0.07)}"
                    )
                    dur_tds.append(
                        f"<td class='cell sub base-hit' style='{hit_style}'>"
                        f"<div class='rec'>{_fmt(Decimal(str(total)))}</div>"
                        f"<div class='day'>{_fmt_day(Decimal(str(round(per_day, 2))))}</div>"
                        f"</td>"
                    )
                else:
                    dur_tds.append(
                        f"<td class='cell sub'>"
                        f"<div class='rec'>{_fmt(Decimal(str(total)))}</div>"
                        f"<div class='day'>{_fmt_day(Decimal(str(round(per_day, 2))))}</div>"
                        f"</td>"
                    )

            rows.append(
                f"<tr class='prov-row' data-cat='{code}' style='display:none'>"
                f"{left_cell}{''.join(dur_tds)}</tr>"
            )

    toggle_btn = (
        "<div style='text-align:right;margin-bottom:6px'>"
        "<button id='ct-toggle-all'"
        " style='background:none;border:1px solid #d5e0fb;border-radius:5px;"
        "padding:3px 12px;font-size:0.80em;color:#3949ab;cursor:pointer;"
        "font-family:inherit;line-height:1.6'>"
        "&#9658; Expandir todo"
        "</button></div>"
    )
    return toggle_btn + f"<table class='ct-table'><tbody>{''.join(rows)}</tbody></table>"


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
            # Do NOT reset ct_zone_idx: clamp (max/min below) handles out-of-bounds.

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

    col_nav, nav_label = st.columns([0.10, 0.90], gap="small")
    with col_nav:
        nav_prev, nav_next = st.columns(2, gap="small")
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
        st.markdown(f"#### {start_s} – {end_s} &nbsp;·&nbsp; zona {idx + 1} / {n_zones}")
    
    master_rep_dates = sorted(
        df[
            (df["provider_code"] == master) &
            (df["start_date"] == z["start_date"]) &
            (df["end_date"] == z["end_date"])
        ]["representative_date"].unique()
    )
    master_rep_date = pd.Timestamp(master_rep_dates[0]).date() if master_rep_dates else z["start_date"]
    zone_df = _filter_zone(df, master, z["start_date"], z["end_date"], master_rep_date)
    rep_dates = sorted(zone_df[zone_df["provider_code"] == master]["representative_date"].unique())

    rep_label = (
        pd.Timestamp(rep_dates[0]).strftime("%d %b %Y") if len(rep_dates) == 1
        else f"{pd.Timestamp(rep_dates[0]).strftime('%d %b')} – {pd.Timestamp(rep_dates[-1]).strftime('%d %b %Y')}"
        if rep_dates else "—"
    )
    n_categories = zone_df["acriss_code"].nunique()
    prov_summary = ", ".join(
        f"{p} ({len(zone_df[zone_df['provider_code'] == p][['acriss_code', 'example_models']].drop_duplicates())} grupos)"
        for p in providers
    )
    st.caption(
        f"Mostrando **{n_categories} categorías** · Maestro: **{master}** · {prov_summary} · "
        f"fecha repr.: {rep_label} · precios en total (€) + €/día derivado"
    )

    # ── Compute grid ─────────────────────────────────────────────────────────
    cell_results = _build_cell_results(
        zone_df=zone_df,
        providers=providers,
        master=master,
        master_rep_date=master_rep_date,
        base=base,
        global_rule=global_rule,
        category_rules=cat_rules,
        round_mode=round_mode,
    )

    # ── Fetch catalog examples (read-only, 1 query per render) ───────────────
    _eng = super_engine()
    with super_session(_eng) as _sess:
        _rows = _sess.execute(
            text("SELECT code, examples FROM acriss_codes WHERE active = TRUE")
        ).fetchall()
    acriss_examples: dict[str, list[str]] = {
        r.code: list(r.examples or [])[:4] for r in _rows
    }

    # ── Render grid ──────────────────────────────────────────────────────────
    grid_html = _build_grid_html(
        cell_results=cell_results,
        zone_df=zone_df,
        durations=list(DURATION_BRACKET),
        providers=providers,
        examples=acriss_examples,
    )
    # Collapsed height: acriss-hdr (~38px) + close-row (~70px) per category.
    # The script sends setFrameHeight after load and after every toggle.
    n_cats = zone_df["acriss_code"].nunique()
    est_height = n_cats * 110 + 40
    _components.html(
        _GRID_FONTS + _GRID_BODY_CSS + _CSS + grid_html + _GRID_SCRIPT,
        height=est_height,
        scrolling=False,
    )
    st.markdown(_CSS, unsafe_allow_html=True)  # legend badges (.b.*) outside iframe

    # ── Legend ───────────────────────────────────────────────────────────────
    st.markdown(
        "<div style='display:flex;gap:18px;flex-wrap:wrap;font-size:0.78em;color:#5a6577;margin:10px 2px 0'>"
        "<span style='display:inline-flex;gap:6px;align-items:center'><span class='b cov'></span> cobertura parcial</span>"
        "<span style='display:inline-flex;gap:6px;align-items:center'><span class='b clamp'></span> suelo/techo</span>"
        "<span style='display:inline-flex;gap:6px;align-items:center'><span class='b stale'></span> stale</span>"
        "<span style='display:inline-flex;gap:6px;align-items:center'><span class='b anom'></span> anomalía descartada</span>"
        "<span style='display:inline-flex;gap:6px;align-items:center'><span class='b inf'></span> precio inferido de zona</span>"
        "</div>",
        unsafe_allow_html=True,
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
