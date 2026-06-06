import type { Controls, CrossTariffResponse, PricingConfig, Rule } from '../types'
import { UnauthorizedError } from './auth'

const CSRF = { 'X-Requested-With': 'fetch', 'Content-Type': 'application/json' }

/** The persisted-config slice of the controls (excludes view nav: zone/location). */
export function controlsToConfig(c: Controls): PricingConfig {
  return {
    base: c.base,
    round: c.round,
    master_provider_key: c.master,
    default_rule: {
      op: c.rule_op, val: c.rule_val, mode: c.rule_mode,
      floor: c.rule_floor, ceiling: c.rule_ceiling,
    },
    category_rules: c.category_rules,
  }
}

/** Stable key over the persisted-config slice, for dirty detection. */
export function configKey(c: Controls): string {
  const cfg = controlsToConfig(c)
  const cats = Object.keys(cfg.category_rules).sort().reduce<Record<string, Rule>>((acc, k) => {
    acc[k] = cfg.category_rules[k]; return acc
  }, {})
  return JSON.stringify({ ...cfg, category_rules: cats })
}

async function parse(res: Response): Promise<CrossTariffResponse> {
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* keep status */ }
    throw new Error(detail)
  }
  return res.json() as Promise<CrossTariffResponse>
}

/** Initial load: render from the tenant's saved config (defaults if none). */
export async function fetchInitial(): Promise<CrossTariffResponse> {
  return parse(await fetch('/api/cross-tariff'))
}

/** Live edit: render from the given controls, without persisting. */
export async function previewCrossTariff(controls: Controls): Promise<CrossTariffResponse> {
  const body: PricingConfig = {
    ...controlsToConfig(controls),
    zone: controls.zone,
    location_id: controls.location_id,
  }
  return parse(await fetch('/api/cross-tariff/preview', {
    method: 'POST', headers: CSRF, body: JSON.stringify(body),
  }))
}

/** Persist the tenant's pricing configuration. */
export async function savePricingConfig(controls: Controls): Promise<{ ok: boolean; version: number }> {
  const res = await fetch('/api/pricing-config', {
    method: 'PUT', headers: CSRF, body: JSON.stringify(controlsToConfig(controls)),
  })
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const b = await res.json()
      if (b?.detail) detail = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
    } catch { /* keep */ }
    throw new Error(detail)
  }
  return res.json()
}
