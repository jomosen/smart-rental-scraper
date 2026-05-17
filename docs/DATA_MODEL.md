# Data Model — Design Decisions

> Consolidated record of the data model decisions taken during the SaaS design phase.
> This document is the source of truth for the database schema. The actual DDL is generated
> from these decisions during implementation; do not change the schema without updating
> this document first.
>
> Companion documents:
> - `ROADMAP_ARCHITECTURE.md` — overall SaaS evolution roadmap.
> - `SCRAPING_OPTIMIZATIONS.md` — deferred scraping optimizations.

---

## Reading guide

The document is organized in three layers:

1. **Decisions** — the 10 modeling decisions, each with its rationale condensed. Read these first.
2. **Schema** — the resulting tables (pseudo-DDL). Derived from the decisions.
3. **Anatomy of the main query** — how the model answers the canonical client question. Use this as a sanity check when designing indexes or modifying the model.

Deliberately deferred items appear at the end with their re-evaluation triggers.

---

## Part 1 — Decisions

### 1. Vehicle classification (three-layer model)

The product organizes vehicles in three layers, each with a distinct role:

- `acriss_codes` — **the ACRISS standard**, materialized as the operator-curated subset of codes that appear in this market. Each entry is a 4-character code (Category + Body Type + Transmission + Fuel). This is the **product's lingua franca**: the industry-standard language in which the market is presented to all tenants. Maintained by the operator via `acriss_codes.yaml` and applied to the database by an idempotent seed script (`scripts/seed_acriss_codes.py`).

- `provider_vehicle_categories` — **the provider's actual catalog**, classified onto ACRISS codes. One row per distinct provider group (i.e. per distinct `external_code` when the provider exposes codes, or per distinct attribute hash when it doesn't). The 4-char `acriss_code` on each row is **classification metadata, not identity**. Multiple rows of the same provider may carry the same `acriss_code` — that is expected and correct (see "Within-provider heterogeneity" below).

- `tenant_vehicle_groups` — **optional, per-tenant taxonomy**. A tenant may declare its own naming for vehicle groups (e.g. "Compactos", "Familiares") and map them onto ACRISS codes via `tenant_vehicle_group_mappings`. This layer is **opt-in**: a tenant that uses ACRISS codes directly does not need to configure anything.

**Why three layers and not two.**

The earlier model had only two layers (`provider_vehicle_groups` and `client_vehicle_groups`), connected by a per-tenant mapping. That model assumed `provider.external_code` was a stable identifier of a vehicle group. In practice, that assumption breaks: some providers do not expose group codes in their public results at all; some have stopped doing so after a website modernization; the model representatives shown for a group may rotate over time even when the underlying group is stable. Building product identity on `external_code` left the system fragile and unable to onboard providers that don't expose codes.

The three-layer model fixes this:

- The stable identity of "a kind of vehicle" in the *product's language* lives in the ACRISS standard.
- Each provider's catalog is captured **faithfully** — one row per group the provider distinguishes — with each row tagged with the ACRISS code it belongs to.
- Tenants without strong opinions consume ACRISS codes directly; tenants with their own internal language map onto ACRISS codes.

**Why ACRISS, not a custom taxonomy.**

The ACRISS 4-character code (Category + Body Type + Transmission + Fuel) is the car rental industry standard used by GDS systems (Amadeus, Sabre, Travelport) and all major providers. Adopting it eliminates the translation layer that a custom taxonomy requires, makes classifications immediately interpretable to domain experts, and enables future GDS integrations without a conversion step. The materialized subset (`acriss_codes.yaml`) covers the ~26 codes observed in our market and is extended by the operator when a new code appears.

**Within-provider heterogeneity: faithfully preserved.**

A central design assumption of this model is that **a provider creates as many groups as price tiers it wants to distinguish**. When a provider separates "Grupo EA" (Peugeot 2008 at €57/day) from "Grupo GA" (Kia XCeed Hybrid at €69/day), they are telling us those vehicles command different prices in their internal pricing strategy — even if both might classify as `IDAR`.

The model respects that:

- Each distinct provider group → its own `provider_vehicle_categories` row.
- Multiple rows may share `acriss_code`. There is **no in-database aggregation** of provider groups within a provider.
- Aggregation (e.g. "what's the min price of `IDAR` in a provider this week") happens at **query time** in `PriceQueryService`, not in persistence.
- This preserves the full price signal of the provider for analytical and pricing purposes downstream.

**Classification of provider data into ACRISS codes.**

Every vehicle observed during a scrape must be classified into an ACRISS code before it becomes part of `provider_vehicle_categories`. Classification is performed by an LLM through an abstract `ClassificationService` interface (so the model provider is swappable). The primary implementation is Gemini.

Two important properties of the classification:

1. **Batch by provider, not vehicle-by-vehicle.** The classifier sees the *entire provider catalog at once* — all the groups the provider exposes, together with a representative 7-day price for each. This lets it reason about the provider's internal pricing hierarchy. If two groups command different prices within the same provider, the classifier is expected to distribute them across adjacent ACRISS categories rather than collapse them. The prompt includes the full `acriss_reference.md` (ACRISS standard description) plus the materialized subset of valid codes.

