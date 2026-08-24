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

**The interlingua mapping flow (current model).**

The mapping path at query time is always:

```
tenant_vehicle_group
    → tenant_vehicle_group_mappings.acriss_code
        → provider_vehicle_categories WHERE acriss_code matches
            → price_observations
```

There is **no direct tenant group ↔ provider group mapping**. ACRISS is the interlingua through which both sides are resolved independently: the provider's catalog is classified into ACRISS at scrape time; the tenant's groups are mapped to ACRISS codes at configuration time; the two are joined at query time. This means a tenant group definition is **provider-agnostic** — once a tenant declares "Compactos → {CDAR, CFAR}", that definition applies uniformly across any provider subscribed, regardless of what the provider calls its groups internally.

The join is implemented in `PriceQueryService._resolve_mappings()` (`src/saas/application/price_query/service.py`): it goes from `tenant_vehicle_group_id` → `acriss_code` (via `TenantVehicleGroupMapping`) → `[pvc_id, …]` (via `ProviderVehicleCategory WHERE acriss_code = ?`). This is the single authoritative traversal. No other code path connects tenant groups to provider data.

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

- `homogeneous_zones` — persisted output of `SeasonAnalyzer`. Each zone covers a date range for a `(provider, location, rate, provider_vehicle_category)` tuple and has a `representative_date` that is *normally* scraped. It is not guaranteed: availability gaps or re-analysis can leave the representative without an observation, so the read side falls back to the closest observation within the zone range (see the `homogeneous_zones` derivation rule in the schema section).
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
- `provider_recipes`  (see Decision 11)

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

### 11. Scraping recipes — versioned, provider-scoped

A **recipe** is a complete, deterministic description of how to scrape one provider: URL, cookie-banner strategy, form-field selectors and widget types, submit selector, and field-extraction strategies for every vehicle attribute. Recipes are discovered automatically by the LLM-driven builder (`run_build_recipe`); subsequent runs execute with **zero LLM calls** (`run_run_recipe`), which is what makes the daily pricing pipeline cheap.

**Why version recipes in the database instead of YAML files.**
- The website of a provider changes over time. When selectors become stale, the builder is re-run and the new recipe supersedes the old one. Keeping every past recipe in the DB gives an exact record of what configuration was active during each scrape run.
- A YAML file on disk has no audit trail, no FK linkage to `providers`, and no rollback mechanism.
- The recipe is global (catalog, not per-tenant), so it belongs in the shared catalog alongside `providers`.

**Append-versioning, no in-place UPDATE.**
Re-running the builder inserts a new row with `version = max(version) + 1` and `active = true`; the previous active row is simultaneously flipped to `active = false`. Historical rows are kept indefinitely for audit. Rollback = flip `active` flags in a back-office query.

**One active recipe per provider.**
Enforced by a partial unique index: `UNIQUE (provider_id) WHERE active = true`. At most one row satisfies the predicate per provider at any time.

**No tenant_id, no RLS.**
Recipes are operator-curated catalog data — the same recipe serves all tenants subscribed to a given provider. Same isolation boundary as `providers`.

**Recipe storage format: JSONB.**
The Python `Recipe` dataclass (domain model) is serialised to a JSONB column by the repository layer (`ProviderRecipeRepository`). The domain model is the source of truth; the JSONB is the persistence representation. No separate recipe-field tables — the whole recipe is one atomic document.

---

### 12. Deterministic ACRISS classification (engine v2)

Decision (2026-08-23): the free-form LLM classification described in Decision 1
is being replaced by a **deterministic classification engine** (spec agreed with
the operator): normalization → model dictionary → source overrides → ACRISS
rules → heuristic fallback, with per-letter confidence and alternatives. The
LLM (Gemini, env-pinned models) remains ONLY as a fallback resolver for
unknown models, and it returns a **structured semantic profile**
(likely category/type/powertrain), never the final ACRISS code — the code is
always built by the rule engine.

**Data artifacts (repo-versioned, like `acriss_codes.yaml`):**
- `data/acriss-models.json` — model dictionary (make/model/aliases, category,
  body type, powertrain profile, verification level).
