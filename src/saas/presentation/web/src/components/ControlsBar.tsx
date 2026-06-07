import type { Meta } from '../types'

interface Props {
  meta: Meta
  base: string
  round: string
  master: string | null
  activeProviders: string[]
  locationId: number | null
  onBase: (b: string) => void
  onRound: (r: string) => void
  onMaster: (m: string) => void
  onToggleProvider: (key: string) => void
  onLocation: (id: number) => void
}

const BASES = [
  { v: 'min', l: 'mín' },
  { v: 'med', l: 'med' },
  { v: 'avg', l: 'avg' },
  { v: 'max', l: 'máx' },
]

const ROUND_OPTS = [
  { v: '0', l: 'sin redondeo' },
  { v: '0.99', l: 'a ,99' },
  { v: '0.90', l: 'a ,90' },
  { v: '0.50', l: 'a ,50' },
  { v: '1', l: 'a entero' },
]

export default function ControlsBar({
  meta, base, round, master, activeProviders, locationId,
  onBase, onRound, onMaster, onToggleProvider, onLocation,
}: Props) {
  const activeSet = new Set(activeProviders)
  return (
    <div className="controls">
      <div>
        <div className="ctl-label">Ubicación</div>
        <div className="field">
          <select
            className="mini"
            value={locationId ?? ''}
            onChange={(e) => onLocation(Number(e.target.value))}
            disabled={meta.locations.length === 0}
          >
            {meta.locations.length === 0 && <option value="">Sin ubicaciones</option>}
            {meta.locations.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <div className="ctl-label">En el radar · {activeProviders.length}/{meta.providers.length}</div>
        <div className="field">
          <div className="chips">
            {meta.providers.map((p) => {
              const on = activeSet.has(p.key)
              const isMaster = p.key === master
              return (
                <button
                  type="button"
                  className={`chip${on ? ' on' : ' off'}${isMaster ? ' master' : ''}`}
                  key={p.key}
                  disabled={isMaster}
                  aria-pressed={on}
                  title={isMaster ? 'Calendario maestro — no se puede quitar' : undefined}
                  onClick={() => !isMaster && onToggleProvider(p.key)}
                >
                  <span className="dot" style={{ background: p.color }} />
                  {p.name}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      <div>
        <div className="ctl-label">Base de precio</div>
        <div className="field">
          <div className="seg">
            {BASES.map((b) => (
              <button
                key={b.v}
                className={base === b.v ? 'on' : ''}
                onClick={() => onBase(b.v)}
              >
                {b.l}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div>
        <div className="ctl-label">Calendario maestro</div>
        <div className="field">
          <select className="mini" value={master ?? ''} onChange={(e) => onMaster(e.target.value)}>
            {meta.providers.filter((p) => activeSet.has(p.key)).map((p) => (
              <option key={p.key} value={p.key}>{p.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <div className="ctl-label">Redondeo</div>
        <div className="field">
          <select className="mini" value={round} onChange={(e) => onRound(e.target.value)}>
            {ROUND_OPTS.map((o) => (
              <option key={o.v} value={o.v}>{o.l}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}
