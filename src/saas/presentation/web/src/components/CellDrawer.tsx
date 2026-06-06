import type { CrossTariffResponse } from '../types'
import { eur, eurDay, marketRange, BASE_LABELS_NOUN } from '../lib/format'

interface Props {
  data: CrossTariffResponse
  selection: { code: string; duration: number } | null
  onClose: () => void
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

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
                        <span>{capitalize(pr.provider_key)}<small>anomalía (descartado)</small></span>
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
                <div className="step muted">
                  <span className="k">Rango de mercado</span>
                  <span className="v">{marketRange(summary.market_min, summary.market_max)}</span>
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
                {cat.flags.clamped && <span className="flag clamp"><span className="b clamp" />clamped</span>}
                {cat.flags.stale && <span className="flag stale"><span className="b stale" />stale</span>}
                {cat.flags.anomaly && <span className="flag anom"><span className="b anom" />degraded_inputs</span>}
                {cat.flags.inferred && <span className="flag inf"><span className="b inf" />is_inferred</span>}
              </div>

              <div className="hint">
                Procedencia de esta celda: cada precio de proveedor usado, cuál fijó la base
                (resaltado), la cobertura y los avisos. El detalle del pipeline de cálculo
                (base → regla → suelo/techo → redondeo) se añadirá al guardar la regla.
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
