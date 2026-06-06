import { useState } from 'react'
import type { Rule } from '../types'
import { BASE_LABELS } from '../lib/format'
import RuleModal from './RuleModal'

interface Props {
  rule: Rule
  base: string
  round: string
  open: boolean
  onToggle: () => void
  onChange: (patch: Partial<Rule>) => void
  // Per-category overrides.
  categoryRules: Record<string, Rule>
  categories: { code: string; label: string }[]   // all categories in the grid
  onAddCategory: (code: string, rule: Rule) => void
  onChangeCategory: (code: string, patch: Partial<Rule>) => void
  onDeleteCategory: (code: string) => void
}

const FLOORS = [['auto', 'auto (histórico)'], ['cost', 'coste + margen'], ['none', 'sin suelo']]
const CEILINGS = [['max', 'máx de mercado'], ['none', 'sin techo']]

function roundPhrase(round: string): string {
  if (round === '0') return ''
  const txt = round === '1' ? 'entero' : ',' + round.split('.')[1]
  return ` Redondeo a ${txt}.`
}

/** The op/val/mode/floor/ceiling editor, shared by the default and per-category rows. */
function RuleControls({ rule, onChange }: { rule: Rule; onChange: (p: Partial<Rule>) => void }) {
  return (
    <div className="rule-controls">
      <div className="seg sm">
        <button className={rule.op === 'sub' ? 'on' : ''} onClick={() => onChange({ op: 'sub' })}>−</button>
        <button className={rule.op === 'add' ? 'on' : ''} onClick={() => onChange({ op: 'add' })}>+</button>
      </div>
      <input
        className="mini val" type="number" step="0.5"
        value={rule.val} onChange={(e) => onChange({ val: parseFloat(e.target.value) || 0 })}
      />
      <div className="seg sm">
        <button className={rule.mode === 'pct' ? 'on' : ''} onClick={() => onChange({ mode: 'pct' })}>%</button>
        <button className={rule.mode === 'abs' ? 'on' : ''} onClick={() => onChange({ mode: 'abs' })}>€</button>
      </div>
      <span style={{ color: 'var(--ink-faint)' }}>· suelo</span>
      <select className="mini" style={{ width: 130 }} value={rule.floor} onChange={(e) => onChange({ floor: e.target.value })}>
        {FLOORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <span style={{ color: 'var(--ink-faint)' }}>techo</span>
      <select className="mini" style={{ width: 120 }} value={rule.ceiling} onChange={(e) => onChange({ ceiling: e.target.value })}>
        {CEILINGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  )
}

export default function RuleBar({
  rule, base, round, open, onToggle, onChange,
  categoryRules, categories, onAddCategory, onChangeCategory, onDeleteCategory,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false)
  const labelOf = (code: string) => categories.find((c) => c.code === code)?.label ?? code
  const available = categories.filter((c) => !(c.code in categoryRules))

  const opTxtSummary =
    rule.val === 0 ? 'sin ajuste' : `${rule.op === 'sub' ? '−' : '+'}${rule.val}${rule.mode === 'pct' ? '%' : '€'}`
  const nCat = Object.keys(categoryRules).length
  const note =
    rule.val === 0
      ? `Igualar a la base, sin ajuste.${roundPhrase(round)}`
      : `${rule.op === 'sub' ? 'Restar' : 'Sumar'} ${rule.mode === 'pct' ? `${rule.val}%` : `${rule.val} €`} a la base.${roundPhrase(round)}`

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
          Base <b>{BASE_LABELS[base] ?? base}</b> · regla global <b>{opTxtSummary}</b>
          {nCat > 0 && <> · <b>{nCat}</b> por categoría</>}
        </div>
      </div>
      <div className={`rule-body${open ? ' open' : ''}`}>
        {/* Default / global rule */}
        <div className="rule-row">
          <div className="grp">
            Por defecto <span className="tag-default">global</span>
            <small>categorías sin regla propia</small>
          </div>
          <RuleControls rule={rule} onChange={onChange} />
          <div className="rule-note">{note}</div>
        </div>

        {/* Per-category overrides */}
        {Object.entries(categoryRules).map(([code, r]) => (
          <div className="rule-row" key={code}>
            <div className="grp">
              <span style={{ fontFamily: 'var(--mono-num)' }}>{code}</span>
              <small>{labelOf(code)}</small>
            </div>
            <RuleControls rule={r} onChange={(p) => onChangeCategory(code, p)} />
            <button className="rule-del" title="Eliminar regla" onClick={() => onDeleteCategory(code)}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
              </svg>
            </button>
          </div>
        ))}

        <div className="rule-add">
          {available.length > 0 ? (
            <button className="addbtn" onClick={() => setModalOpen(true)}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Añadir regla por categoría
            </button>
          ) : (
            <span className="none-left">Todas las categorías tienen regla propia.</span>
          )}
        </div>
      </div>

      <RuleModal
        open={modalOpen}
        categories={available}
        onClose={() => setModalOpen(false)}
        onAdd={onAddCategory}
      />
    </div>
  )
}