2. **Confidence-aware fallback.** Gemini Flash is the primary model. If Flash returns a confidence below 0.85 for *any* vehicle in the batch, the whole batch is re-attempted with Gemini Pro. If individual vehicles remain below 0.85 even after Pro, those rows are persisted with `acriss_code = NULL` and `pending_review = true`, awaiting manual classification by the operator. Responses with confidence < 0.70 are immediately marked `pending_review` without Pro escalation.

The LLM is never permitted to use ACRISS codes not in the materialized subset; if the response contains an unknown code it is treated as `pending_review`.

**The representative price.**

The price passed to the LLM is computed as the **mean of 7-day prices observed during the probe phase**. This filters noise from any single date (weekends, peak seasons) and reflects the provider's "baseline" pricing for that group. It is **transient** — used only as classification input, not persisted as a column. The true price history lives in `price_observations`.

**When the LLM fails (network, rate limit, error):** the row keeps any previously cached classification if one exists. If not, it is persisted with `acriss_code = NULL` and `pending_review = true`. The scrape itself does not abort.

**When to reclassify a provider.** A provider's full classification is re-run in two situations:
- A new group appears in the provider's catalog (the new group can shift the internal hierarchy interpretation).
- The operator forces it via an explicit reclassification command.

**Subscription lifecycle states.**

```
pending_discovery → pending_mapping → active → paused → cancelled
                                            ↓
                                          broken (mapping orphaned)
```

A subscription enters `pending_mapping` only when the tenant has declared `tenant_vehicle_groups` but has not yet mapped any of them. A subscription with no tenant groups declared at all is `active` from the start — it consumes the canonical taxonomy directly.

A subscription stays in `pending_mapping` only when ZERO mappings exist for tenants that opted into custom groups. Partial mappings are valid and activate the subscription with the declared scope. Canonical categories without a mapping in this tenant are simply rendered with their canonical name in queries.

---

### 2. Provider catalog

Curated by the operator (you), not by tenants.

- `providers` — global catalog. One row per implemented scraper.
- `provider_locations` — locations supported by each provider (e.g. ALC, MAD).
- `provider_rates` — rate plans available per provider.
- `tenant_subscriptions` — what a tenant is monitoring. Joins to a specific `(provider, location, rate)` tuple.
- `provider_vehicle_categories` — see Decision 1. The provider-side catalog rows carry the ACRISS classification (`acriss_category`, `acriss_body_type`, `acriss_transmission`, `acriss_fuel`, generated `acriss_code`, `classification_confidence`, `pending_review`) in addition to the observed display attributes.

**Why curated, not BYO (bring-your-own-scraper).** Scraper quality is the operator's responsibility, not the tenant's. Each new scraper added is a product asset that benefits all existing tenants. SSRF and resource-abuse problems disappear.

**Adding a new provider is operator work.** A developer implements `provider_X_scraper.py`, registers it in `SCRAPER_REGISTRY` (see `CLAUDE.md`), and the catalog gets a new entry. Tenants then subscribe through the UI.

**Reclassification.** Classification is always done at provider granularity (a whole batch at once), never per-vehicle. This reflects the batch nature of the `ClassificationService` (see Decision 1). Reclassification is triggered when a new group appears in the provider's catalog or when the operator forces it explicitly.

---

### 3. Price observation identity

Observations are **global**, not per tenant.

A `price_observation` belongs to a `(provider, location, rate, vehicle_category, pickup_date, duration)` tuple. It does **not** carry `tenant_id`. All tenants subscribed to the same upstream tuple consume the same observations.

**Why global.**
- One scrape serves N tenants → marginal cost of an extra tenant on an existing tuple is near zero.
- New tenants get historical depth from day one (the market history is shared).
- Daily scrape cadence is enough for this domain; no tenant has a legitimate reason for "private history" or "higher frequency than others" in the MVP.

**Tenant isolation in queries** is enforced by joining through `tenant_subscriptions`. If a tenant is not subscribed, the join returns nothing.

**`price_observations` references `provider_vehicle_category_id`** — i.e. a specific provider group (one row in `provider_vehicle_categories`). Each provider group has its own price history, even when several groups share the same `canonical_type_id`. Aggregation across PVCs of the same canonical category — within a provider or across providers — happens at query time in `PriceQueryService`, never in the observation itself.

Translation to `tenant_vehicle_group_id` (when the tenant has declared one) happens at query time via `tenant_vehicle_group_mappings`. This preserves the raw observation as the audit-grade truth and lets tenants opt in or out of their own naming layer without affecting the underlying data.

---

### 4. Scrape period

`period_days` is an attribute of the **subscription**, not a global constant.

Each `tenant_subscription` declares how many days of forward coverage it wants. The MVP defaults to 90; tenants can request longer (up to whatever the provider supports).

**Optimizations deferred to v2:** adaptive probe and frequency-decreasing layered scraping. See `SCRAPING_OPTIMIZATIONS.md`. The current pipeline handles 365 days at daily cadence in under 30 minutes per provider — well within MVP tolerance.

---

### 5. History vs snapshot

