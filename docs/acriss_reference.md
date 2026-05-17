## ACRISS Code Reference (for LLM classification)

ACRISS is the international standard used by car rental industry to classify
vehicles. Every code is exactly 4 characters, one per dimension:

  Position 1 = Category    (size/tier)
  Position 2 = Body Type   (shape of the vehicle)
  Position 3 = Transmission (manual, auto, drivetrain)
  Position 4 = Fuel/Air    (engine type and air conditioning)

Example: CFAR = Compact SUV, Automatic, Combustion + Air

---

### POSITION 1 — CATEGORY (size/tier)

ACRISS uses pairs of letters to distinguish mainstream tier vs "Elite" tier
within each size class. "Elite" means a premium-brand vehicle of the same size
as its mainstream counterpart (e.g. Audi A1 is Elite of Compact size).

Mainstream / Elite pairs (Elite = premium-brand version of same size):

  M = Mini              N = Mini Elite          (rare, tiny city cars)
  E = Economy           H = Economy Elite       (sub-compact, ~3.5-3.9m)
  C = Compact           D = Compact Elite       (compact, ~4.0-4.3m)
  I = Intermediate      J = Intermediate Elite  (mid-size, ~4.3-4.6m)
  S = Standard          R = Standard Elite      (full-size, ~4.6-5.0m)
  F = Fullsize          G = Fullsize Elite      (extra-large)
  P = Premium           U = Premium Elite       (premium sedan tier)
  L = Luxury            W = Luxury Elite        (top luxury)
  O = Oversize                                  (Hummer-style)
  X = Special                                   (anything not fitting)

KEY INSIGHT about Elite:
- "Elite" is NOT about being more luxurious within a brand.
- "Elite" identifies a premium-brand vehicle of equivalent SIZE to its
  mainstream counterpart.
- Example: A VW Polo (mainstream compact, 4.05m) = C.
  Audi A1 (premium-brand compact, 4.03m) = D (Compact Elite).
- Example: Mercedes GLA (premium-brand compact SUV, 4.41m) = D
  (Compact Elite). The VW T-Roc (mainstream compact SUV, 4.23m) = C.
- Example: Mercedes GLC (premium-brand mid-large SUV, 4.66m) = R
  (Standard Elite). The Ford Kuga (mainstream mid SUV, 4.62m) = I or S.

PASSENGER VAN special category encoding (1st char varies by seat count):

  IV = 6+ seats         JV = Elite 6+ seats or 5+2
  SV = 7+ seats         RV = Elite 7+ seats
  FV = 7+ seats + space GV = Elite 7+ seats + space
  PV = 8+ seats         UV = Elite 8+ seats
  LV = 9+ seats         WV = Elite 9+ seats
  XV = 12+ seats        OV = 15+ seats

So a Mercedes Vito Tourer 9 plazas = WV (premium-brand 9+ seats van) +
A (automatic) + R (combustion) = WVAR.
A Renault Trafic Passenger 9 plazas = LV (mainstream 9+ seats) +
M (manual) + R (combustion) = LVMR.

---

### POSITION 2 — BODY TYPE

  B = 2-3 door (small hatchback or 2-door)
  C = 2/4 door
  D = 4-5 door (standard sedan/hatchback) — the most common
  W = Wagon/Estate (kombi/estate body)
  V = Passenger Van (multi-row passenger transport)
  L = Limousine/Sedan (in 2018, expanded to include sedan cars)
  S = Sport
  T = Convertible / Cabrio
  F = SUV (sport utility vehicle, real SUV body)
  J = Open-air All Terrain (jeep-style)
  X = Special
  P = Pick-up single/extended cab 2 door
  Q = Pick-up double cab 4 door
  Z = Special Offer
  E = Coupe (2-door coupe)
  M = Monospace / MPV (people-carrier, NOT van)
  R = Recreational Vehicle
  H = Motor Home
  Y = 2-Wheel Vehicle (motorcycle / scooter)
  N = Roadster
  G = Crossover (urban SUV, soft-roader)
  K = Commercial Van/Truck (cargo, NOT passenger)

KEY DISTINCTIONS:
- F (SUV) vs G (Crossover): F is a real SUV body (Tiguan, Tucson).
  G is an urban crossover with raised driving position but soft body
  (Captur, T-Roc, Kona).
