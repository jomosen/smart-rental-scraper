# Scraping optimizations — deferred to v2+

> Optimizations identified during MVP design but **deliberately not implemented now**.
> Each one solves a specific scaling problem that does not exist yet at MVP scale.
> Implement only when triggered by the conditions described.

---

## Context

The current pipeline (`SeasonProbe` → `SeasonAnalyzer` → extraction → persistence) was designed for a 90-day period with 2 providers. Synthetic days within a zone are derived at read time, not produced by an explicit expansion step. It scales better than linearly when extending the period because the number of homogeneous zones grows much slower than the number of days.

Concrete estimate for the current pipeline:

| Period | Probe searches (per provider) | Extraction searches (per provider, ~zones × durations) | Total per scrape (2 providers) |
|---|---|---|---|
| 90 days | ~13 (weekly) | ~3 zones × 9 = 27 | ~80 |
| 365 days | ~52 (weekly) | ~6 zones × 9 = 54 | ~210 |

At 8s per refined search, 365 days ≈ 28 minutes per daily run with 2 providers in parallel. Manageable.

The optimizations below are only worth implementing **when the current pipeline starts hurting**, not preemptively.

---

## 1. Adaptive probe

### The problem (when it appears)

`SeasonProbe` currently does fixed weekly searches across the full period. At 365 days that's 52 probes per provider, regardless of whether the season landscape is simple (e.g. one summer peak) or complex (multiple holidays, local events, school breaks).

Most of those probes are wasted: when prices are flat across 6 consecutive weeks, you're confirming flatness 6 times. The information gain per probe drops sharply once the gross structure of the seasons is known.

### The proposed approach

Replace uniform weekly probing with **adaptive probing**, modeled as a discontinuity-search algorithm:

1. **Coarse pass.** Probe at wide intervals (every 3–4 weeks) across the full period. Cheap. Establishes the gross price landscape.
2. **Refinement pass.** For each pair of consecutive coarse probes whose price difference exceeds `SEASON_PRICE_THRESHOLD`, insert additional probes between them — bisection-style — until the boundary is localized within ±N days (N configurable, e.g. 3 days).
3. **Stop condition.** No coarse pair shows a meaningful gap, or all gaps are localized within tolerance.

For typical rent-a-car season landscapes (3–8 zones across 365 days), this brings probe count from ~52 down to ~15–20 per provider — a 60–70% reduction in the most expensive phase.

### When to implement

Trigger any of:

- A scrape run takes >60 minutes per provider.
- Provider rate-limits or anti-bot defenses make probe count a constraint.
- Period is extended beyond 365 days (multi-year monitoring).

### What it doesn't change

- The output of the probe phase (zone boundaries) is identical in shape.
- `SeasonAnalyzer`, extraction, and expansion are untouched.
- Domain and application layers don't change. Only `SeasonProbe` is replaced.

This is a drop-in optimization behind the existing `ISeasonProbe` interface.

---

## 2. Frequency-decreasing scrape strategy

### The problem (when it appears)

A daily scrape across 365 days assumes all days are equally important. They're not.

For pricing decisions, what matters is the **near future**, where the customer's typical lead time falls. In rent-a-car, booking lead times typically cluster in the 14–60 day window. Prices for pickup dates 8 months out:

- Change less frequently (most providers haven't even set them yet, or use placeholder values).
- Are less reliable as competitive signal (some providers extrapolate from current season).
- Are scraped daily for no real benefit.

Running daily scrapes on the full period multiplies cost without proportional value.

### The proposed approach

Replace the single uniform scrape frequency with a **layered scrape strategy**, where update frequency decreases with how far the pickup date is from today:

| Layer | Pickup date range | Frequency | Rationale |
|---|---|---|---|
| Hot | Today + 0 to +30 days | Daily | Highest decision value. Prices move. |
| Warm | +31 to +90 days | Every 2–3 days | Medium decision value. Some movement. |
| Cold | +91 to +365 days | Weekly | Low decision value. Mostly stable or placeholder. |

Implementation-wise:

- The `SearchPlanBuilder` partitions the period into layers based on age relative to `today`.
- Each layer has its own freshness SLA stored against the (zone, duration) pair.
- The scheduler triggers extraction only for points whose freshness has expired.
- Probe runs less often than extraction (e.g. weekly), since season boundaries shift slowly.

Layer thresholds should be **per-tenant config**, because lead time distribution varies by client market.

### When to implement

Trigger any of:

- Daily full-period scrape becomes impractical (cost, time, provider pressure).
- A client explicitly asks for higher frequency on near-term dates.
- Provider rate-limits force prioritization.

### What it doesn't change

- Data model. `price_observations` already has `observed_at`; layered freshness is a scheduling concern, not a schema concern.
- Domain logic. `HomogeneousZone` and `PricePoint` are unchanged.
- Pricing engine consumers. They query "latest price for (group, pickup_date, duration)" exactly as before; the layer model only affects how often that "latest" gets refreshed.

This optimization lives entirely in the scheduler / `SearchPlanBuilder` layer.

---

## 3. Duration-linearity detection (skip 14/21/28-day searches)

### The observation

Some providers price long durations as an exact linear multiple of their 7-day
price: `total(14d) = 2 × total(7d)`, etc. For those providers, the 14/21/28-day
extraction searches (3 of the 10 durations — ~30% of extraction volume) return
information already contained in the 7-day point, and could be **derived at
read time instead of searched**.

### Empirical status (2026-08-23, dev observations)

Measured as `total(Nd) / (total(7d) × N/7)` over all (group, pickup_date) pairs:

| provider | avg ratio 14/21/28d | exact multiples |
|---|---|---|
| victoria | 0.987 / 0.986 / 0.971 | ~80% / ~80% / ~65% |
| solcar   | 1.020 / 1.021 / 1.013 | ~4% |
| centauro | 1.051 / 1.113 / 1.144 | ~1% |

**Only victoria is quasi-linear, and imperfectly.** A blanket multiply-by-N
derivation would be wrong for most providers and wrong ~20% of the time even
for victoria. Any implementation must be per-(provider, rate), evidence-based,
and continuously revalidated — never assumed.

### The proposed approach

1. **Detection.** During normal extraction, keep searching the full duration
   set. After K consecutive representative dates where every long-duration
   total is within ε (e.g. ±1%) of the linear extrapolation from 7d, set a
   flag on `provider_rates`: `duration_pricing = 'linear_beyond_7d'`.
2. **Skipping.** `SearchPlanBuilder` omits 14/21/28-day searches for flagged
   rates. The values are **derived at read time** (future `PriceQueryService`),
   like synthetic in-zone days — NEVER persisted as observations
   (synthetic-vs-real is the invariant this repo protects hardest).
3. **Revalidation.** Each run still performs one long-duration spot-check per
   flagged rate. If it deviates beyond ε, clear the flag — the next run
   searches the full set again.

### Caveats that make this riskier than it looks

- **Cross-season bookings.** A 28-day rental spanning a zone boundary may be
  priced by the provider against both seasons; linear extrapolation from the
  starting zone's 7-day price diverges exactly there.
- **Long-duration products.** Some providers switch product beyond a duration
  threshold (e.g. recordgo's FlexRent renting kicks in at 15+ days) — long
  durations are structurally different prices, not multiples.
- The ~30% search saving applies to extraction only; probe (7-day searches)
  is unaffected.

### When to implement

Same triggers as #1/#2 (run duration, provider pressure), **plus** the
detection data showing a provider actually holds linearity ≥99% of points over
a sustained window. With current data, no provider qualifies.

---

## Combined effect

The optimizations are independent and compose (#3 adds up to ~30% off
extraction volume, but only for providers whose data proves linearity):

- Adaptive probe reduces the **per-run** cost.
- Layered frequency reduces the **per-day** cost across runs.

A 365-day daily scrape at MVP would be ~210 searches/day. With both optimizations, the same coverage drops to roughly:

- Probe: ~15 searches, run weekly → ~2/day amortized.
- Extraction: ~54 points, but only the 30-day hot layer (≈ 9 points × 9 durations / number of zones in that range) refreshed daily, others refreshed at lower cadence → ~30–40/day amortized.

**Estimated steady-state cost: 30–50 searches/day per provider** vs. 210/day naive. ~5x improvement.

These numbers are illustrative. Validate with real measurements before sizing infrastructure around them.

---

## What NOT to do preemptively

Resist the temptation to:

- **Implement both optimizations before they're needed.** The current pipeline handles 365 days at daily frequency in under 30 minutes. That's not a problem yet.
- **Cache scrape results across tenants without first confirming the global-observations model.** This is already decided (see `ROADMAP_ARCHITECTURE.md` — observations are global per `(provider, location, rate)` tuple), but the optimizations above assume that decision holds.
- **Add ML-based "predict when prices change" logic.** That's a v3+ conversation. Adaptive probe and layered frequency are deterministic and explainable; keep them that way until there's clear evidence rule-based scheduling is insufficient.