- `data/acriss-source-overrides.json` — per-provider known mappings.
- `data/acriss-aliases.json` — normalization aliases.
Their content hashes join `classifier_version`, so editing any of them
invalidates the `model_classifications` cache coherently.

**Review queue lives in the DATABASE, not in a file** (`acriss_review_queue`,
catalog scope — see Part 2). Rationale: it is operational state produced by
scraper runs (which execute on the operator's machine) and consumed by
back-office review (which runs against the same DB the SaaS serves) — a repo
file cannot be shared between those two worlds, and the DB is the source of
truth for state. Promotion flow: the operator validates a queued model and
adds it to `data/acriss-models.json` in a commit; the queue row is then marked
`accepted` (never deleted, never auto-promoted — LLM suggestions do not write
the dictionary).

**`provider_vehicle_categories.classification_detail` (JSONB)** stores the full
engine output per group: per-letter code/confidence/source, alternatives,
assumptions, and the human-readable explanation. The existing aggregate
columns keep their meaning: `classification_confidence` = min() across the
four letters; `pending_review` = the engine's `needs_review`.

**Bundles.** Provider groups often list several models ("Audi A1, Ford Focus,
Opel Astra"). The engine classifies each member; the group is then priced to
the MOST EXPENSIVE member (unchanged business rule — see the mixed-group rule
in Decision 1). The premium-brand list may be used to rank members by expected
price; it must never be used to assign the category letter of a single model.

**Catalog extension policy (operator decision: extend, not project).** New
ACRISS codes (e.g. fuel `I` = plug-in hybrid, `D` = diesel) are materialized
**on demand**: a code is added to `acriss_codes.yaml` (never straight to SQL)
when a dictionary entry or a real provider group needs it, and applied with
`scripts/seed_acriss_codes.py`. Codes are never bulk-generated from the ACRISS
combinatorial space. **Body type `G` (crossover) IS materialized** (operator
decision 2026-08-23, superseding the earlier F-only rule recorded in the
`acriss_codes.yaml` header): a crossover is not an SUV, and the taxonomy
should say so. The cross-provider grouping concern that motivated F-only is
accepted as resolved by the product's pivot to **direct group-to-group
matching at the client** — ACRISS is now taxonomy + fallback, not the primary
matching path, so F/G splitting a segment across codes no longer breaks the
core flow. Per-provider source overrides may assign F or G per the engine
spec; the model dictionary is the default authority.

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
  password_hash TEXT NULL             -- bcrypt hash of the user's password.
                                      -- NULL = user cannot log in yet (e.g. created
                                      -- by onboarding before a password is set).
                                      -- The raw password is never stored.
  session_version INT NOT NULL DEFAULT 0
                                      -- bumped to invalidate all of this user's
                                      -- outstanding session JWTs (logout-everywhere,
                                      -- password change). The minted JWT carries the
                                      -- value; a mismatch on validation = 401.
  external_auth_id                    -- identity from external auth provider
                                      -- (the 'sub' claim of the JWT, or equivalent)
  role                                -- enum, default 'owner'
                                      -- MVP only uses 'owner'; expand when needed
  created_at
  ...
  -- Authentication is a minimal homegrown email + password flow (see
  -- "Authentication: email + password" below). `email` is the login identity
  -- (unique across all tenants — see uniqueness note). `external_auth_id` stays
  -- nullable, reserved for a future external IdP migration. Sessions are
  -- stateless JWTs in an httpOnly cookie carrying `session_version`; the only
  -- server-side session state is that integer counter.
  --
  -- Email uniqueness: a UNIQUE index on LOWER(email) is enforced GLOBALLY (not
  -- per tenant), because password login resolves an email to exactly one user
  -- without a tenant context. The legacy per-tenant unique (tenant_id, email)
  -- is kept too.

login_tokens
  id UUID PK
  email                              -- requested email (stored even if no user
                                     -- matches, so rate-limiting works pre-auth)
  user_id UUID NULL FK → users       -- resolved when the email maps to a user
  token_hash CHAR(64) UNIQUE         -- SHA-256 hex of a ≥256-bit random token;
                                     -- the raw token is never stored
  request_ip                         -- for per-IP rate limiting
  created_at
  expires_at                         -- created_at + 15 min
  used_at NULL                       -- set on first successful verify (single use)
  -- RETAINED, currently unused by the login flow. Kept as the natural
  -- substrate for a future "password reset by email" flow (and for the
  -- per-email / per-IP rate-limit accounting that the login endpoint reuses).
  -- NO RLS: this table is pre-auth (no tenant context exists). It is touched
  -- ONLY by the auth service, which runs as smart_rental_super (owner-inherited
  -- + BYPASSRLS). The app role has no grants on it.

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

  -- The cross-tariff client stores ONE active rule per tenant with
  -- acriss_code = NULL (a whole-config rule). Per-category overrides live
  -- inside formula_jsonb, not as separate rows (versioning is of the complete
  -- configuration; see MILESTONES D5). Canonical formula_jsonb shape written by
  -- PUT /api/pricing-config and read by GET /api/cross-tariff:
  --   {
  --     "providers": ["centauro", ...],          -- providers in the radar
  --     "base_aggregation": "min|med|avg|max",
  --     "master_provider": "centauro",           -- whose calendar drives zones
  --     "rounding": "0|0.99|0.90|0.50|1",
  --     "global_rule":  {op:"sub|add", val:>=0, mode:"pct|abs",
  --                      floor:"auto|cost|none", ceiling:"max|none"},
  --     "category_overrides": { "CFAR": {<same shape as global_rule>}, ... },
  --     "muted_categories": ["CFAR", ...]        -- ACRISS codes the tenant
  --                                              -- silenced: shown dimmed and
  --                                              -- last in the grid, excluded
  --                                              -- from CSV/PDF exports
  --   }
  -- The column-level floor/ceiling stay NULL for these rules (the modes live
  -- inside the JSON). Live edits in the front use POST /api/cross-tariff/preview
  -- (same body, not persisted); Save issues the PUT.

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

acriss_review_queue                    -- catalog scope (no tenant_id, no RLS)
  id BIGSERIAL PK
  normalized_model TEXT NOT NULL UNIQUE -- engine's normalized "make model" key
  raw_model        TEXT NOT NULL        -- as first scraped (kept verbatim)
  suggested_category  CHAR(1) NULL      -- engine/LLM best estimate (semantic, not final)
  suggested_type      CHAR(1) NULL
  suggested_powertrain VARCHAR(16) NULL -- powertrain_profile mode (ice_only, bev_only, mixed…)
  suggested_acriss    VARCHAR(4) NULL   -- engine-built best estimate, informational
  confidence       NUMERIC(4,3) NULL
  reason           TEXT NULL            -- heuristic/LLM rationale
  sources_seen     JSONB NOT NULL DEFAULT '[]'  -- provider codes that surfaced it
  status           VARCHAR(16) NOT NULL DEFAULT 'pending_review'
                                        -- 'pending_review' | 'accepted' | 'rejected'
  first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
  last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
  -- Unknown models found during classification land here (upsert on
  -- normalized_model; sources_seen accumulates). Rows are never auto-promoted:
  -- the operator validates and adds the entry to data/acriss-models.json in a
  -- commit, then marks the row 'accepted'. See Decision 12.

providers
  id PK
  code                                -- 'provider_a', 'victoria', 'solcar', 'centauro', ...
  display_name
  scraper_key                         -- maps to SCRAPER_REGISTRY
  default_currency CHAR(3) NOT NULL
  status                              -- 'active' | 'beta' | 'deprecated' | 'broken'
  created_at

### Canonical market locations (catalog)

Providers each have their own offices (`provider_locations`, with the provider's own
naming — the literal text the RecipeScraper types into the provider's search form).
What the product compares is a MARKET: the canonical location where provider offices
compete. The two concepts are deliberately separate:

- **The scraper text belongs to the provider.** `provider_locations` keeps the
  provider's naming untouched; scraping is unaffected by this feature.
- **The canonical market is ours**, and the mapping is a manual operator action
  (SQL or onboarding CLI). A new provider_location discovered by the scraper is born
  unmapped (`location_id NULL`) and does NOT appear in market-filtered views until
  an operator maps it. With a handful of providers this is trivial; revisit (text
  similarity suggester) only if provider count grows by an order of magnitude.

locations                            -- catalog, no tenant_id, no RLS
  id PK
  code            -- stable slug, e.g. "alc-airport" (used in URLs/API)
  name            -- display name, e.g. "Alicante · Aeropuerto"
  created_at

provider_locations
  id PK
  provider_id FK
  location_code                       -- 'ALC', 'MAD', ...
  location_name
  country
  city
  active
  location_id FK → locations NULL     -- n:1 mapping to the canonical market;
                                      -- NULL = unmapped (excluded from filtered views)

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
  classification_confidence FLOAT NULL                     -- min() across the 4 letters (0..1)
  pending_review BOOLEAN NOT NULL DEFAULT false            -- operator attention required
  classification_detail JSONB NULL                         -- engine v2 full output: per-letter
                                                           -- code/confidence/source, alternatives,
                                                           -- assumptions, explanation (Decision 12)
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

provider_recipes
  id BIGSERIAL PK
  provider_id FK → providers          -- catalog scope (no tenant_id)
  version INT NOT NULL                 -- monotonically increasing per provider
  recipe_jsonb JSONB NOT NULL          -- complete Recipe document (domain → JSONB)
  discovered_at TIMESTAMPTZ NULL       -- timestamp from the discovery scrape
  active BOOLEAN NOT NULL DEFAULT false
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  -- Partial unique index: UNIQUE (provider_id) WHERE active = true
  -- Invariant: at most one active recipe per provider at any time.
  -- Append-versioning: re-run builder → new row version N+1 active=true,
  --   previous row set active=false atomically in the same transaction.

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
  reference_price NUMERIC(10,2) NULL  -- guide-group price/day that defined the zone
  detected_at
  active BOOLEAN
  -- Partial index: WHERE active = true
  --
  -- Season-start stability (carry-forward): the scrape window starts at
  -- today+PERIOD_OFFSET, so a re-scrape would otherwise re-anchor the FIRST zone's
  -- start_date to the moving window edge — making the same season appear to "start"
  -- a day later each run. To keep season identity stable, on persist the new leading
  -- zone inherits the previously-active leading zone's start_date when their
  -- reference_price matches within SEASON_PRICE_THRESHOLD (same relative test the
  -- analyzer uses for boundaries). reference_price is persisted for this comparison
  -- (and makes a zone self-describing for history). Implemented in
  -- smart_orchestrator (carry_forward_leading_start) + HomogeneousZoneRepository.
  --
  -- Derivation rule (read side): a zone is backed by the observation at its
  -- representative_date; if that exact observation is missing (the chosen
  -- representative was not captured — e.g. no availability for that pickup date,
  -- or the zone was re-analysed onto a date the extractor did not hit), fall back
  -- to the CLOSEST observation WITHIN [start_date, end_date]. A homogeneous zone
  -- is ~flat in price by construction (±SEASON_PRICE_THRESHOLD), so any in-range
  -- observation is representative. NEVER fall back to an observation outside the
  -- zone — that belongs to another season and would be a wrong price. A zone with
  -- no in-range observation at all stays empty (surfaced, not silently dropped).
  -- Implemented in cross_tariff_read.fetch_cross_tariff_dataframe (obs_in_zone CTE).

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
- Translates `acriss_code` to the tenant's label if applicable (via `tenant_vehicle_group_mappings`).
- If the tenant maps multiple ACRISS codes to a single tenant_vehicle_group, applies a second-level aggregation (default: `min` again) across them.
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

### PricingRule: canonical_type_id → acriss_code (ORM/migration debt, scheduled)

The `pricing_rules` and `pricing_outputs` tables were created by migration
`80486ab5bff3_create_tenant_tables_with_rls.py` using a pre-ACRISS column
`canonical_type_id INTEGER` that referenced the now-deprecated custom taxonomy.
The pseudo-DDL in Part 2 of this document already specifies the correct target
schema (`acriss_code VARCHAR(4) FK → acriss_codes.code`), but the actual ORM
models (`PricingRule` and `PricingOutput` in
`src/saas/infrastructure/persistence/models/tenant.py`) still carry
`canonical_type_id: Mapped[int]`. No repository exists yet for either model.

**Gap to close:**

1. Alembic migration: rename/replace `canonical_type_id` with
   `acriss_code VARCHAR(4) NOT NULL FK → acriss_codes.code` on both tables.
2. ORM update: replace `canonical_type_id: Mapped[int]` with
   `acriss_code: Mapped[str] = mapped_column(String(4), ForeignKey(...))`.
3. Repository: create `PricingRuleRepository` (at minimum: `create`, `get_active`,
   `get_history_for_code`).
4. Tenant isolation test: verify that `pricing_rules` RLS isolates across tenants.

**Trigger / scheduled.** This debt is saldada in the "Tarifa cruzada y
persistencia de configuración de pricing" milestone. Until then, `pricing_rules`
and `pricing_outputs` are structurally present in the DB but not used by any
application code.

---

### Authentication: email + password

**Decision (supersedes the earlier "magic link" stance, which itself reversed the
"external IdP, deferred" stance).** For the MVP, authentication is a small
homegrown **email + password** flow. No external identity provider. The earlier
passwordless magic-link flow was replaced because the first customers want a
conventional credential login and a 30-day "remember me" session.
`users.external_auth_id` remains in the schema, nullable, so a later migration to
an external IdP needs no destructive change.

**Flow.**
1. `POST /api/auth/login {email, password}` — resolve the email to a single user
   (global `LOWER(email)` uniqueness), verify the password against
   `users.password_hash` with bcrypt. On success mint a session **JWT**
   (`sub`=user_id, `tenant_id`, `email`, `sv`=session_version, **30-day** exp)
   signed with `JWT_SECRET`, set it in an **httpOnly + Secure + SameSite=Lax**
   cookie, and return `200`. On failure return a generic `401` (no distinction
   between "unknown email" and "wrong password" → no account enumeration).
2. `GET /api/auth/me` — `{email, tenant_name}` when the cookie is valid, else `401`.
3. `POST /api/auth/logout` — clear the cookie.

**Session invalidation.** The JWT carries `sv` (the user's `session_version`).
On every authenticated request the value is compared against the current
`users.session_version`; a mismatch is a `401`. Bumping `session_version`
(password change, "log out everywhere", or incident response) invalidates every
outstanding token for that one user without rotating the global `JWT_SECRET`.

**Non-negotiable security requirements.**
- Passwords are stored only as a bcrypt hash; the raw password is never persisted
  or logged.
- `login` is generic-failure: the same `401` whether the email is unknown or the
  password is wrong.
- Rate limiting per email and per IP on `login` (windowed count; reuses the
  `login_tokens` accounting substrate) to blunt credential stuffing / brute force.
- Session cookie is httpOnly + SameSite=Lax; `Secure` is on whenever
  `APP_ENV != development` (kept off only for the local http loop).
- `JWT_SECRET` must be a strong random secret in any non-development environment.

**Dev bypass.** `get_current_tenant` accepts a `DEV_TENANT_ID` env fallback **only
when `APP_ENV=development`** and there is no session cookie, so the API is usable
in local development without logging in. In production `APP_ENV` is set and
`DEV_TENANT_ID` must be absent — the bypass is then dead code. `/api/auth/me` never
honours the bypass: it reflects only a real session.

**`login_tokens`** is retained but no longer part of the login path — see its
schema note. It is the intended substrate for a future "password reset by email"
flow and backs the login rate-limit window.

**Trigger to revisit (external IdP).** Enterprise SSO/SAML demand, or the first
customer requiring it. The migration path is: populate `users.external_auth_id`,
swap the cookie-JWT issuer for the IdP's, retire the password columns.

- `users.role` exists from day one with `'owner'` as default. Granular roles wait until a real customer demands them.

### Machine-to-machine authentication (API keys)

**Implemented** (migration `r0s1t2u3v4w5`). Backs the public prices API
(`GET /api/v1/prices`) so a customer's own system can pull its configured prices
automatically. Tenant-scoped, RLS-forced (mirrors `users`).

```
api_keys
  id UUID PK
  tenant_id UUID FK            -- keys belong to the tenant, not the user
  name TEXT                    -- human label
  key_prefix VARCHAR(16)       -- visible leading chars, for UI identification
  key_hash TEXT                -- sha256 of the raw key; the raw is shown once, never stored
  created_at TIMESTAMPTZ
  last_used_at TIMESTAMPTZ NULL -- bumped on each successful auth
  revoked_at TIMESTAMPTZ NULL   -- set to revoke without deleting the audit row
```
Unique index on `key_hash` (a hash resolves to exactly one tenant with no tenant
context). Auth flow: present `Authorization: Bearer <key>`; the server hashes it
and looks it up **as `smart_rental_super` (BYPASSRLS)** because the request has no
`app.tenant_id` yet (see `api/dependencies.get_tenant_from_api_key`). Keys are
created/revoked via `scripts/create_api_key.py`.

The key principle: API keys are tenant-scoped, not user-scoped. They survive user deactivation.

**Deliberately omitted from the pre-decided shape (add when a need appears):**
`scopes` (the only capability today is read-only prices), `expires_at` (lifecycle
is covered by `revoked_at`), and `created_by_user_id` (keys are provisioned via the
back-office script, not by an end user). Hashing uses sha256 rather than bcrypt
because the key is a high-entropy random token, not a low-entropy password.

### Ad-hoc classification cache (`model_classifications`)

**Implemented** (migration `t2u3v4w5x6y7`). Read-through cache for the `GET
/api/v1/classify?model=…` endpoint, which classifies a free-text model to an
ACRISS code via the LLM. Catalog scope (no `tenant_id`, no RLS — a model
classifies the same for everyone).

```
model_classifications
  normalized_model   TEXT   -- input lowercased + whitespace-collapsed
  classifier_version TEXT   -- sha1(acriss_codes.yaml)[:12] + ":" + PROMPT_VERSION
  acriss_code        TEXT NULL  -- NULL = unclassifiable
  confidence         NUMERIC(4,3)
  pending_review     BOOLEAN
  created_at, last_used_at, hit_count
  PK (normalized_model, classifier_version)
```
The LLM is hit only on a cache miss. `classifier_version` is part of the key, so a
catalog change (the YAML hash moves) or a prompt change (bump `PROMPT_VERSION` in
`gemini_service`) makes old rows stop matching — stale classifications are never
served and the cache re-fills lazily. The scraper's own PVC-level reuse cache is
separate and not reusable here (its rows are provider *group* bundles whose code
follows group rules, not single-model mappings).

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
- Authentication: see the "email + password" section above. Passwords are bcrypt-hashed in `users.password_hash`; sessions are stateless JWTs carrying `session_version`, the only server-side session state.
- Tests for tenant isolation are part of the definition of "API done", not an optional nicety.
- The LLM-based classification is wrapped behind an abstract `ClassificationService` interface. The interface must not leak provider-specific concepts (request shape, response shape, authentication). Implementations live in infrastructure; the rest of the system depends only on the interface. Confidence threshold (0.85) is hardcoded in the service composition.
- Classification is **batch by provider**, not vehicle-by-vehicle. The classifier receives the complete provider catalog at once together with each group's representative 7-day price, so it can reason about the provider's internal pricing hierarchy. Calling the classifier with a single vehicle in isolation is supported by the interface but is **not** the production path — it loses the hierarchical context.
- The representative 7-day price passed to the classifier is computed as the **mean of all 7-day prices observed during the probe phase** for that group. It is transient (used only as classifier input, not persisted). The true price history is in `price_observations`.
- `provider_vehicle_categories` identity is `(provider, location, rate, external_code)` when `external_code` is non-null, or `(provider, location, rate, attributes_hash)` otherwise. `acriss_code` is **not** part of identity; multiple rows of the same provider may share it, by design (Decision 1).
- `acriss_codes.yaml` is the source of truth. `scripts/seed_acriss_codes.py` must be idempotent: running it twice on an unchanged YAML produces zero changes in DB. Running it after a YAML edit applies only the deltas.
