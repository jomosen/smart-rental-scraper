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

### 1. Vehicle group mapping

Three entities, N:M relation, manual mapping per tenant.

- `client_vehicle_groups` — taxonomy defined by the tenant for their own business.
- `provider_vehicle_groups` — groups discovered by scraping each `(provider, location, rate)` tuple. Catalog-side. The same provider monitored by two tenants in the same configuration shares the same provider group rows. Each row also carries display attributes populated by the scraper: `example_models` (required, e.g. `"Fiat Panda, Kia Picanto"`), `seats` (nullable int), `luggage` (nullable int), `transmission` (nullable varchar, `'manual'` or `'automatic'`). These attributes are written by `upsert_seen` on every probe and extraction pass; they are updated in-place when the provider changes them. `example_models` is NOT NULL — a provider that does not display example models is not worth scraping and should not be onboarded.
- `vehicle_group_mappings` — N:M relation between the two, scoped to the tenant.

**Why N:M, not N:1.** Two provider groups ("Compact" and "Compact Auto") may map to the same client group. Forcing uniqueness on either side breaks valid scenarios.

**Discovery is automatic; mapping is manual.** When a tenant subscribes to a `(provider, location, rate)` tuple for the first time, the system runs a discovery scrape and populates `provider_vehicle_groups`. The tenant then maps each one (or chooses to ignore it) before activating the subscription.

**Subscription lifecycle states:**
```
pending_discovery → pending_mapping → active → paused → cancelled
                                            ↓
                                          broken (mapping orphaned)
```

A subscription stays in `pending_mapping` only when ZERO mappings exist for its tuple.
Partial mappings are valid and activate the subscription with the declared scope.

A subscription becomes `active` when at least one mapping exists for its tuple. Provider
groups without a mapping in this tenant are out of the tenant's scope and do not appear
in its queries. (Previously the requirement was "all groups mapped or explicitly ignored",
but the "explicitly ignored" mechanism was never built and the strict completeness check
blocked legitimate use cases where a customer doesn't operate every group the provider
offers. See MILESTONES.md for the revision context.)

**New provider groups appearing later** in an active subscription do not break the scrape. They land in `provider_vehicle_groups` as `active=true` but have no mapping, are excluded from pricing, and trigger a notification to the tenant.

---

### 2. Provider catalog

Curated by the operator (you), not by tenants.

- `providers` — global catalog. One row per implemented scraper.
- `provider_locations` — locations supported by each provider (e.g. ALC, MAD).
- `provider_rates` — rate plans available per provider.
- `tenant_subscriptions` — what a tenant is monitoring. Joins to a specific `(provider, location, rate)` tuple.

**Why curated, not BYO (bring-your-own-scraper).** Scraper quality is the operator's responsibility, not the tenant's. Each new scraper added is a product asset that benefits all existing tenants. SSRF and resource-abuse problems disappear.

**Adding a new provider is operator work.** A developer implements `provider_X_scraper.py`, registers it in `SCRAPER_REGISTRY` (see `CLAUDE.md`), and the catalog gets a new entry. Tenants then subscribe through the UI.

---

### 3. Price observation identity

Observations are **global**, not per tenant.

A `price_observation` belongs to a `(provider, location, rate, vehicle_group, pickup_date, duration)` tuple. It does **not** carry `tenant_id`. All tenants subscribed to the same upstream tuple consume the same observations.

**Why global.**
- One scrape serves N tenants → marginal cost of an extra tenant on an existing tuple is near zero.
- New tenants get historical depth from day one (the market history is shared).
- Daily scrape cadence is enough for this domain; no tenant has a legitimate reason for "private history" or "higher frequency than others" in the MVP.

**Tenant isolation in queries** is enforced by joining through `tenant_subscriptions`. If a tenant is not subscribed, the join returns nothing.

**`price_observations` references `provider_vehicle_group_id`** (raw provider data). Translation to `client_vehicle_group_id` happens at query time via `vehicle_group_mappings`. This preserves the raw observation for audit/debugging and supports tenants that haven't yet mapped certain provider groups.

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

- `homogeneous_zones` — persisted output of `SeasonAnalyzer`. Each zone covers a date range for a `(provider, location, rate, provider_vehicle_group)` tuple and has a `representative_date` that is actually scraped.
- `price_observations` — only contains real scrapes (representatives + probe points).
- The application layer (`PriceQueryService` or equivalent) joins zones → representative → observation to answer "price for day X". Returns `is_inferred=true` when the day requested ≠ representative date.