**Append-only on change**, with a configurable change threshold. Heartbeats stored separately.

- `price_observations` — a row is inserted **only when the observed price changes** beyond `PRICE_CHANGE_THRESHOLD` (default 0.5%) relative to the **last recorded row** for that tuple. Append-only.
- `price_observation_heartbeats` — one row per tuple, UPDATEd on every scrape regardless of price change. Tracks `last_checked_at` and `last_price_per_day`. Used for staleness detection.

**Threshold comparator subtlety:** the comparator is the last row in `price_observations`, not the last heartbeat. This prevents silent drift where prices move by repeated sub-threshold steps without ever being recorded.

**Why this model.**
- Repeated identical observations carry no information; storing them is noise that distorts analytical queries.
- Most points in this domain don't change day-over-day; storing only changes reduces volume by ~5–10x.
- "Current price" queries are still O(1) per tuple with a proper index.
- Staleness detection is preserved via heartbeats without bloating the main table.

**Threshold configuration:** `PRICE_CHANGE_THRESHOLD` lives in `.env` as a system-wide value. It is noise filtering, not tenant preference. Changing it is an operator decision.

---

### 6. Synthetic data (`ResultExpander`)

**Option C: do not persist synthetic data.** Only real scraped observations are stored. Expansion happens at query time.

- `homogeneous_zones` — persisted output of `SeasonAnalyzer`. Each zone covers a date range for a `(provider, location, rate, provider_vehicle_category)` tuple and has a `representative_date` that is actually scraped.
- `price_observations` — only contains real scrapes (representatives + probe points).
- The application layer (`PriceQueryService` or equivalent) joins zones → representative → observation to answer "price for day X". Returns `is_inferred=true` when the day requested ≠ representative date.

**Why this model.**
- Synthetic prices are **derivations**, not data. Persisting them duplicates information and creates drift if the analyzer changes.
- Improvements to the zone-detection algorithm propagate retroactively without any data migration.
- Volume of `price_observations` is dominated by signal, not by repetition of synthetic points (typically 5–10x reduction vs persisting expanded data).

**Zone re-analysis: total replacement.** When `SeasonAnalyzer` runs again, old zones are flagged `active=false` and new ones inserted with `active=true`. Historical price queries reinterpret old observations under current zones. No SCD versioning of zones in the MVP.

**Index on zones:** partial index `WHERE active=true`. Inactive zones are kept for potential future versioning, but should not weight regular query plans.

**Aggregated market zones (across providers).** When the dashboard shows the market in aggregate ("market average" or "market minimum" across all subscribed providers), the time partition shown to the tenant is the **union of cut points** of the active zones across all providers within the tenant's scope. This produces a partition that is faithful to the heterogeneity of the market (when providers shift seasons on different dates, both shifts are visible). The union grid is computed at query time by `compute_intersected_grid` in `PriceQueryService` and is **not persisted** — it is a derivation, consistent with the broader rule against persisting synthetic data.

---

### 7. Currency

**Level 1: one currency per tenant. Column present on the DB.**

- `tenants.currency CHAR(3) NOT NULL` — fixed at onboarding, not changeable.
- `providers.default_currency CHAR(3) NOT NULL` — catalog metadata.
- `price_observations.currency CHAR(3) NOT NULL` — what the provider returned.
- `price_observation_heartbeats` does **not** carry currency (kept lightweight).

**Subscription validation rule:** at the application layer, a tenant can only subscribe to providers where `provider.default_currency == tenant.currency`. Multi-currency operation is rejected with an explicit message.

**Numeric type for prices: `NUMERIC(10,2)`.** Never `FLOAT`/`DOUBLE PRECISION`. Float aggregation errors are unacceptable in pricing data.

**Migration to Level 2 (true multi-currency)** in the future = add `fx_rates` table, change subscription validation to allow mismatch, add conversion in `PriceQueryService`. No schema change to large tables.

---

### 8. Tenant isolation

**Shared database, `tenant_id` column on tenant-scoped tables.** Three defensive layers.

**Layer 1 — Repository-enforced filtering (mandatory).** `tenant_id` is injected into repositories from request context. Callers cannot pass a different value. Queries always filter automatically. Implemented in `src/saas/infrastructure/persistence/repositories/`.

**Layer 2 — Postgres Row-Level Security (active in the current schema).** Each tenant-scoped table has an RLS policy. Application sets `app.tenant_id` via `set_config('app.tenant_id', ..., true)` in `tenant_context()` (`src/saas/infrastructure/persistence/session.py`) per request. Even if a repository has a bug, the database enforces isolation.

**Layer 3 — Isolation tests (implemented for the data layer; required for any new feature touching tenant-scoped tables).** Verified by `test_app_user_sees_only_own_tenant` in `tests/saas/infrastructure/persistence/test_repositories.py`.

**Catalog tables (no `tenant_id`, no RLS):**
- `providers`, `provider_locations`, `provider_rates`
- `acriss_codes`, `provider_vehicle_categories`
- `homogeneous_zones`, `price_observations`, `price_observation_heartbeats`
- `scrape_runs`

