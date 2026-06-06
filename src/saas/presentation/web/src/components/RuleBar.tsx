import type { Rule } from '../types'
import { BASE_LABELS } from '../lib/format'

interface Props {
  rule: Rule
  base: string
  round: string
  open: boolean
  onToggle: () => void
  onChange: (patch: Partial<Rule>) => void
}

const FLOORS = [
  { v: 'auto', l: 'auto (histórico)' },
  { v: 'cost', l: 'coste + margen' },
  { v: 'none', l: 'sin suelo' },
]
const CEILINGS = [
  { v: 'max', l: 'máx de mercado' },
  { v: 'none', l: 'sin techo' },
]

function roundPhrase(round: string): string {
  if (round === '0') return ''
  const txt = round === '1' ? 'entero' : ',' + round.split('.')[1]
  return ` Redondeo a ${txt}.`
}

export default function RuleBar({ rule, base, round, open, onToggle, onChange }: Props) {
  const opTxtSummary =
    rule.val === 0 ? 'sin ajuste' : `${rule.op === 'sub' ? '−' : '+'}${rule.val}${rule.mode === 'pct' ? '%' : '€'}`
  const v = rule.mode === 'pct' ? `${rule.val}%` : `${rule.val} €`
  const note =
    rule.val === 0
      ? `Igualar a la base, sin ajuste.${roundPhrase(round)}`
      : `${rule.op === 'sub' ? 'Restar' : 'Sumar'} ${v} a la base.${roundPhrase(round)}`

  return (
    <div className={`rulebar${open ? ' open' : ''}`}>
      <div className="rule-head" onClick={onToggle}>
        <div className="title">
          <span className="chev">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </span>{' '}
          Reglas de pricing
        </div>
        <div className="rule-summary">
          Base <b>{BASE_LABELS[base] ?? base}</b> · regla global <b>{opTxtSummary}</b> · suelo <b>{rule.floor}</b>
        </div>
      </div>
      <div className={`rule-body${open ? ' open' : ''}`}>
        <div className="rule-row">
          <div className="grp">
            Por defecto <span className="tag-default">global</span>
            <small>categorías sin regla propia</small>
          </div>
          <div className="rule-controls">
            <div className="seg sm">
              <button className={rule.op === 'sub' ? 'on' : ''} onClick={() => onChange({ op: 'sub' })}>−</button>
              <button className={rule.op === 'add' ? 'on' : ''} onClick={() => onChange({ op: 'add' })}>+</button>
            </div>
            <input
              className="mini val"
              type="number"
              step="0.5"
              value={rule.val}
              onChange={(e) => onChange({ val: parseFloat(e.target.value) || 0 })}
            />
            <div className="seg sm">
              <button className={rule.mode === 'pct' ? 'on' : ''} onClick={() => onChange({ mode: 'pct' })}>%</button>
              <button className={rule.mode === 'abs' ? 'on' : ''} onClick={() => onChange({ mode: 'abs' })}>€</button>
            </div>
            <span style={{ color: 'var(--ink-faint)' }}>· suelo</span>
            <select className="mini" style={{ width: 130 }} value={rule.floor} onChange={(e) => onChange({ floor: e.target.value })}>
              {FLOORS.map((f) => <option key={f.v} value={f.v}>{f.l}</option>)}
            </select>
            <span style={{ color: 'var(--ink-faint)' }}>techo</span>
            <select className="mini" style={{ width: 120 }} value={rule.ceiling} onChange={(e) => onChange({ ceiling: e.target.value })}>
              {CEILINGS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
            </select>
          </div>
          <div className="rule-note">{note}</div>
        </div>
        <div className="rule-add">
          <span className="none-left">
            Las reglas por categoría llegan pronto. Por ahora se aplica la regla global a todas.
          </span>
        </div>
      </div>
    </div>
  )
}
