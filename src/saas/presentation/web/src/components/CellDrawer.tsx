import type { CrossTariffResponse, ProviderRow } from '../types'
import { eur, eurDay, marketRange, fmtDateShort, zoneRangeShort, BASE_LABELS_NOUN } from '../lib/format'

interface Props {
  data: CrossTariffResponse
  selection: { code: string; duration: number } | null
  onClose: () => void
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** "Precio del 23 jun · zona 2–29 jun" — provenance of a provider's derived price. */
function provenance(pr: ProviderRow): string {
  if (!pr.observation_date) return ''
  const zone = pr.zone_range
    ? ` · zona ${zoneRangeShort(pr.zone_range.date_from, pr.zone_range.date_to)}`
    : ''
  return `Precio del ${fmtDateShort(pr.observation_date)}${zone}`
}

export default function CellDrawer({ data, selection, onClose }: Props) {
  const open = selection != null
  const cat = selection ? data.categories.find((c) => c.acriss_code === selection.code) : undefined
  const dur = selection?.duration ?? 0
  const summary = cat?.cells.find((c) => c.duration === dur)
  const masterName = data.meta.providers.find((p) => p.is_master)?.name ?? '—'
  const colorByKey: Record<string, string> = {}
  for (const p of data.meta.providers) colorByKey[p.key] = p.color

  // Per-provider contributions for this duration.
  const lines = (cat?.providers ?? []).map((pr) => {
    const c = pr.cells.find((x) => x.duration === dur)
    return { pr, c }
  })
  const presentCount = lines.filter((l) => l.c && !l.c.missing && !l.c.anomaly).length
  const totalProviders = data.meta.providers.length
  const degraded = lines.some((l) => l.c?.anomaly)

  // Effective rule for this category (own override or the global default).
  const rule = cat ? (data.meta.rules[cat.acriss_code] ?? data.meta.rules._default) : undefined
  const usedDefault = cat ? !(cat.acriss_code in data.meta.rules) : true
  const opTxt = rule ? `${rule.op === 'sub' ? '−' : '+'} ${rule.val}${rule.mode === 'pct' ? '%' : ' €'}` : ''

  return (
    <>
      <div className={`scrim${open ? ' on' : ''}`} onClick={onClose} />
      <div className={`drawer${open ? ' on' : ''}`}>
        {cat && summary && (
          <>
            <div className="drawer-head">
              <span className="close" onClick={onClose}>×</span>
              <div className="crumb">{cat.acriss_code} · {dur} día{dur > 1 ? 's' : ''}</div>
              <h3>{cat.view_label}</h3>
              <div style={{ color: 'var(--ink-soft)', fontSize: 13 }}>
                zona de {masterName} · base {BASE_LABELS_NOUN[data.meta.base] ?? data.meta.base} de mercado
              </div>
            </div>
            <div className="drawer-body">
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.07em', fontWeight: 600, marginBottom: 6 }}>
                Proveedores · total (€/día)
              </div>

              {lines.map(({ pr, c }, i) => {
                const color = colorByKey[pr.provider_key] ?? '#97a0b0'
                if (!c || c.missing) {
                  return (
                    <div className="prov-line dropped" key={i}>
                      <span className="who">
                        <span className="dot" style={{ background: color }} />
                        <span>{capitalize(pr.provider_key)}<small>sin oferta</small></span>
                      </span>
                      <span className="pr">—</span>
                    </div>
                  )
                }
                if (c.anomaly) {
                  return (
                    <div className="prov-line dropped" key={i}>
                      <span className="who">
                        <span className="dot" style={{ background: color }} />
                        <span>
                          {capitalize(pr.provider_key)}<small>anomalía (descartado)</small>
                          {provenance(pr) && <small>{provenance(pr)}</small>}
                        </span>
                      </span>
                      <span className="pr">{eur(c.total)}</span>
                    </div>
                  )
                }
                return (
                  <div className={`prov-line${c.is_base ? ' win' : ''}`} key={i}>
                    <span className="who">
                      <span className="dot" style={{ background: color }} />
                      <span>
                        {capitalize(pr.provider_key)}
                        {pr.models ? <small>{pr.models}</small> : null}
                        {provenance(pr) && <small>{provenance(pr)}</small>}
                      </span>
                    </span>
                    <span className="pr">
                      {eur(c.total)}
                      <small style={{ display: 'block', color: 'var(--ink-faint)', fontWeight: 400, textAlign: 'right' }}>
                        {eurDay(c.per_day)}
                      </small>
                    </span>
                  </div>
                )
              })}

              <div className="calc">
                <div className="step">
                  <span className="k">Rango de mercado</span>
                  <span className="v">{marketRange(summary.market_min, summary.market_max)}</span>
                </div>
                <div className="step">
                  <span className="k">Base ({BASE_LABELS_NOUN[data.meta.base] ?? data.meta.base})</span>
                  <span className="v">{eur(summary.market_base)}</span>
                </div>
                <div className="step">
                  <span className="k">
                    Regla {opTxt} ·{' '}
                    <span style={{ color: 'var(--blue)' }}>{usedDefault ? 'global' : 'propia'}</span>
                  </span>
                  <span className="v">{eur(summary.after_rule)}</span>
                </div>
                {summary.clamped ? (
                  <div className="step">
                    <span className="k">Ajuste por suelo/techo ({eur(summary.clamp_bound)})</span>
                    <span className="v" style={{ color: 'var(--warn)' }}>{eur(summary.clamp_bound)}</span>
                  </div>
                ) : (
                  <div className="step muted">
                    <span className="k">Suelo · techo</span>
                    <span className="v">ok</span>
                  </div>
                )}
                <div className="step">
                  <span className="k">
                    Redondeo ({data.meta.round === '0' ? 'no' : data.meta.round})
                    {summary.round_flip && (
                      <span style={{ color: 'var(--warn)' }}> · ajustado para no cruzar la base</span>
                    )}
                  </span>
                  <span className="v">{eur(summary.rounded)}</span>
                </div>
                <div className="step total">
                  <span className="k">Total recomendado</span>
                  <span className="v">{eur(summary.recommended_total)}</span>
                </div>
                <div className="step muted">
                  <span className="k">equivale a</span>
                  <span className="v">{eurDay(summary.recommended_per_day)}</span>
                </div>
              </div>

              <div className="flagrow">
                <span className="flag cov"><span className="b cov" />cobertura {presentCount} de {totalProviders}</span>
                {summary.clamped && <span className="flag clamp"><span className="b clamp" />clamped</span>}
                {summary.stale && <span className="flag stale"><span className="b stale" />stale</span>}
                {degraded && <span className="flag anom"><span className="b anom" />degraded_inputs</span>}
                {summary.inferred && <span className="flag inf"><span className="b inf" />is_inferred</span>}
              </div>

              <div className="hint">
                Procedencia exacta de esta celda: cada precio de proveedor usado, cuál fijó la base
                (resaltado), el pipeline base → regla → suelo/techo → redondeo, y el estado de
                cobertura. Es lo que se persistirá como <code>inputs_snapshot</code> al guardar.
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