**Tenant-scoped tables (with `tenant_id` and RLS):**
- `users`, `tenant_vehicle_groups`, `tenant_vehicle_group_mappings`
- `tenant_subscriptions`, `pricing_rules`, `pricing_outputs`

**Primary keys: UUIDs** for tenant-scoped entities. Catalog tables can use integers if preferred (their stable external IDs are codes like `provider_a` or canonical type codes like `ECONOMY_PASSENGER` anyway).

**Postgres role structure.** Four roles, separating concerns:

- `smart_rental` — initial superuser created by the Postgres container (`POSTGRES_USER`). Not used by application code; available for ad-hoc administrative tasks at the database level.
- `smart_rental_admin` — owns all schema objects. Used by Alembic to run migrations. RLS applies to it (tables use `FORCE ROW LEVEL SECURITY`).
- `smart_rental_app` — runtime application user. RLS applies. Has `SELECT/INSERT/UPDATE/DELETE` on catalog tables and on tenant-scoped tables. Operations through this role **must** set `app.tenant_id` via `tenant_context()` before touching tenant-scoped tables.
- `smart_rental_super` — administrative role with `BYPASSRLS`. Inherits `smart_rental_admin`'s permissions. Used for cross-tenant operations (provisioning new tenants, system-wide reports). Exposed in code via `super_session()`.

The four roles are created by `deploy/postgres/init/01_create_app_users.sql` on database initialization. Their corresponding connection URLs live in `.env` (`DATABASE_URL`, `ADMIN_DATABASE_URL`, `APP_DATABASE_URL`, `SUPER_DATABASE_URL`).

**Why `FORCE RLS` even on the table owner:** without `FORCE`, the owner bypasses RLS, which would silently break tenant isolation if anyone ran admin queries thinking they were safe. With `FORCE`, the only way to bypass is the explicit `smart_rental_super` role.

---

### 9. Configuration versioning

Pragmatic mix, not uniform across all entities.

**`tenant_subscriptions` — cancel and recreate.** Changes to provider, location, rate, or `period_days` are not edits. The current subscription is marked `cancelled` and a new one is created. Status `cancelled` is terminal. Historical observations remain visible to the tenant in read mode but the subscription does not participate in further scraping or pricing.

**`tenant_vehicle_groups` and `tenant_vehicle_group_mappings` — mutable in place.** Renaming a group, adjusting a mapping is an edit. Historical pricing decisions remain auditable through `pricing_outputs.inputs_snapshot_jsonb`.

**`acriss_codes` — sourced from `acriss_codes.yaml`, applied to DB by idempotent seed** (`scripts/seed_acriss_codes.py`). The seed script (a) inserts new codes, (b) updates display_name/description/criteria/examples when changed, (c) marks missing codes as `active = false` (never deletes — they may have historical FK references). See Decision 1 for re-classification semantics.

**`pricing_rules` — explicit versioning.** Editing a rule does not UPDATE in place: a new row is created with `version = N+1`, the old row gets `superseded_at` and `superseded_by_id`. `pricing_outputs.rule_id` references the specific version used at calculation time.

**`pricing_outputs.inputs_snapshot_jsonb`** captures the complete context of each pricing calculation: mapping in effect, competitor prices used, parameters consumed, taxonomy version. This is the audit trail. It must be sufficient on its own to reconstruct any past decision.

**Why this mix.**
- Subscriptions are operational contracts; their semantics change qualitatively when the upstream tuple changes. Cancel-and-recreate makes that explicit.
- Group/mapping edits are interpretive; the snapshot in `pricing_outputs` already preserves audit value.
- The canonical taxonomy is product-wide configuration; the YAML + version mechanism gives a single, reviewable source of truth.
- Pricing rules carry direct economic consequence and are few in number; versioning them is cheap and high-value.

---

### 10. Canonical output format

**Format A: zone-based price table.** Not day×duration matrix.

Output structure:
```
Category               | Zone (period)     | 1d | 2d | ... | 7d | 14d | 21d | 28d
ECONOMY_PASSENGER      | Mid (01–14 Jul)   | 45 | 42 | ... | 38 |  35 |  33 |  32
ECONOMY_PASSENGER      | High (15–31 Jul)  | 65 | 60 | ... | 52 |  48 |  46 |  44
...
```

When the tenant has declared `tenant_vehicle_groups`, the rows are rendered with the tenant's labels instead of the canonical code (e.g. "Compactos" instead of `ECONOMY_PASSENGER`). The underlying data is the same; only the presentation layer changes.

**Why Format A.**
- Reflects how the rent-a-car pricing domain actually works (tariff table per season).
- Pricing rules operate on zones, not days. Internal data flow stays compressed.
- Day×duration expansion is trivially derivable from this format if a downstream system needs it; the reverse is lossy.

**Format B (day × duration matrix)** remains available as a presentation option through `PriceQueryService`, computed on demand. Not a separate stored representation.

---

## Part 2 — Schema (pseudo-DDL)

> Pseudo-DDL only. Real DDL with constraints, exact types, indexes and partitioning is generated during implementation. This section is the canonical structural reference.

### Tenant-scoped (with `tenant_id`, RLS enabled)