**Why this model.**
- Synthetic prices are **derivations**, not data. Persisting them duplicates information and creates drift if the analyzer changes.
- Improvements to the zone-detection algorithm propagate retroactively without any data migration.
- Volume of `price_observations` is dominated by signal, not by repetition of synthetic points (typically 5–10x reduction vs persisting expanded data).

**Zone re-analysis: total replacement.** When `SeasonAnalyzer` runs again, old zones are flagged `active=false` and new ones inserted with `active=true`. Historical price queries reinterpret old observations under current zones. No SCD versioning of zones in the MVP.

**Index on zones:** partial index `WHERE active=true`. Inactive zones are kept for potential future versioning, but should not weight regular query plans.

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
- `providers`, `provider_locations`, `provider_rates`, `provider_vehicle_groups`
- `homogeneous_zones`, `price_observations`, `price_observation_heartbeats`
- `scrape_runs`

**Tenant-scoped tables (with `tenant_id` and RLS):**
- `users`, `client_vehicle_groups`, `vehicle_group_mappings`
- `tenant_subscriptions`, `pricing_rules`, `pricing_outputs`

**Primary keys: UUIDs** for tenant-scoped entities. Catalog tables can use integers if preferred (their stable external IDs are codes like `provider_a` anyway).

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

**`client_vehicle_groups` and `vehicle_group_mappings` — mutable in place.** Renaming a group, adjusting a mapping is an edit. Historical pricing decisions remain auditable through `pricing_outputs.inputs_snapshot_jsonb`.

**`pricing_rules` — explicit versioning.** Editing a rule does not UPDATE in place: a new row is created with `version = N+1`, the old row gets `superseded_at` and `superseded_by_id`. `pricing_outputs.rule_id` references the specific version used at calculation time.

**`pricing_outputs.inputs_snapshot_jsonb`** captures the complete context of each pricing calculation: mapping in effect, competitor prices used, parameters consumed. This is the audit trail. It must be sufficient on its own to reconstruct any past decision.

**Why this mix.**
- Subscriptions are operational contracts; their semantics change qualitatively when the upstream tuple changes. Cancel-and-recreate makes that explicit.
- Group/mapping edits are interpretive; the snapshot in `pricing_outputs` already preserves audit value.
- Pricing rules carry direct economic consequence and are few in number; versioning them is cheap and high-value.

---

### 10. Canonical output format

**Format A: zone-based price table.** Not day×duration matrix.

Output structure:
```
Group | Zone (period)     | 1d | 2d | ... | 7d | 14d | 21d | 28d
B     | Mid (01–14 Jul)   | 45 | 42 | ... | 38 |  35 |  33 |  32
B     | High (15–31 Jul)  | 65 | 60 | ... | 52 |  48 |  46 |  44
...
```

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

client_vehicle_groups
  id UUID PK
  tenant_id UUID FK
  code           -- tenant-defined (e.g. "B", "C", "SUV")
  name
  description
  display_order

vehicle_group_mappings
  id UUID PK
  tenant_id UUID FK
  client_vehicle_group_id UUID FK
  provider_vehicle_group_id UUID FK
  created_at
  created_by
  notes

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
  client_vehicle_group_id UUID FK
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
  client_vehicle_group_id UUID FK
  pickup_date DATE
  duration_days INT
  computed_price NUMERIC(10,2)
  rule_id UUID FK → pricing_rules    -- specific version used
  inputs_snapshot_jsonb               -- mapping, competitor prices, params
  computed_at TIMESTAMPTZ
```

### Catalog (global, no `tenant_id`, no RLS)

```
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

provider_vehicle_groups
  id PK
  provider_id FK
  provider_location_id FK
  provider_rate_id FK
  external_code
  external_name
  example_models TEXT NOT NULL    -- e.g. "Fiat Panda, Kia Picanto" (required; '' until first scrape)
  seats          INT NULL
  luggage        INT NULL
  transmission   VARCHAR(16) NULL -- 'manual' | 'automatic' | NULL
  first_seen_at
  last_seen_at
  active

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
  provider_vehicle_group_id FK
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
  provider_vehicle_group_id
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
  --              provider_vehicle_group_id, pickup_date, duration_days,
  --              observed_at DESC)

