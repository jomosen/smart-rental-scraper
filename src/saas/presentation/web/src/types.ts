// TypeScript mirror of the GET /api/cross-tariff JSON contract.
// All monetary values are decimal strings (the front formats them).

export interface ProviderMeta {
  key: string
  name: string
  color: string
  is_master: boolean
}

export interface ZoneMeta {
  index: number
  total: number
  date_from: string | null
  date_to: string | null
}

export interface Rule {
  op: string // "sub" | "add"
  val: number
  mode: string // "pct" | "abs"
  floor: string // "auto" | "cost" | "none"
  ceiling: string // "max" | "none"
}

export interface Rules {
  _default: Rule
  [code: string]: Rule
}

export interface LocationOption {
  id: number
  code: string
  name: string
}

export interface Meta {
  tenant_name: string
  plan: string | null
  location: LocationOption | null
  locations: LocationOption[]
  zone: ZoneMeta
  master_rep_date: string | null
  durations: number[]
  providers: ProviderMeta[]
  data_updated_at: string | null
  base: string // effective base aggregation
  round: string // effective rounding mode
  rules: Rules
}

export interface SummaryCell {
  duration: number
  recommended_total: string | null
  recommended_per_day: string | null
  market_min: string | null
  market_max: string | null
  empty: boolean
  // Calculation pipeline (for the drawer) + per-cell flags.
  market_base: string | null
  after_rule: string | null
  clamped: boolean
  clamp_bound: string | null
  rounded: string | null
  round_flip: boolean
  stale: boolean
  inferred: boolean
  partial: boolean
}

export interface ProviderCell {
  duration: number
  total: string | null
  per_day: string | null
  is_base: boolean
  anomaly: boolean
  missing: boolean
}

export interface ProviderRow {
  provider_key: string
  models: string
  transmission: string | null
  inferred: boolean
  observation_date: string | null
  zone_range: { date_from: string; date_to: string } | null
  cells: ProviderCell[]
}

export interface CategoryFlags {
  partial_coverage: boolean
  clamped: boolean
  stale: boolean
  anomaly: boolean
  inferred: boolean
}

export interface Category {
  acriss_code: string
  view_label: string
  catalog_examples: string
  flags: CategoryFlags
  cells: SummaryCell[]
  providers: ProviderRow[]
}

export interface CrossTariffResponse {
  meta: Meta
  categories: Category[]
}

// Ephemeral control state — drives the query string (overrides server defaults).
export interface Controls {
  base: string
  round: string
  master: string | null
  location_id: number | null
  zone: number | null
  rule_op: string
  rule_val: number
  rule_mode: string
  rule_floor: string
  rule_ceiling: string
  category_rules: Record<string, Rule>
}

// Body shape for POST /preview and PUT /pricing-config.
export interface PricingConfig {
  base: string
  round: string
  master_provider_key: string | null
  default_rule: Rule
  category_rules: Record<string, Rule>
  zone?: number | null
  location_id?: number | null
}