```
tenants
  id UUID PK
  name
  currency CHAR(3) NOT NULL          -- ISO 4217
  plan
  created_at

users
  id UUID PK
  tenant_id UUID FK → tenants
  email
  external_auth_id                    -- identity from external auth provider
                                      -- (the 'sub' claim of the JWT, or equivalent)
  role                                -- enum, default 'owner'
                                      -- MVP only uses 'owner'; expand when needed
  created_at
  ...
  -- Authentication itself (passwords, sessions, MFA, recovery, magic links,
  -- SSO) is delegated to an external identity provider. This table only
  -- stores the local identity row tied to the external one. Do NOT add
  -- columns for password_hash, session_token, password_reset_token, etc.

tenant_vehicle_groups
  id UUID PK
  tenant_id UUID FK
  code           -- tenant-defined (e.g. "Compactos", "Familiares", "SUV")
  name
  description
  display_order
  -- Optional layer: tenants without custom groups use canonical types directly.

tenant_vehicle_group_mappings
  id UUID PK
  tenant_id UUID FK
  tenant_vehicle_group_id UUID FK → tenant_vehicle_groups
  acriss_code VARCHAR(4) FK → acriss_codes.code
  created_at
  created_by
  notes
  -- Maps tenant labels onto ACRISS codes. N:M permitted: a tenant group
  -- may map onto multiple ACRISS codes, and the same ACRISS code may
  -- appear in multiple tenant groups (the latter is rare but valid).

tenant_subscriptions
  id UUID PK
  tenant_id UUID FK
  provider_id FK
  provider_location_id FK
  provider_rate_id FK
  period_days INT NOT NULL
  status                              -- pending_discovery | pending_mapping
                                      -- | active | paused | cancelled | broken
  subscribed_at TIMESTAMPTZ
  cancelled_at TIMESTAMPTZ NULL
  -- Constraint: at most one (active|paused) per (tenant_id, provider_id,
  -- provider_location_id, provider_rate_id)

pricing_rules
  id UUID PK
  tenant_id UUID FK
  acriss_code VARCHAR(4) FK → acriss_codes.code
                              -- rules operate on ACRISS codes;
                              -- tenants with custom groups resolve through
                              -- tenant_vehicle_group_mappings at apply time
  name
  version INT NOT NULL
  condition_jsonb
  formula_jsonb
  floor NUMERIC(10,2) NULL
  ceiling NUMERIC(10,2) NULL
  active BOOLEAN
  created_at
  superseded_at TIMESTAMPTZ NULL
  superseded_by_id UUID NULL FK → pricing_rules

pricing_outputs
  id UUID PK
  tenant_id UUID FK
  acriss_code VARCHAR(4) FK → acriss_codes.code
  pickup_date DATE
  duration_days INT
  computed_price NUMERIC(10,2)
  rule_id UUID FK → pricing_rules    -- specific version used
  inputs_snapshot_jsonb               -- mapping, competitor prices, params,
                                      -- taxonomy_version, classification_versions
  computed_at TIMESTAMPTZ
```

### Catalog (global, no `tenant_id`, no RLS)