price_observation_heartbeats
  provider_id
  provider_location_id
  provider_rate_id
  provider_vehicle_group_id
  pickup_date DATE
  duration_days INT
  last_checked_at TIMESTAMPTZ
  last_price_per_day NUMERIC(10,2)
  PRIMARY KEY (provider_id, provider_location_id, provider_rate_id,
               provider_vehicle_group_id, pickup_date, duration_days)
```

---

## Part 3 — Anatomy of the main query

How the model answers the canonical client question:

> *"For tenant T, give me prices for client groups {B, C, SUV} on subscription S, for pickup dates between D1 and D2, in durations {1,2,3,4,5,6,7,14,21,28}."*

### Conceptual flow

1. Resolve subscription S to its `(provider_id, location_id, rate_id)` tuple.
2. Resolve client groups {B, C, SUV} to provider groups via `vehicle_group_mappings`.
3. Find active zones in `homogeneous_zones` overlapping [D1, D2] for those provider groups.
4. For each zone × duration, fetch the latest observation in `price_observations` for the zone's representative date.
5. Return one row per (client_group, zone, duration) with `is_inferred` flagged appropriately when expanding to specific dates.

### Single SQL realization

The whole flow collapses into one SQL with two CTEs:

```sql
WITH zones AS (
  SELECT provider_vehicle_group_id,
         representative_date,
         start_date,
         end_date
  FROM homogeneous_zones
  WHERE provider_id = :P
    AND provider_location_id = :L
    AND provider_rate_id = :R
    AND provider_vehicle_group_id IN (:provider_groups)
    AND active = true
    AND end_date >= :D1
    AND start_date <= :D2
),
latest_observations AS (
  SELECT DISTINCT ON (provider_vehicle_group_id, pickup_date, duration_days)
         provider_vehicle_group_id,
         pickup_date,
         duration_days,
         price_per_day,
         total_price,
         currency
  FROM price_observations
  WHERE provider_id = :P
    AND provider_location_id = :L
    AND provider_rate_id = :R
    AND provider_vehicle_group_id IN (:provider_groups)
    AND pickup_date IN (SELECT representative_date FROM zones)
    AND duration_days IN (1,2,3,4,5,6,7,14,21,28)
  ORDER BY provider_vehicle_group_id, pickup_date, duration_days, observed_at DESC
)
SELECT z.provider_vehicle_group_id,
       z.representative_date,
       z.start_date,
       z.end_date,
       lo.duration_days,
       lo.price_per_day,
       lo.total_price,
       lo.currency
FROM zones z
JOIN latest_observations lo
  ON lo.provider_vehicle_group_id = z.provider_vehicle_group_id
 AND lo.pickup_date = z.representative_date;
```

The application layer then:
- Joins to `vehicle_group_mappings` to translate `provider_vehicle_group_id` → `client_vehicle_group_id` (handles N:M aggregation policy).
- Returns Format A directly, or expands to Format B if explicitly requested.

### Volume reasoning

For a 31-day window, 3 client groups, 10 durations:
- **Distinct prices in the answer:** ~60 (assuming ~2 zones in the window per group × 10 durations).
- **Rows fetched from BD:** ~60 (one per zone × duration).
- **Day×duration expansion (if Format B requested):** 930, computed in memory.

This is the validation that the model supports the canonical use case efficiently.

### Index implication

The main index on `price_observations` is dictated by this query:

```
(provider_id, provider_location_id, provider_rate_id,
 provider_vehicle_group_id, pickup_date, duration_days,
 observed_at DESC)
```

`DISTINCT ON` over this ordering is the idiomatic Postgres way to fetch "latest per tuple" and uses the index efficiently.

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

**Migration.** Add `valid_from`/`valid_to` to `homogeneous_zones`, `client_vehicle_groups`, `vehicle_group_mappings`. Change relevant queries to use the time-effective row. Existing `inputs_snapshot_jsonb` in `pricing_outputs` already covers most audit cases without this.

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
- `inputs_snapshot_jsonb` is the audit trail for pricing decisions. Any field that participates in the calculation must be captured there at calculation time, because mutable configuration upstream can change after the fact.
- Authentication is **not** built locally. Do not add tables for passwords, sessions, password reset tokens, or any mechanism that would duplicate what an external identity provider does. The `users` table only holds the local identity bound to the external `sub`.
- Tests for tenant isolation are part of the definition of "API done", not an optional nicety.
