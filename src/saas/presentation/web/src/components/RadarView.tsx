import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { fetchCrossTariff } from '../lib/api'
import type { Controls, Meta, Rule } from '../types'
import Topbar from './Topbar'
import ControlsBar from './ControlsBar'
import RuleBar from './RuleBar'
import ZoneNav from './ZoneNav'
import PriceGrid from './PriceGrid'
import CellDrawer from './CellDrawer'
import Legend from './Legend'
import Modal from './Modal'

function controlsFromMeta(meta: Meta): Controls {
  const master = meta.providers.find((p) => p.is_master)?.key ?? null
  const r = meta.rules._default
  return {
    base: meta.base,
    round: meta.round,
    master,
    location_id: meta.location?.id ?? null,
    zone: meta.zone.index,
    rule_op: r.op,
    rule_val: r.val,
    rule_mode: r.mode,
    rule_floor: r.floor,
    rule_ceiling: r.ceiling,
  }
}

interface Props {
  onMeta: (meta: Meta) => void
  email: string
  onLogout: () => void
}

export default function RadarView({ onMeta, email, onLogout }: Props) {
  const [controls, setControls] = useState<Controls | null>(null)
  const [openCats, setOpenCats] = useState<Set<string>>(new Set())
  const [drawer, setDrawer] = useState<{ code: string; duration: number } | null>(null)
  const [ruleOpen, setRuleOpen] = useState(true)
  // Settings/rules live in a modal — closed by default, grid gets full width.
  const [settingsOpen, setSettingsOpen] = useState(false)

  const query = useQuery({
    queryKey: ['cross-tariff', controls],
    queryFn: () => fetchCrossTariff(controls),
    placeholderData: keepPreviousData,
  })

  // Seed local controls from the first successful response; lift meta to the shell.
  useEffect(() => {
    if (query.data) {
      onMeta(query.data.meta)
      if (controls === null) setControls(controlsFromMeta(query.data.meta))
    }
  }, [query.data, controls, onMeta])

  const patch = (p: Partial<Controls>) => setControls((c) => (c ? { ...c, ...p } : c))
  const patchRule = (r: Partial<Rule>) =>
    patch({
      ...(r.op !== undefined ? { rule_op: r.op } : {}),
      ...(r.val !== undefined ? { rule_val: r.val } : {}),
      ...(r.mode !== undefined ? { rule_mode: r.mode } : {}),
      ...(r.floor !== undefined ? { rule_floor: r.floor } : {}),
      ...(r.ceiling !== undefined ? { rule_ceiling: r.ceiling } : {}),
    })

  const toggleCat = (code: string) =>
    setOpenCats((s) => {
      const n = new Set(s)
      n.has(code) ? n.delete(code) : n.add(code)
      return n
    })

  // ── Loading (first load) ──
  if (query.isLoading && !query.data) {
    return (
      <div className="wrap">
        <div className="skel skel-row" style={{ height: 72 }} />
        <div className="skel skel-row" style={{ height: 40, width: '40%' }} />
        {Array.from({ length: 8 }).map((_, i) => (
          <div className="skel skel-row" key={i} />
        ))}
      </div>
    )
  }

  // ── Error ──
  if (query.isError && !query.data) {
    return (
      <div className="wrap">
        <div className="error-box">
          <div>
            <strong>No se pudieron cargar los precios.</strong>
            <div style={{ fontSize: 12.5, marginTop: 4 }}>{(query.error as Error).message}</div>
          </div>
          <button className="btn" onClick={() => query.refetch()}>Reintentar</button>
        </div>
      </div>
    )
  }

  const data = query.data!
  const ctl = controls ?? controlsFromMeta(data.meta)

  return (
    <>
      <Topbar
        providerCount={data.meta.providers.length}
        dataUpdatedAt={data.meta.data_updated_at}
        email={email}
        onLogout={onLogout}
      />
      <div className="wrap">
        <ZoneNav
          meta={data.meta}
          onPrev={() => patch({ zone: Math.max(0, (ctl.zone ?? data.meta.zone.index) - 1) })}
          onNext={() => patch({ zone: (ctl.zone ?? data.meta.zone.index) + 1 })}
          onOpenSettings={() => setSettingsOpen(true)}
          action={
            <button className="settings-btn" onClick={() => setSettingsOpen(true)}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" /><line x1="4" y1="18" x2="20" y2="18" />
                <circle cx="9" cy="6" r="2" fill="#fff" /><circle cx="15" cy="12" r="2" fill="#fff" /><circle cx="8" cy="18" r="2" fill="#fff" />
              </svg>
              Ajustes y reglas
            </button>
          }
        />
        <PriceGrid
          data={data}
          openCats={openCats}
          onToggleCat={toggleCat}
          onCellClick={(code, duration) => setDrawer({ code, duration })}
        />
        <Legend />
        <div className="footer-actions">
          <div className="export-note">
            Esto es un <b>preview en vivo</b>. Al guardar, se persiste como regla versionada; el
            motor recalcula con cada scrape y la API sirve por código ACRISS. La aplicación al
            sistema del cliente la decide su cron — nada se publica en silencio.
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn">Exportar CSV</button>
            <button className="btn primary">Guardar configuración de precios</button>
          </div>
        </div>
      </div>

      <Modal open={settingsOpen} title="Ajustes y reglas" onClose={() => setSettingsOpen(false)}>
        <ControlsBar
          meta={data.meta}
          base={ctl.base}
          round={ctl.round}
          master={ctl.master}
          locationId={ctl.location_id}
          onBase={(base) => patch({ base })}
          onRound={(round) => patch({ round })}
          onMaster={(master) => patch({ master, zone: 0 })}
          onLocation={(location_id) => patch({ location_id, zone: 0 })}
        />
        <RuleBar
          rule={{
            op: ctl.rule_op, val: ctl.rule_val, mode: ctl.rule_mode,
            floor: ctl.rule_floor, ceiling: ctl.rule_ceiling,
          }}
          base={ctl.base}
          round={ctl.round}
          open={ruleOpen}
          onToggle={() => setRuleOpen((o) => !o)}
          onChange={patchRule}
        />
      </Modal>
      <CellDrawer data={data} selection={drawer} onClose={() => setDrawer(null)} />
    </>
  )
}