```
acriss_codes
  code           VARCHAR(4) PK               -- e.g. 'CFAR', 'IDAR'
  acriss_category    CHAR(1) NOT NULL        -- position 1
  acriss_body_type   CHAR(1) NOT NULL        -- position 2
  acriss_transmission CHAR(1) NOT NULL       -- position 3
  acriss_fuel        CHAR(1) NOT NULL        -- position 4
  display_name   VARCHAR(128) NOT NULL
  description    TEXT NOT NULL DEFAULT ''
  criteria       JSONB NOT NULL DEFAULT '[]'
  examples       JSONB NOT NULL DEFAULT '[]'
  active         BOOLEAN NOT NULL DEFAULT true
  created_at     TIMESTAMPTZ
  last_updated_at TIMESTAMPTZ
  -- Source of truth: acriss_codes.yaml. Applied to DB by scripts/seed_acriss_codes.py.
  -- Deactivated codes are never deleted; they remain as FK references.

providers
  id PK
  code                                -- 'provider_a', 'provider_b', ...
  display_name
  scraper_key                         -- maps to SCRAPER_REGISTRY
  default_currency CHAR(3) NOT NULL
  status                              -- 'active' | 'beta' | 'deprecated' | 'broken'
  created_at

provider_locations
  id PK
  provider_id FK
  location_code                       -- 'ALC', 'MAD', ...
  location_name
  country
  city
  active

provider_rates
  id PK
  provider_id FK
  rate_code
  rate_name
  description
  active

provider_vehicle_categories
  id PK
  provider_id FK
  provider_location_id FK
  provider_rate_id FK
  -- ACRISS classification (4 orthogonal attributes + generated code):
  acriss_category    CHAR(1) NULL
  acriss_body_type   CHAR(1) NULL
  acriss_transmission CHAR(1) NULL
  acriss_fuel        CHAR(1) NULL
  acriss_code VARCHAR(4) GENERATED ALWAYS AS               -- NULL until all 4 attrs set
    (acriss_category || acriss_body_type || acriss_transmission || acriss_fuel) STORED
    FK → acriss_codes.code
  classification_confidence FLOAT NULL                     -- last LLM confidence (0..1)
  pending_review BOOLEAN NOT NULL DEFAULT false            -- operator attention required
  -- Observed display attributes (last seen):
  example_models  TEXT NOT NULL DEFAULT ''
  seats           INT NULL
  luggage         INT NULL
  -- Documentary metadata when the provider exposes group identifiers:
  external_code   VARCHAR(64) NULL
  external_name   TEXT NULL
  -- Identity fallback when external_code is NULL:
  attributes_hash VARCHAR(16) NULL  -- sha256[:16] of (example_models, seats, luggage)
  -- Lifecycle:
  first_seen_at   TIMESTAMPTZ
  last_seen_at    TIMESTAMPTZ
  active          BOOLEAN NOT NULL DEFAULT true
  -- Identity: each distinct provider group is its own row. Identity key is:
  --   - UNIQUE (provider_id, provider_location_id, provider_rate_id, external_code)
  --     when external_code IS NOT NULL.
  --   - UNIQUE (provider_id, provider_location_id, provider_rate_id, attributes_hash)
  --     when external_code IS NULL. attributes_hash is a deterministic sha256-truncated
  --     hex of (example_models, seats, luggage).
  -- acriss_code is classification metadata, NOT part of identity. Multiple rows
  -- of the same provider may share acriss_code (provider distinguishes price
  -- tiers more finely than ACRISS does — see Decision 1, "Within-
  -- provider heterogeneity"). Aggregation across them happens at query time.

scrape_runs
  id PK
  provider_id FK
  provider_location_id FK
  provider_rate_id FK
  started_at
  finished_at
  status
  stats_jsonb
  error TEXT NULL

homogeneous_zones
  id PK
  provider_id FK
  provider_location_id FK
  provider_rate_id FK
  provider_vehicle_category_id FK → provider_vehicle_categories
  start_date DATE
  end_date DATE
  representative_date DATE
  detected_at
  active BOOLEAN
  -- Partial index: WHERE active = true

price_observations
  id BIGSERIAL
  provider_id
  provider_location_id
  provider_rate_id
  provider_vehicle_category_id FK → provider_vehicle_categories
  scrape_run_id FK
  pickup_date DATE
  duration_days INT
  price_per_day NUMERIC(10,2)
  total_price NUMERIC(10,2)
  currency CHAR(3) NOT NULL
  observed_at TIMESTAMPTZ
  PRIMARY KEY (id, observed_at)
  PARTITION BY RANGE (observed_at)    -- monthly partitions
  -- IMPLEMENTATION NOTE: only the partitions for the current and next
  -- month are created by the initial migration. Automatic creation
  -- of future partitions is NOT yet implemented and is on the
  -- deferred list. Until it is, INSERTs targeting a date beyond the
  -- existing partitions will fail with "no partition of relation
  -- found for row".
  -- Main index: (provider_id, provider_location_id, provider_rate_id,
  --              provider_vehicle_category_id, pickup_date, duration_days,
  --              observed_at DESC)

price_observation_heartbeats
  provider_id
  provider_location_id
  provider_rate_id
  provider_vehicle_category_id FK → provider_vehicle_categories
  pickup_date DATE
  duration_days INT
  last_checked_at TIMESTAMPTZ
  last_price_per_day NUMERIC(10,2)
  PRIMARY KEY (provider_id, provider_location_id, provider_rate_id,
               provider_vehicle_category_id, pickup_date, duration_days)
```

---

## Part 3 — Anatomy of the main query

How the model answers the canonical client question:

> *"For tenant T, give me prices for ACRISS codes {EDAR, CDAR, IDAR} on subscription S, for pickup dates between D1 and D2, in durations {1,2,3,4,5,6,7,14,21,28}."*

(When the tenant has declared `tenant_vehicle_groups`, the query receives tenant group codes and resolves them to ACRISS codes via `tenant_vehicle_group_mappings` before the rest of the flow.)

### Conceptual flow

1. Resolve subscription S to its `(provider_id, location_id, rate_id)` tuple.
2. If the request used tenant group codes, resolve them to ACRISS codes via `tenant_vehicle_group_mappings`.
3. Resolve ACRISS codes to provider vehicle categories: `provider_vehicle_categories` filtered by `(provider, acriss_code IN ...)`. **This may return multiple PVCs per ACRISS code** (a provider may have several groups tagged with the same ACRISS code — see Decision 1, "Within-provider heterogeneity").
4. Find active zones in `homogeneous_zones` overlapping [D1, D2] for those provider vehicle categories.
5. For each zone × duration, fetch the latest observation in `price_observations` for the zone's representative date — one observation per PVC.
6. **Aggregate per (acriss_code, zone, duration)**: when multiple PVCs of the same ACRISS code contribute, apply the configured policy (default: `min`). The result is one price per (acriss_code, zone, duration) per provider.
7. Return one row per (acriss_code or tenant_group, zone, duration) with `is_inferred` flagged appropriately when expanding to specific dates.

### Single SQL realization

The query in SQL is similar to the earlier model but adds a `GROUP BY` for the aggregation step:

