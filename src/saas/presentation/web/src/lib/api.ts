import type { Controls, CrossTariffResponse } from '../types'
import { UnauthorizedError } from './auth'

/** Build the query string for /api/cross-tariff from the (optional) controls. */
function buildQuery(controls: Controls | null): string {
  if (!controls) return ''
  const p = new URLSearchParams()
  p.set('base', controls.base)
  p.set('round', controls.round)
  if (controls.master) p.set('master', controls.master)
  if (controls.location_id != null) p.set('location_id', String(controls.location_id))
  if (controls.zone != null) p.set('zone', String(controls.zone))
  p.set('rule_op', controls.rule_op)
  p.set('rule_val', String(controls.rule_val))
  p.set('rule_mode', controls.rule_mode)
  p.set('rule_floor', controls.rule_floor)
  p.set('rule_ceiling', controls.rule_ceiling)
  return `?${p.toString()}`
}

export async function fetchCrossTariff(
  controls: Controls | null,
): Promise<CrossTariffResponse> {
  const res = await fetch(`/api/cross-tariff${buildQuery(controls)}`)
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body — keep the status code */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<CrossTariffResponse>
}