- V (Passenger Van) vs M (MPV): V is a true van with tall roof and
  passenger-focused seats (Mercedes Vito Tourer, Renault Trafic Passenger).
  M is a monospace/MPV (people-carrier with car-like proportions, e.g.
  BMW Serie 2 Gran Tourer, Mercedes Clase B).
- V (Passenger Van) vs K (Commercial Van): V is for passengers; K is cargo.
- D (4-5 door) vs W (Wagon): D is hatchback/sedan; W is estate body.
- D vs E (Coupe): E is 2-door coupe (sport, performance positioning).
- Y (2-Wheeler): use only for motorcycles and scooters.

---

### POSITION 3 — TRANSMISSION / DRIVE

Manual options:
  M = Manual (FWD or RWD, no 4WD)
  N = Manual 4WD
  C = Manual AWD

Auto options:
  A = Auto (FWD or RWD, no 4WD)
  B = Auto 4WD
  D = Auto AWD

Electric:
  E = Electric

KEY RULE for our market:
- Most rental cars use M or A. 4WD/AWD codes (N, C, B, D) are reserved
  for vehicles where 4x4/AWD is the defining feature (real off-roaders,
  premium-brand AWD SUVs marketed as such).
- A Mercedes GLC 4Matic Coupe might be A (auto, as the default) or D
  (auto AWD) depending on how the provider lists it. If the provider
  explicitly says "4Matic" or "AWD", use D. Otherwise default to A.
- Same applies to manual 4WD: use N only if the vehicle is sold as a
  real off-roader (Jeep Wrangler, Toyota Land Cruiser); otherwise M.

---

### POSITION 4 — FUEL / AIR

Default (combustion, regular):
  R = Unspecified Fuel/Power Combustion Engine + Air conditioning
  N = Same but no air conditioning (rare in our market)

Diesel:
  D = Diesel + Air
  Q = Diesel no air

Petrol explicit:
  V = Petrol + Air
  Z = Petrol no air

Alternative fuels:
  H = Hybrid + Air
  I = Hybrid Plug-in + Air
  E = Electric + Air
  C = Electric (without specifying air)

  L = LPG/Compressed Gas + Air
  S = LPG/Compressed Gas no Air
  A = Hydrogen + Air
  B = Hydrogen no Air
  M = Multi Fuel/Power + Air
  F = Multi Fuel/Power no Air
  U = Ethanol + Air
  X = Ethanol no Air

KEY RULE for our market:
- DEFAULT to R (Combustion + Air) when fuel is not explicitly mentioned.
  This is the ACRISS default for rentals.
- Use H (Hybrid + Air) only if the provider explicitly says "Hybrid",
  "Híbrido", or the model name explicitly indicates hybrid (e.g. "Fiat
  Panda Hybrid", "Kia XCeed Hybrid", "Toyota Yaris Hybrid").
- Use I (Plug-in Hybrid) only if "Plug-in" or "PHEV" is mentioned.
- Use E or C (Electric) only if the vehicle is fully electric (Tesla,
  EVs, etc.).
- Do not infer fuel from brand alone (e.g. Toyota does not always mean
  hybrid). Look at the actual model name and provider listing.

---

### KEY RULES FOR CLASSIFICATION

1. The output `acriss_code` MUST exist in the list of materialized codes
   provided in the next section. Do not invent codes that are not in the
   materialized catalog.

2. If you cannot find a materialized code that fits the vehicle, return
   `acriss_code: null` and set `pending_review: true`. Do not force a
   poor fit.

3. The 4 attributes you return MUST be the 4 characters of the chosen
   `acriss_code`, in order. Internal consistency is required.

4. When the provider groups multiple distinct models in one PVC, classify
   based on the dominant/representative model. If there is no clear
   dominant model and the models span multiple ACRISS categories, return
   pending_review.

5. Confidence calibration:
   - 0.95+: clear match, all attributes unambiguous
   - 0.85-0.95: clear match, one attribute slightly uncertain (e.g. fuel
     not specified but defaulted to R)
   - 0.70-0.85: ambiguous on 1-2 attributes
   - <0.70: poor fit; consider pending_review
