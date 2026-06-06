import { useEffect, useState } from 'react'
import type { Rule } from '../types'
import Modal from './Modal'

interface Props {
  open: boolean
  categories: { code: string; label: string }[] // categories without a rule yet
  onClose: () => void
  onAdd: (code: string, rule: Rule) => void
}

const DEFAULT: Rule = { op: 'sub', val: 0, mode: 'pct', floor: 'auto', ceiling: 'max' }
const FLOORS = [['auto', 'auto (histórico)'], ['cost', 'coste + margen'], ['none', 'sin suelo']]
const CEILINGS = [['max', 'máx de mercado'], ['none', 'sin techo']]

export default function RuleModal({ open, categories, onClose, onAdd }: Props) {
  const [code, setCode] = useState('')
  const [rule, setRule] = useState<Rule>(DEFAULT)

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (open) {
      setCode(categories[0]?.code ?? '')
      setRule(DEFAULT)
    }
  }, [open, categories])

  const set = (p: Partial<Rule>) => setRule((r) => ({ ...r, ...p }))

  return (
    <Modal open={open} title="Añadir regla por categoría" onClose={onClose}>
      {categories.length === 0 ? (
        <p style={{ color: 'var(--ink-faint)' }}>
          Todas las categorías visibles ya tienen regla propia.
        </p>
      ) : (
        <>
          <div className="mfield">
            <label>Categoría ACRISS</label>
            <select className="mini" value={code} onChange={(e) => setCode(e.target.value)}>
              {categories.map((c) => (
                <option key={c.code} value={c.code}>{c.code} — {c.label}</option>
              ))}
            </select>
          </div>

          <div className="mfield">
            <label>Ajuste sobre la base</label>
            <div className="inline" style={{ gap: 8, flexWrap: 'wrap' }}>
              <div className="seg sm">
                <button className={rule.op === 'sub' ? 'on' : ''} onClick={() => set({ op: 'sub' })}>−</button>
                <button className={rule.op === 'add' ? 'on' : ''} onClick={() => set({ op: 'add' })}>+</button>
              </div>
              <input
                className="mini val" type="number" step="0.5" style={{ width: 74, textAlign: 'right' }}
                value={rule.val} onChange={(e) => set({ val: parseFloat(e.target.value) || 0 })}
              />
              <div className="seg sm">
                <button className={rule.mode === 'pct' ? 'on' : ''} onClick={() => set({ mode: 'pct' })}>%</button>
                <button className={rule.mode === 'abs' ? 'on' : ''} onClick={() => set({ mode: 'abs' })}>€</button>
              </div>
            </div>
          </div>

          <div className="mfield-row">
            <div className="mfield">
              <label>Suelo</label>
              <select className="mini" value={rule.floor} onChange={(e) => set({ floor: e.target.value })}>
                {FLOORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="mfield">
              <label>Techo</label>
              <select className="mini" value={rule.ceiling} onChange={(e) => set({ ceiling: e.target.value })}>
                {CEILINGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
          </div>

          <div className="modal-foot">
            <button className="btn" onClick={onClose}>Cancelar</button>
            <button
              className="btn primary"
              disabled={!code}
              onClick={() => { if (code) { onAdd(code, rule); onClose() } }}
            >
              Añadir regla
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}
