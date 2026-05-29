## ACRISS Code Reference (for LLM classification)

ACRISS is the international standard used by car rental industry to classify
vehicles. Every code is exactly 4 characters, one per dimension:

  Position 1 = Category    (size/tier)
  Position 2 = Body Type   (shape of the vehicle)
  Position 3 = Transmission (manual, auto, drivetrain)
  Position 4 = Fuel/Air    (engine type and air conditioning)

Example: CFAR = Compact SUV, Automatic, Combustion + Air

This reference is derived from the official ACRISS Expanded Matrix and Car
Type Definitions (https://acriss.org/car-codes/expanded-matrix/).

---

### POSITION 1 — CATEGORY (size/tier)

ACRISS uses pairs of letters to distinguish mainstream tier vs "Elite" tier
within each size class. "Elite" identifies a category of vehicle that is
superior to another of equal body size — the difference can be price,
engine size, performance, fixtures, features, or any combination of these.

In practice, Elite usually means premium-brand (e.g. BMW, Audi, Mercedes,
Lexus, Volvo) of equivalent size to its mainstream counterpart.

Mainstream / Elite pairs:

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
- "Elite" identifies a premium-brand or higher-spec vehicle of equivalent
  SIZE to its mainstream counterpart.
- VW Polo (mainstream compact, 4.05m) = C ; Audi A1 (premium-brand
  compact, 4.03m) = D (Compact Elite).
- Mercedes GLA (premium-brand compact SUV, 4.41m) = D (Compact Elite);
  VW T-Roc (mainstream compact SUV, 4.23m) = C.
- Mercedes GLC (premium-brand mid-large SUV, 4.66m) = R (Standard Elite);
  Ford Kuga (mainstream mid SUV, 4.62m) = I or S.

REFERENCE MODELS BY CATEGORY (from ACRISS Spanish Selling Guide, European
fleet):

  Mini (M):              Fiat 500 (MBMR/MBMH if hybrid)
  Mini Elite EV (N):     Honda e (NBAE)
  Economy (E):           Peugeot 208 (ECMR)
  Economy Elite (H):     Audi A1 (HDMR)
  Compact (C):           Ford Focus (CDMR)
  Compact Elite (D):     Mercedes A-Class, GLA, GLB (DDMR, DFAR)
  Intermediate (I):      Hyundai Ioniq EV, VW Tiguan (IDAE, IFAR)
  Intermediate Elite (J):Mercedes CLA, BMW Serie 2 Gran Tourer (JDAR, JMAR)
  Standard (S):          Peugeot 508, VW Passat (SDMR, SDAR)
  Standard Elite (R):    Mercedes GLC, Audi Q4 e-tron (RFAR, RFAE)
  Fullsize (F):          Skoda Superb (FDAR)
  Fullsize Elite (G):    Alfa Romeo Stelvio (GFAR)
  Premium (P):           Mercedes C-Class (PDAR)
  Premium Elite (U):     BMW i4 (UDAE)
  Luxury (L):            Audi A6 (LDAR)
  Luxury Elite (W):      Audi A7 (WDAR)
  Special (X):           Mercedes S-Class, Range Rover Vogue (XDAR, XFAR)

PASSENGER VAN SPECIAL CATEGORY ENCODING (1st character varies by seat count
when 2nd character is V):

  IV = 6+ seats              JV = Elite 6+ seats or 5+2 seats
  SV = 7+ seats              RV = Elite 7+ seats
  FV = 7+ seats + more space GV = Elite 7+ seats + more space
  PV = 8+ seats              UV = Elite 8+ seats
  LV = 9+ seats              WV = Elite 9+ seats
  XV = 12+ seats             OV = 15+ seats

Examples:
- Mercedes Vito Tourer 9 plazas = WV (Elite 9+ seats) + A (auto) +
  R (combustion) = WVAR.
- Renault Trafic Passenger 9 plazas = LV (mainstream 9+) + M (manual) +
  R (combustion) = LVMR.
- BMW Serie 2 Gran Tourer (5+2 seats, Elite brand) → could be JV when
  encoded as van-passenger, but its body type is M (Monospace, 5 seats
  + extra headroom + 2 jump seats), so JMAR is the typical code.

---

### POSITION 2 — BODY TYPE

Official definitions (ACRISS Expanded Matrix, Dec 2019, updated 2025):

  B = 2-3 Door car
  C = 2-4 Door car
  D = 4-5 Door car (the most common — standard sedan or hatchback)
  W = Wagon/Estate (estate derivative of a C or D-type car)
  V = Passenger Van (multi-passenger vehicle with 6+ seats; combined with
      van-passenger coding above)
  L = Limousine/Sedan (specially extended luxury cars OR sedan in markets
      where "Limousine" means sedan; expanded since 2018)
  S = Sport (sports car with more powerful engine)
  T = Convertible (cars with open roof, usually 4 seats)
  F = SUV (sport utility vehicle; family vehicle usually with 4WD or
      on/off-road capability — but not guaranteed; can also be FWD)
  J = Convertible SUV (SUV with open roof)
  X = Special (doesn't fit other groups)
  P = Pick-up single/extended cab 2 door
  Q = Pick-up double cab 4 door
  Z = Special Offer / promotional vehicle
  E = Coupe (two-door sporty car, usually with two small rear seats)
  M = Monospace (5-seat multi-purpose vehicle with extra headroom)
  R = Recreational vehicle (substantial motorhome with living space)
  H = Motorhome (smaller recreational vehicle; campervan)
  Y = 2-Wheel Vehicle (motorcycle, scooter, moped)
  N = Roadster (two-door, two-seat sports car with open roof; distinct
      from T which has 4 seats)
  G = Crossover (CUV built on unibody car platform, combines SUV features
      with passenger car character; typically WITHOUT 4WD capability)
  K = Commercial Van/Truck (cargo or goods transport)

KEY DISTINCTIONS that matter for our market:

- F (SUV) vs G (Crossover): F is a proper SUV body (Tiguan, Tucson, X3,
  GLC, RAV4). G is an urban crossover with raised driving position but
  unibody construction, FWD-typical (Captur, T-Roc, Kona, Puma, Kamiq).
  When in doubt, F is "more truck-like", G is "more car-like".

- V (Passenger Van) vs M (Monospace): V is a true van body with tall roof
  and 6+ passenger-focused seats (Mercedes Vito Tourer, Renault Trafic
  Passenger, Ford Tourneo). M is a monospace / people-carrier with 5
  seats (or 5+2) and extra headroom but still car-like (BMW Serie 2
  Gran Tourer, Mercedes Clase B, VW Touran in some configurations).

- V (Passenger Van) vs K (Commercial Van): V is for passengers (Vito
  Tourer); K is for cargo (Vito Cargo, Transit Cargo).

- D (4-5 door) vs W (Wagon): D is hatchback or sedan; W is estate body
  (raised rear roofline, wagon styling).

- D vs E (Coupe): D is 4-5 door; E is 2-door coupe (sport, performance
  positioning, usually with small rear seats).

- N (Roadster) vs T (Convertible): N is 2-seat 2-door open-top sports
  car (Mazda MX-5, Porsche Boxster, BMW Z4); T is 4-seat convertible
  with open roof (Mini Cabrio, Audi A3 Cabrio, Mustang Convertible).

- J (Convertible SUV) is specifically a SUV with an open roof
  (Range Rover Evoque Convertible, Suzuki Vitara Cabrio in some markets);
  NOT a "Jeep-style" off-roader (those go in F).

- R (Recreational) vs H (Motorhome): R is the more substantial motorhome
  with full living space (Fiat Ducato Camper, larger campers); H is the
  smaller campervan (VW California Beach, Ford Transit Custom Nugget).
  Note: H = smaller, R = larger. This may seem counter-intuitive.

- Y (2-Wheeler): use only for motorcycles, scooters, mopeds.

---

### POSITION 3 — TRANSMISSION / DRIVE

Manual options:
  M = Manual Unspecified Drive (FWD or RWD, no 4WD)
  N = Manual 4WD
  C = Manual AWD

Auto options:
  A = Auto Unspecified Drive
  B = Auto 4WD
  D = Auto AWD

Electric:
  E = Electric

Autonomous (added 2025, not yet relevant in our market):
  Q = Level 3 Conditional Automation
  H = Level 4 High Automation
  F = Level 5 Full Automation

KEY RULES for our market:
- Most rental cars use M (manual) or A (auto unspecified). 4WD/AWD codes
  (N, C, B, D) are reserved for vehicles where 4x4/AWD is the defining
  feature (real off-roaders, premium-brand AWD SUVs marketed as such).
- A Mercedes GLC 4Matic Coupe might be A (auto, the default) or D (auto
  AWD) depending on how the provider lists it. If the provider explicitly
  says "4Matic" or "AWD", use D. Otherwise default to A.
- Same applies to manual 4WD: use N only if the vehicle is sold as a
  real off-roader (Jeep Wrangler, Toyota Land Cruiser); otherwise M.
- Autonomous codes (Q/H/F) are not yet present in our market. Ignore
  unless explicitly indicated.

---

### POSITION 4 — FUEL / AIR

Default (combustion):
  R = Unspecified Fuel/Power with Air conditioning
  N = Unspecified Fuel/Power without Air (rare in our market)

Diesel:
  D = Diesel with Air
  Q = Diesel no Air

Petrol explicit:
  V = Petrol with Air
  Z = Petrol no Air

Alternative fuels (electric and hybrid INCLUDE air conditioning by default;
the air specification is omitted in these codes):
  H = Hybrid (HEV / MHEV)
  I = Hybrid Plug-in (PHEV)
  E = Electric Vehicle (BEV) — typically longer range
  C = Electric Vehicle (BEV) — typically shorter range
  L = LPG/Compressed Gas with Air
  S = LPG/Compressed Gas no Air
  A = Hydrogen with Air
  B = Hydrogen no Air
  M = Multi Fuel/Power with Air
  F = Multi Fuel/Power no Air
  U = Ethanol with Air
  X = Ethanol no Air

IMPORTANT — change since 2020-2022:
- For Electric (E, C) and Hybrid (H, I), air conditioning is INCLUDED by
  default. There is no "no air" version. The fuel codes for these are
  used to distinguish between PHEV vs MHEV and longer/shorter range EVs.
- This is a change from earlier ACRISS versions, which had "+ Air"
  suffixes on H, E, etc.

KEY RULES for our market:
- DEFAULT to R (Combustion + Air) when fuel is not explicitly mentioned.
  This is the ACRISS default for rental cars.
- Use H (Hybrid) only if the provider explicitly says "Hybrid", "Híbrido",
  or the model name explicitly indicates hybrid (e.g. "Fiat Panda Hybrid",
  "Kia XCeed Hybrid", "Toyota Yaris Hybrid").
- Use I (Plug-in Hybrid) only if "Plug-in", "PHEV", or "enchufable" is
  mentioned.
- Use E or C (Electric) only if the vehicle is fully electric (Tesla,
  Honda e, BMW i4, etc.). When in doubt between E and C, default to E.
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

4. **MIXED GROUPS — classify to the highest-tier model present.**

   When the provider's PVC groups multiple distinct vehicle models that
   would map to DIFFERENT ACRISS codes (e.g. "VW Tiguan, VW T-Roc" where
   Tiguan = IFAR but T-Roc = CGAR), classify the PVC into the code of
   the model with the HIGHEST tier — the model representing the upper
   bound of what the customer pays for in that group.

   How to determine "highest tier":
   - First, by `acriss_category` (position 1). Standard order from
     lowest to highest in our market:
       M < E < C < I < S < F < P < L
     With Elite versions ranking just above their mainstream counterpart
     of the same size:
       N (Mini Elite) > M ; H > E ; D > C ; J > I ; R > S ;
       G > F ; U > P ; W > L
   - If multiple models tie on category but differ in body type, prefer
     in this order: F (SUV) > G (Crossover) > M (Monospace) > V (Van)
     > D (sedan) > W (wagon) > E (coupe) > T (convertible) > N (roadster).
     When in doubt, pick the body type with greater price prominence in
     the market.
   - If models tie on category and body but differ in transmission, prefer
     A (Auto) > M (Manual) — automatic is usually the more expensive
     variant.
   - If models tie on the first 3 attributes but differ in fuel, prefer
     R (standard combustion) — hybrid/electric are usually features, not
     tier indicators.

   ALWAYS in mixed-group cases:
   - Set `acriss_code` to the highest-tier code (not null).
   - Set the 4 attributes accordingly.
   - Set `pending_review = true` to signal operator review needed.
   - Set `confidence` to ~0.65 (low confidence due to model mixing).
   - In `reasoning`, explicitly state which models were present, why
     they map to different codes, and which one determined the choice.

   The intent of this rule: when a PVC groups heterogeneous models, we
   DO want to assign it a code (to keep it visible in pricing analytics),
   but we want to be honest about the ambiguity (via pending_review).
   The highest-tier choice is conservative: it reflects what the
   customer pays for at the top, never underestimating market pricing.

5. If the LLM cannot find ANY materialized code that fits — including
   the highest-tier model in a mixed group — set `acriss_code = null`
   and `pending_review = true`. This is rare; rule 4 should usually
   resolve mixed groups.

6. **NOTE ON `external_code` from the provider:**

   The `external_code` is the provider's internal label (e.g. "Grupo A",
   "Grupo F1", "CDAR"). Most providers use ARBITRARY internal labels
   that do NOT correspond to ACRISS standards. Their letters mean
   whatever the provider decides — typically just a sequential or
   classification scheme internal to that provider.

   Strategy:
   - If `external_code` is a valid 4-character ACRISS code (e.g. "CDAR",
     "EDMR", "IFAR") AND matches the description of a materialized code,
     use it as a STRONG hint with high confidence (0.95+).
   - If `external_code` is anything else (e.g. "Grupo A", "Group 5",
     "Category X"), IGNORE the code letters for classification. Use only
     `example_models`, seats, transmission, and other vehicle attributes.

   Do NOT infer ACRISS attributes from arbitrary internal labels. Two
   providers using "Grupo D" may classify completely different vehicles.

7. Confidence calibration:
   - 0.95+: clear match, all attributes unambiguous
   - 0.85-0.95: clear match, one attribute slightly uncertain (e.g. fuel
     not specified but defaulted to R)
   - 0.70-0.85: ambiguous on 1-2 attributes
   - ~0.65: mixed group resolved via rule 4 (highest-tier choice)
   - <0.60: poor fit; consider pending_review without a code