```sql
WITH zones AS (
  SELECT provider_vehicle_category_id,
         representative_date,
         start_date,
         end_date
  FROM homogeneous_zones
  WHERE provider_id = :P
    AND provider_location_id = :L
    AND provider_rate_id = :R
    AND provider_vehicle_category_id IN (:provider_categories)
    AND active = true
    AND end_date >= :D1
    AND start_date <= :D2
),
latest_observations AS (
  SELECT DISTINCT ON (provider_vehicle_category_id, pickup_date, duration_days)
         provider_vehicle_category_id,
         pickup_date,
         duration_days,
         price_per_day,
         total_price,
         currency
  FROM price_observations
  WHERE provider_id = :P
    AND provider_location_id = :L
    AND provider_rate_id = :R
    AND provider_vehicle_category_id IN (:provider_categories)
    AND pickup_date IN (SELECT representative_date FROM zones)
    AND duration_days IN (1,2,3,4,5,6,7,14,21,28)
  ORDER BY provider_vehicle_category_id, pickup_date, duration_days, observed_at DESC
),
joined AS (
  SELECT pvc.acriss_code,
         z.start_date,
         z.end_date,
         z.representative_date,
         lo.duration_days,
         lo.price_per_day,
         lo.currency
  FROM zones z
  JOIN provider_vehicle_categories pvc
    ON pvc.id = z.provider_vehicle_category_id
  JOIN latest_observations lo
    ON lo.provider_vehicle_category_id = z.provider_vehicle_category_id
   AND lo.pickup_date = z.representative_date
)
SELECT acriss_code,
       start_date,
       end_date,
       duration_days,
       MIN(price_per_day) AS price_per_day,   -- min policy across PVCs
       currency
FROM joined
GROUP BY acriss_code, start_date, end_date, duration_days, currency;
```

The application layer then:
- Translates `canonical_type_id` to the tenant's label if applicable (via `tenant_vehicle_group_mappings`).
- If the tenant maps multiple canonicals to a single tenant_vehicle_group, applies a second-level aggregation (default: `min` again) across canonicals.
- Returns Format A directly, or expands to Format B if explicitly requested.

### Volume reasoning

For a 31-day window, 3 canonical categories, 10 durations, a provider that has on average 2 PVCs per canonical:
- **PVCs touched:** ~6 (3 canonicals × 2 PVCs).
- **Zones overlapping the window:** ~2 per PVC = ~12 zones.
- **Observations fetched:** ~12 zones × 10 durations = ~120 rows.
- **Rows in the output after aggregation:** ~60 (3 canonicals × 2 zones × 10 durations).
- **Day×duration expansion (if Format B requested):** ~930, computed in memory.

The aggregation `GROUP BY` adds negligible cost; Postgres handles it efficiently with the available indexes.

### Index implication

The main index on `price_observations` is unchanged from the earlier design:

```
(provider_id, provider_location_id, provider_rate_id,
 provider_vehicle_category_id, pickup_date, duration_days,
 observed_at DESC)
```

`DISTINCT ON` over this ordering is the idiomatic Postgres way to fetch "latest per tuple" and uses the index efficiently. The subsequent `JOIN` to `provider_vehicle_categories` for the canonical aggregation is a small hash join on the in-memory result of the CTEs.

---

## Part 4 — Deliberately deferred

These are concerns identified during design that the current model does **not** address, with explicit re-evaluation triggers.

### Authentication provider choice

Authentication is delegated to an external identity provider. The choice of provider (Auth0, Clerk, WorkOS, Supabase Auth, FusionAuth, Keycloak self-hosted, etc.) is not made yet.

**Why deferred.** The right choice depends on variables not yet resolved: budget, whether enterprise SSO is needed soon, hosting preferences (managed vs self-hosted), and the profile of the first real customer. Building auth from scratch is not on the table for an MVP in 2026.

**Trigger.** Before exposing the first endpoint that requires real user login.

**What's already decided** (so no rework when the provider is chosen):
- `users.external_auth_id` will hold the provider's identity reference (the 'sub' claim of the JWT, or equivalent).
- `users.role` exists from day one with `'owner'` as default. Granular roles wait until a real customer demands them.

### Machine-to-machine authentication (API keys)

Not in the model yet. Will appear when a customer wants to consume pricing outputs from their own system automatically.

**Trigger.** First customer requesting programmatic API access.

**Expected shape (pre-decided to avoid surprises):**
```
api_keys
  id UUID PK
  tenant_id UUID FK            -- keys belong to the tenant, not the user
  name
  key_hash                     -- never store the key in clear
  key_prefix                   -- visible prefix for UI identification
  scopes                       -- list of permissions (jsonb or array)
  created_at
  created_by_user_id FK
  last_used_at
  expires_at NULL
  revoked_at NULL
```

The key principle: API keys are tenant-scoped, not user-scoped. They survive user deactivation.

### Intermediate-duration calculation

The model stores prices for the bracket `{1,2,3,4,5,6,7,14,21,28}`. Durations not in the bracket (e.g. 10 days) are not stored.

**Why deferred.** It is provider pricing logic (typically: lower-bracket price + extra days × bracket average), not a storage concern. Lives in `PriceQueryService`.

**Trigger.** A client requests durations outside the bracket as a first-class output. Implement in the service; cache results only if measured to be a hotspot.

### True multi-currency (Level 2)

Currently the model enforces one currency per tenant.

**Trigger.** A tenant wants to monitor providers operating in a different currency than their own.

**Migration.** Add `fx_rates` table (with timestamped rates and a chosen daily reference policy), relax the subscription-validation rule, add conversion in `PriceQueryService`. No schema change to `price_observations` or `heartbeats`.

### SCD-style versioning of zones and configuration

Zones are replaced wholesale on re-analysis. Group renames and mapping changes are mutable in place.

**Trigger.** A client demands "show me how you saw the market 3 months ago, with the zones in effect at that time" or external audit pressure requires reconstructing past interpretive state byte-for-byte.

**Migration.** Add `valid_from`/`valid_to` to `homogeneous_zones`, `tenant_vehicle_groups`, `tenant_vehicle_group_mappings`. Change relevant queries to use the time-effective row. Existing `inputs_snapshot_jsonb` in `pricing_outputs` already covers most audit cases without this.

### Higher scrape frequency than daily

Current model assumes one scrape per day per `(provider, location, rate)` tuple is enough. The append-on-change pattern handles intra-day perfectly (multiple changes within a day generate multiple rows), but the scheduler is daily.

**Trigger.** A client demonstrably needs intra-day signal (e.g. competitor adjusts prices in real time and lag matters operationally).

**Migration.** Scheduler change only. Model is unaffected.

### Per-tenant scrape frequency

Current model assumes one cadence for all tenants subscribed to the same tuple (justified by sharing observations).

**Trigger.** A premium tier offers higher-frequency monitoring as a paid feature.

**Migration.** Either (a) introduce per-tenant scrape jobs for premium tiers (breaks the shared-observation optimization for those tuples), or (b) raise the global cadence and accept the cost. Decision is product-driven.

### Automatic creation of future price_observations partitions

Only the current and next month's partitions exist after the initial migration. New partitions are not auto-created.

**Why deferred.** Adding it preemptively requires choosing a mechanism (cron job, `pg_partman` extension, application-level scheduler hook), and the choice depends on operational context not yet decided.

**Trigger.** Any of:
- The next-month partition no longer covers a date the scraper is about to insert (operational risk: INSERTs fail silently in logs until a write hits the missing range).
- Volume pressure warrants TimescaleDB hypertables (which would obviate manual partitioning).

**Migration options when triggered.**
- Application-level: a startup hook or scheduled task that ensures the next N months' partitions exist before scrapes run.
- `pg_partman` extension: declarative partition lifecycle.
- TimescaleDB: full hypertable replacement; bigger surgery but handles partitioning, compression, and retention together.

### Tenant data residency / regional isolation

Single shared database, single region.

**Trigger.** EU/non-EU regulatory requirements force per-region storage, or a large enterprise customer demands physical data segregation.

**Migration.** Move toward database-per-tenant or region-per-deployment. Significant operational impact; do not anticipate without a contract on the table.

---

## Operating notes for implementers

- Generate real DDL from this document, not from intuition. If something is missing here, surface it before writing the migration.
- The threshold for change detection (`PRICE_CHANGE_THRESHOLD`) compares against the **last recorded row in `price_observations`**, not against the heartbeat. This is a correctness point, not a style preference.
- `inputs_snapshot_jsonb` is the audit trail for pricing decisions. Any field that participates in the calculation must be captured there at calculation time, because mutable configuration upstream can change after the fact. Include the `acriss_code` and `classification_confidence` of every `provider_vehicle_categories` row consumed.
- Authentication is **not** built locally. Do not add tables for passwords, sessions, password reset tokens, or any mechanism that would duplicate what an external identity provider does. The `users` table only holds the local identity bound to the external `sub`.
- Tests for tenant isolation are part of the definition of "API done", not an optional nicety.
- The LLM-based classification is wrapped behind an abstract `ClassificationService` interface. The interface must not leak provider-specific concepts (request shape, response shape, authentication). Implementations live in infrastructure; the rest of the system depends only on the interface. Confidence threshold (0.85) is hardcoded in the service composition.
- Classification is **batch by provider**, not vehicle-by-vehicle. The classifier receives the complete provider catalog at once together with each group's representative 7-day price, so it can reason about the provider's internal pricing hierarchy. Calling the classifier with a single vehicle in isolation is supported by the interface but is **not** the production path — it loses the hierarchical context.
- The representative 7-day price passed to the classifier is computed as the **mean of all 7-day prices observed during the probe phase** for that group. It is transient (used only as classifier input, not persisted). The true price history is in `price_observations`.
- `provider_vehicle_categories` identity is `(provider, location, rate, external_code)` when `external_code` is non-null, or `(provider, location, rate, attributes_hash)` otherwise. `acriss_code` is **not** part of identity; multiple rows of the same provider may share it, by design (Decision 1).
- `acriss_codes.yaml` is the source of truth. `scripts/seed_acriss_codes.py` must be idempotent: running it twice on an unchanged YAML produces zero changes in DB. Running it after a YAML edit applies only the deltas.
