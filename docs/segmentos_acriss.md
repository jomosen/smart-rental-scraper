# Tabla de emparejamientos modelo → segmento → ACRISS

> **Documento de criterio de clasificación.** Define LA TAXONOMÍA PROPIA de agrupación
> (vocabulario ACRISS, fronteras nuestras) y el mapa de migración código viejo → nuevo.
> Alimenta la realineación de `acriss_codes.yaml` y los ejemplos del clasificador
> (`GeminiClassificationService`).
>
> **Marco (ver decisiones transversales 11-16 al final):**
> - **Objetivo:** agrupar coches comparables entre proveedores para cruzar precios.
>   NO existe un "ACRISS canónico" único (Sixt y Drivalia clasifican distinto); la
>   taxonomía es nuestra, consistente consigo misma.
> - **Grano:** código completo de 4 letras (tamaño × carrocería × transmisión × fuel).
> - **Premium = tier Elite en 1ª letra**; la marca decide la tier (lista cerrada).
> - **Frontera por SEGMENTO comercial**, no por cm estricto.
> - **Validación de oro:** códigos reales de Drivalia (compite en Alicante y publica ACRISS).
> - Mercado acotado (<100 modelos) → lista de ejemplos generosa.
>
> **Avisos:** longitudes aproximadas (orientativas, no deciden frontera). La tabla
> canónica de las 4 letras es la del `acriss_reference.md` (validada con la de Sixt).

---

## LA ESCALA (1ª letra) y la corrección del desplazamiento

El catálogo original asignó letras según el **nombre comercial español**, no según el
significado ACRISS → toda la gama quedó **desplazada un peldaño** (se empezó en `E`
saltándose la `M`). Escala canónica:

`M` Mini · `N` Mini Elite · `E` Economy · `H` Economy Elite · `C` Compact ·
`D` Compact Elite · `I` Intermediate · `J` Intermediate Elite · `S` Standard ·
`R` Standard Elite · `F` Fullsize · `G` Fullsize Elite · `P` Premium ·
`U` Premium Elite · `L` Luxury · `W` Luxury Elite · `X` Special

**Corrección estructural (gama baja-media):**

| Segmento | Código HOY (corrido) | Código NUEVO | Validación externa |
|---|---|---|---|
| A — city cars (500, Panda, Aygo) | `EDMR`/`EDAR` | **`MDMR`/`MDAR`** (M=Mini) | Drivalia: 500e=`MBAE` ✓ |
| B — superminis (Polo, Corsa, 208) | `CDMR`/`CDAR` | **`EDMR`/`EDAR`** (E=Economy) | Drivalia: 208=`EDMR` ✓, MG3=`EDAR` ✓ |
| C — compactos (Golf, Focus, A3) | `CDAR` + `IDMR` (split) | **`CDMR`/`CDAR`** (unifica C) | Drivalia: Focus=`CDMR` ✓, C4=`CDAR` ✓ |

El código pasa a la escala correcta; el nombre español vive en `display_name`.

> **Migración:** este documento es el mapa viejo→nuevo para UN ÚNICO cambio en Code
> (YAML + reference + reclasificación + revisión de referencias), aplicado de golpe y
> verificado contra los códigos de Drivalia. NO migrar a trozos: los códigos viejos y
> nuevos solapan (el `CDMR` de hoy = B; el `CDMR` nuevo = C) → colisiones. Ver el
> **RESUMEN DE MIGRACIÓN** al final del documento.

---

## A-SEGMENT — City cars (≤ ~3,7 m) → ACRISS `M` (Mini)

**Migración:** hoy en `EDMR`/`EDAR` (Economy) → canónico **`MDMR`/`MDAR`** (Mini).
Variantes powertrain: híbrido hoy `EDMH` → **`MDMH`**; eléctrico hoy `EDAE` → **`MDAE`**.

`display_name` sugerido: "Mini" / "Mini Automático" (en vez de "Económico", nombre
que pasa a designar el B-segment).

| Modelo | Largo aprox | ¿En catálogo hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Fiat 500 | 3,57 | Sí (EDMR) | **MDMR** | Muy común | El más habitual del A-segment en alquiler |
| Fiat Panda | 3,69 | Sí (EDMR) | **MDMR** | Muy común | Workhorse de flotas económicas |
| Kia Picanto | 3,60 | Sí (EDMR) | **MDMR** | Común | |
| Hyundai i10 | 3,67 | Sí (EDAR) | **MDAR** | Común | Gemelo del Picanto |
| Toyota Aygo (clásico) | 3,47 | Sí (bundle) | **MDMR / MDAR** | Flota viva, descatalogado | Aygo X (4,0 m) NO entra aquí → Crossovers |
| Citroën C1 | 3,47 | No | **MDMR** | Flota viva, descatalogado | Trillizo Aygo/108/C1 |
| Peugeot 108 | 3,48 | No | **MDMR** | Flota viva, descatalogado | ídem |
| Volkswagen up! | 3,60 | No | **MDMR / MDAR** | Flota viva, descatalogado | Trillizo con Mii/Citigo |
| SEAT Mii | 3,56 | No | **MDMR** | Flota viva, descatalogado | Probable en flotas españolas (SEAT) |
| Škoda Citigo | 3,56 | No | **MDMR** | Flota viva, descatalogado | |
| Renault Twingo | 3,62 | No | **MDMR** | Flota viva, descatalogado | Posible en mix Renault |
| Mitsubishi Space Star | 3,85 | No | **MDMR** | Económico low-cost | Frontera alta; flotas isleñas/low-cost. Revisar si entra |

**Frontera A-segment:** longitud ≤ ~3,7 m (Space Star a 3,85 como caso límite alto).

**Estado:** los 5 modelos de hoy (500, Panda, Picanto, i10, Aygo) ya están juntos y
bien agrupados; solo cambia la LETRA (E→M) al migrar a canónico. Coherencia interna OK.

**Para el YAML — ejemplos canónicos sugeridos (MDMR/MDAR):**
Fiat 500, Fiat Panda, Kia Picanto, Hyundai i10, Toyota Aygo, VW up!, SEAT Mii,
Škoda Citigo, Citroën C1, Peugeot 108, Renault Twingo.

**Fuera de A:** Toyota Yaris y Suzuki Swift → B (Economy). Toyota Aygo X → Crossovers.

### Apéndice A.1 — MINI ELITE → ACRISS `N` (Mini Elite)

City cars de **marca premium o posicionamiento superior** dentro del A-segment.
Mismo tamaño que un Mini (≤ ~3,9 m) pero gama/precio por encima. Categoría real de
ACRISS, pero de **volumen muy bajo** en alquiler (el A-segment se extingue, y su
versión elite es un nicho dentro de un nicho).

**No tenemos ninguno hoy.** Tabla anticipatoria — crear la categoría en el YAML solo
cuando aparezca un coche real (principio de densidad: no crear códigos vacíos).

| Modelo | Largo aprox | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|
| MINI 3 puertas (Cooper) | 3,88 | **NDMR / NDAR** | Posible (turístico/leisure) | El caso canónico de Mini Elite |
| MINI Electric (3p) | 3,86 | **NDAE** | Creciente | Mini Elite eléctrico |
| Abarth 500 / 595 | 3,66 | **NDMR** | Raro, leisure | 500 "premium-deportivo" |
| Abarth 500e | 3,67 | **NDAE** | Raro | Eléctrico |
| Smart fortwo | 2,70 | **NDAR / NDAE** | Raro, urbano | Microcoche premium; encaje dudoso |

(La Lancia Ypsilon nueva (2024, 4,08 m) NO va aquí — por tamaño es B; se recoge en el
apéndice B.1 Economy Elite.)

**Frontera N:** ≤ ~3,9 m + marca/posicionamiento premium. El MINI Cooper es el ancla.

**Aviso de densidad:** salvo el MINI (que en zona turística/leisure sí puede aparecer),
el resto es marginal. Mantener este apéndice como referencia; no materializar `N` en el
YAML hasta tener al menos un modelo real clasificándose aquí.

---

## B-SEGMENT — Superminis (~3,9–4,2 m) → ACRISS `E` (Economy)

**Migración:** hoy en `CDMR`/`CDAR` (Compact, mal) → canónico **`EDMR`/`EDAR`** (Economy).
`display_name`: "Económico" / "Económico Automático" (nombre que se libera al bajar el
A-segment a Mini). Variantes: híbrido **`EDMH`**; eléctrico **`EDAE`** (ver sub-tipos).

> **Atención migración — colisión de códigos:** el `EDMR`/`EDAR` de HOY contiene el
> A-segment (Mini). Tras la migración, `EDMR`/`EDAR` pasa a contener el B-segment.
> Por eso la migración debe ser una sola pasada con mapa viejo→nuevo, no incremental.

**Frontera B-segment:** ~3,85–4,25 m, hatchback 5p, 5 plazas, mainstream. Por encima
del Mini (city car ≤3,7), por debajo del Compact (Golf ~4,28).

### B.0 — Superminis hatchback (núcleo del segmento)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| VW Polo | 4,07 | Sí (CDMR, bundle) | **EDMR** | Muy común | Referencia del segmento |
| Opel Corsa | 4,06 | Sí (CDMR) | **EDMR** | Muy común | |
| Peugeot 208 | 4,06 | Sí (CDAR/CDMR) | **EDAR / EDMR** | Muy común | |
| Renault Clio | 4,05 | No (ej. YAML) | **EDMR / EDAR** | Muy común | Falta como dato real, muy probable |
| Citroën C3 | 4,01 | Sí (CDMR, bundle) | **EDMR** | Común | |
| Hyundai i20 | 4,04 | Sí (CDAR/CDMR) | **EDAR / EDMR** | Común | |
| SEAT Ibiza | 4,06 | No | **EDMR / EDAR** | Muy común | SEAT, casi seguro en flota ES |
| Toyota Yaris | 3,94 | Sí (bundle EDAR=Mini) | **EDAR** | Muy común | **Bajó del A.** Ver decisión frontera |
| Suzuki Swift | 3,86 | Sí (bundle EDAR=Mini) | **EDAR / EDMR** | Común | **Bajó del A** (límite bajo) |
| Ford Fiesta | 4,07 | No | **EDMR / EDAR** | Flota viva, descatalogado | Muerto 2023, aún circula |
| Mazda 2 | 4,07 | No | **EDMR / EDAR** | Posible | Mazda2 Hybrid = Yaris rebadge |
| Nissan Micra | 4,00 | No | **EDMR / EDAR** | Posible | |
| Renault Sandero / Dacia | 4,09 | No | **EDMR** | Posible low-cost | Flotas económicas |

### B.1 — Apéndice ECONOMY ELITE → ACRISS `H` (Economy Elite)

Superminis de marca/posicionamiento premium. Se **materializa** en el YAML (estrategia
granular: la rejilla ACRISS plausible se crea aunque haya pocos ocupantes, para no
reabrir el catálogo cada vez que llega un coche).

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Audi A1 | 4,03 | Sí (hoy DDAR) | **HDAR / HDMR** | Posible/real | **RECLASIFICAR desde DDAR.** Supermini premium = Economy Elite por tamaño |
| MINI 5 puertas | 4,04 | No | **HDAR** | Posible leisure | La variante 5p (la 3p va a Mini Elite N) |
| Lancia Ypsilon (2024) | 4,08 | No | **HDMR / HDAR** | Posible (Italia) | Movido aquí desde Mini Elite |
| DS 3 | 4,12 | No | **HDAR** | Raro | Supermini premium-ish francés |

> **Nota cruzada:** el Audi A1 estaba en DDAR (premium compacto) desde la fase b, por
> densidad (no existía Economy Elite). Con la estrategia granular se materializa `H` y
> el A1 baja a su sitio canónico. En el bloque PREMIUM, el A1 ya NO figura en DDAR.

### B.2 — Sub-tipo ELÉCTRICO B → `EDAE` (Economy Eléctrico)

BEV de tamaño supermini. El eje eléctrico cruza el de tamaño: van a Economy + fuel E.

| Modelo | Largo aprox | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|
| Peugeot e-208 | 4,06 | **EDAE** | Creciente | Ya citado como ej. en YAML (estaba en EDAE=Mini, revisar) |
| Opel Corsa-e | 4,06 | **EDAE** | Creciente | |
| Renault Zoe | 4,08 | **EDAE** | Flota viva, descatalogado | |
| MG4 | 4,29 | **EDAE / CDAE** | Creciente | Límite alto, casi C — revisar |

### B.3 — Crossover-B → van a la sección CROSSOVERS (no aquí)

Los SUV-city diminutos (Fiat 600, Toyota Aygo X, Jeep Avenger, etc.) NO son superminis
hatchback: son crossovers. Se clasifican y listan en la sección **Crossovers**, no en el
B-segment. Apunte de migración a no perder: el **Fiat 600 hoy está en CDAR** (Compact) —
mal; se reubica en su código crossover al tratar esa sección.

---

**Frontera resuelta:** Yaris (3,94) y Swift (3,86) → **Economy (`E`)**, junto al Polo.
Criterio: tamaño (ambos sobre el city-car puro ~3,5) + regla del usuario (Toyota tiene
Aygo debajo → Yaris sube a E; Suzuki sin modelo inferior → Swift validado por sus pares
superminis ya en E). Los dos juntos: coherencia visual intacta. El posicionamiento
"barato" se refleja en el PRECIO dentro de Economy, no en mezclar segmentos.

---

## C-SEGMENT — Compactos (~4,25–4,45 m) → ACRISS `C` (Compact)

**El conflicto que originó esta revisión.** Hoy el C-segment está PARTIDO en dos códigos:
el Golf cayó en `CDAR` (que hoy = "Compacto" pero contiene superminis B) y el Focus/Astra
en `IDMR`/`IDAR` ("Intermedio"). Coches idénticos en tiers distintas. La migración canónica
los UNIFICA en `C` (Compact) real.

**Migración:** Golf hoy `CDAR` → **`CDAR`** (canónico, mismo código pero ahora significa
Compact de verdad). Focus/Astra/308 hoy `IDMR`/`IDAR` → **`CDMR`/`CDAR`**. Ojo: el `CDMR`/
`CDAR` de hoy contiene superminis B (Polo, Corsa) que bajan a `E`; los compactos C suben a
ocupar `CDMR`/`CDAR`. Es el baile de sillas — una sola pasada con mapa viejo→nuevo.

`display_name`: "Compacto" / "Compacto Automático" (nombre que se libera al bajar el
B-segment a Economy). Frontera C: ~4,25–4,45 m, hatchback/sedán 5p, mainstream.

### C.0 — Compactos hatchback/sedán (núcleo)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| VW Golf | 4,28 | Sí (CDAR) | **CDAR** | Muy común | El conflicto: hoy solo en compacto, separado del Focus |
| Ford Focus | 4,38 | Sí (IDMR, bundle) | **CDMR / CDAR** | Muy común | Hoy en "intermedio", debe unirse al Golf |
| Opel Astra | 4,37 | Sí (IDMR + IGAR, ¡dup!) | **CDMR / CDAR** | Muy común | Duplicado hoy; resolver al migrar |
| Peugeot 308 | 4,37 | No (ej. YAML) | **CDMR / CDAR** | Común | |
| SEAT León | 4,37 | No | **CDMR / CDAR** | Muy común | SEAT, casi seguro en flota ES |
| Hyundai i30 | 4,34 | No (ej. YAML) | **CDMR / CDAR** | Común | |
| Renault Mégane | 4,36 | No | **CDMR / CDAR** | Común | (el Mégane E-Tech eléctrico → C eléctrico/crossover) |
| Toyota Corolla | 4,37 | No | **CDAR** | Común | Híbrido muy frecuente → ver powertrain |
| Honda Civic | 4,55 | No | **CDAR** | Posible | C-segment aunque mida 4,55 (segmento manda, no el cm) |
| Škoda Scala | 4,36 | No | **CDMR / CDAR** | Posible | |
| Mazda 3 | 4,46 | No | **CDAR** | Posible | C-segment aunque mida 4,46 (segmento manda) |
| Kia Ceed (hatch) | 4,31 | No | **CDMR / CDAR** | Posible | C-segment. La variante SW (wagon) va a `CW`, no aquí |
| VW Golf Variant (familiar) | 4,63 | No | **`CW` Wagon** (CWMR/CWAR) | Posible | Carrocería WAGON, no hatch → sección Wagon, NO con Touran (MPV) |
| Kia Ceed SW (familiar) | 4,60 | Sí (hoy IWAR) | **`CW` Wagon** | Posible | Migra de IWAR→CW. Hereda tamaño C de su plataforma, no sube por la cola |

> **Frontera C/I — nota del usuario:** el Intermediate (`I`) en este catálogo ha sido
> históricamente un cajón de edge cases mal clasificados, no una tier real del mercado.
> Por eso los "límite alto" (Civic 4,55 / Mazda3 4,46 / A3 Sedan 4,50) se quedan en C
> por segmento. Revisar al llegar al bloque Intermediate si esa tier debe existir o es
> casi vacía (el mercado salta de Compact a Standard).

### C.1 — Apéndice COMPACT ELITE → ACRISS `D` (Compact Elite)

Compactos C de marca premium (sedán/hatch). **Esta es la `D` que en la fase b creímos
inventada y resulta ser canónica** (D = Compact Elite). Aquí viven los DDAR/DDMR ya creados.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Audi A3 | 4,34 | Sí (DDAR) | **DDAR** | Real | Ya bien clasificado en fase b. Espejo premium del Golf |
| BMW Serie 1 | 4,32 | No | **DDAR** | Posible | Encaja limpio en D; ancla del Compact Elite |
| Mercedes Clase A | 4,42 | No | **DDAR** | Posible | |
| Audi A3 Sedan | 4,50 | No | **DDAR / DDMR** | Posible | C-segment premium aunque mida 4,50 (segmento manda) |

> **Serie 2 Gran Coupé fuera de aquí:** el Serie 1 (4,32) ocupa `D` limpiamente; el Serie 2
> (4,53) es el escalón SUPERIOR de BMW → por la regla del usuario, sube al ACRISS superior
> (Premium `P` / ejecutivo), no se amontona en `D` con el Serie 1. Se trata en el bloque Premium.

> **Coherencia clave:** DDAR (compacto premium) y CDAR (compacto mainstream) cubren el
> MISMO escalón de tamaño (C-segment), uno premium y otro mainstream. A3 ↔ Golf son
> espejo. Esto es la simetría que da sensación de rigor en la vista.

### C.2 — Sub-tipo ELÉCTRICO C → `CDAE` (Compact Eléctrico)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| VW ID.3 | 4,26 | No (ej. en EDAE hoy) | **CDAE** | Creciente | Hoy citado en EDAE (Mini elec) — mal, es C. Reubicar |
| Cupra Born | 4,32 | No | **CDAE** | Creciente | Gemelo del ID.3 |
| MG4 | 4,29 | No | **CDAE** | Creciente | Estaba dudoso en B.2; por tamaño es C |
| Renault Mégane E-Tech | 4,21 | No | **CDAE** | Creciente | |

### C.3 — Sub-tipo HÍBRIDO C → `CDAH` (Compact Híbrido)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Toyota Corolla Hybrid | 4,37 | No | **CDAH** | Muy común | El híbrido C más frecuente en alquiler |
| Honda Civic e:HEV | 4,55 | No | **CDAH** | Posible | |

### Crossover-C → van a la sección CROSSOVERS

SUV/crossover de tamaño C (4,3–4,5 m) NO van aquí: a Crossovers. Apunte: hoy hay un bundle
"Peugeot 2008, Opel Astra" en `IGAR` que mezcla un crossover (2008) con un compacto (Astra)
— resolver al tratar Crossovers / al deshacer el bundle.

---

**Lo que el C destapa para la migración (resumen):**
1. **Unificar Golf + Focus + Astra + 308** en `C`. Hoy partidos CDAR vs IDMR.
2. **Resolver el Astra duplicado** (IDMR + IGAR). Decidir bundle único.
3. **A3 ya está bien** en DDAR (D = Compact Elite, canónico). No se toca salvo confirmar.
4. **ID.3 mal en EDAE** (Mini eléctrico) → debe ser CDAE (Compact eléctrico). Reubicar.
5. **Powertrain como eje**: C tiene combustión (CDAR/CDMR), eléctrico (CDAE), híbrido (CDAH).

---

## INTERMEDIATE — Mid-size (~4,5–4,7 m) → ACRISS `I` (Intermediate)

**Tier dudosa (ver decisión transversal nº10).** En este catálogo el `I` ha sido un cajón
de edge cases mal clasificados, no una tier real. La pregunta es si el mercado de alquiler
(turismo, NO SUV) tiene de verdad un escalón entre Compact y Standard, o si salta directo.

**Realidad del mercado turismo:** el "intermedio sedán/hatch mainstream" casi ha
desaparecido. El C-segment creció (un Golf de hoy ya mide 4,28), el siguiente salto
mainstream suele ser directo a fullsize (Passat, 4,87 → Standard). El hueco intermedio
(4,5–4,7) lo ocupan hoy sobre todo **SUV** (que van a su sección) y algún sedán suelto.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Škoda Octavia | 4,69 | No | **IDAR** | Posible | **Único ocupante del I turismo.** Desplazado por el Superb (Skoda en S) |
| Kia Ceed (hatch) | 4,31 | No | **CDAR** (Compact) | — | Contraejemplo: NO es I, es C (ver C.0) |

**Veredicto:** el Intermediate turismo se materializa con **un único ocupante real, el
Octavia.** No es ruido: existe por la **regla relacional** — Škoda tiene Superb (4,91) en
Standard, que desplaza al Octavia (4,69) al escalón inferior. Una tier de un coche con
justificación relacional es legítima.

**Nota de fragilidad (no acción):** al depender de un solo modelo, si el Octavia sale de
flota el `I` turismo queda vacío. El YAML debe reflejar que esta tier existe por un modelo
concreto, no por un segmento amplio. Revisar si queda vacía.

**Cuidado (no confundir):** el `I` turismo (Octavia, sedán/liftback) es distinto del `I` SUV
(`IFAR`/`IFMR`/`IGAR` = Tucson, Kuga, Tiguan, Grandland), que vive en Crossovers/SUV. Misma
letra de tamaño, distinta carrocería. Los edge cases históricos venían de meter turismos
sueltos en la tier que en realidad usaban los SUV.

---

## STANDARD — Fullsize (~4,8–5,0 m) → ACRISS `S` (Standard)

Berlinas/familiares grandes mainstream (no premium). Tope de gama mainstream, por debajo
del premium ejecutivo (`P`).

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| VW Passat | 4,92 | No (ej. YAML) | **SDAR** | Común (familiar) | El Passat actual solo es familiar (Variant) |
| Škoda Superb | 4,91 | No | **SDAR** | Posible | Buque insignia Skoda → S (desplaza al Octavia a I) |
| Ford Mondeo | 4,87 | No | **SDAR** | Descatalogado, vivo | |
| Toyota Camry | 4,88 | No | **SDAR** | Raro en ES | Más común híbrido → SDAH |
| Opel Insignia | 4,90 | No | **SDAR** | Descatalogado, vivo | |
| Renault Talisman | 4,85 | No | **SDAR** | Descatalogado, raro | Fullsize → S (no I) |
| Mazda 6 | 4,87 | No | **SDAR** | Posible | Fullsize → S (no I) |

**Powertrain:** Standard híbrido (Camry) → `SDAH`. Eléctrico fullsize mainstream raro hoy.

**Nota:** el catálogo ya tiene `SDAR` ("Estándar Automático") con Passat/Octavia/Mondeo
como ejemplos. **Corregir:** el Octavia NO va aquí (va a Intermediate por la regla relacional
con el Superb). Passat/Superb/Mondeo/Insignia sí son Standard.

---



---

## SUV / CROSSOVER → eje por TAMAÑO + premium (Elite)

> **RESET (alineado con las decisiones cerradas + validación Drivalia).**
> Decisiones que aplican aquí:
> - **2ª letra = `F` (SUV)** para todos los SUV/crossover. Validado por Drivalia: C3
>   Aircross = `CFMR`, Qashqai = `IFMR`, 3008 = `IFAR`. **La `G` (Crossover) del reference
>   existe, pero Drivalia colapsa todo en `F`** → adoptamos `F` para máxima cobertura de
>   agrupación. (Si más adelante un proveedor distingue G, se revisa; por defecto: `F`.)
> - **Premium = tier Elite en 1ª letra** (Forma B): SUV compacto premium → `D`, SUV mediano
>   premium → `R`. Marca premium decide la tier por lista cerrada.
> - **Eje de tamaño:** Compacto (`C`) → Mediano (`I`) → Grande (`S`/`F` fullsize). Premium
>   sube a su Elite (`D`/`J`/`R`).
> - **Vista:** "SUV Compacto / SUV Mediano / SUV Grande" + "Premium". Sin "crossover"/"urbano".

Simetría con turismo: Compacto `C`↔SUV Comp. `CF` · Compacto Elite `D`↔SUV Comp. premium
`DF` · Intermedio `I`↔SUV Mediano `IF` · Standard Elite `R`↔SUV Mediano premium `RF`.

### SUV Urbano mainstream (~4,0–4,25 m) → `EF**` (Economy + SUV)

Mini-SUV de base supermini. El peldaño de entrada. Resuelve las colisiones Bayon/Kona
(Hyundai) y T-Cross/T-Roc (VW) que antes convivían en un CF demasiado ancho.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Hyundai Bayon | 4,18 | Sí (CGMR) | **EFMR / EFAR** | Real | Base i20 |
| SEAT Arona | 4,14 | Sí (CGMR) | **EFMR / EFAR** | Real | Base Ibiza |
| VW T-Cross | 4,11 | No | **EFAR / EFMR** | Posible | Base Polo. Ya no colisiona con T-Roc |
| Ford Puma | 4,19 | Sí (CGAR/CGMR) | **EFAR / EFMR** | Real | Base Fiesta |
| Kia Stonic | 4,14 | No | **EFMR / EFAR** | Posible | |
| Toyota Yaris Cross | 4,18 | No | **EFAR / EFAH** | Común | Casi siempre híbrido → EFAH |
| Opel Crossland | 4,21 | Sí (CGMR) | **EFMR** | Real | |
| Citroën C3 Aircross | 4,16 | No | **EFMR** | Real | ⚠️ Drivalia lo pone en CFMR; nosotros más fino → EF |
| Renault Captur | 4,23 | No | **EFAR / EFMR** | Posible | |
| Nissan Juke | 4,21 | No | **EFAR / EFMR** | Posible | |
| Jeep Avenger | 4,08 | No | **EFAR** | Creciente | |
| Fiat 600 | 4,17 | Sí (CDAR) | **EFAR** | Creciente | Hoy MAL en CDAR. Mini-SUV |
| Toyota Aygo X | 4,00 | No | **EFAR / EFMR** | Creciente | Límite bajo |

### SUV Compacto mainstream (~4,25–4,45 m) → `CF**`

El segundo peldaño: base compacta (Golf/i30/Scala).

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| VW T-Roc | 4,23–4,37* | Sí (CGAR) | **CFAR** | Real | *El nuevo T-Roc crece. Escalón VW sobre el T-Cross |
| Hyundai Kona | 4,35 | No | **CFAR / CFMR / CFAH** | Posible | Escalón Hyundai sobre el Bayon |
| Škoda Kamiq | 4,24 | Sí (CGAR) | **CFAR** | Real | Frontera EF/CF; base Scala (C) → CF por plataforma |
| Toyota C-HR | 4,36 | No | **CFAH** | Común | Híbrido |
| Peugeot 2008 | 4,30 | Sí (IGAR, bundle) | **CFAR** | Real | Deshacer bundle con Astra |
| Kia XCeed | 4,40 | Sí (CGAH) | **CFAH / CFAR** | Real | Migra CGAH→CFAH |
| MG ZS | 4,32 | Sí (CGMR) | **CFMR** | Real | |
| SEAT Ateca | 4,38 | No | **CFAR** | Común | Antes lo teníamos en IF; por escalón SEAT (sobre Arona, bajo Tarraco) → CF |

**Powertrain:** híbrido `EFAH`/`CFAH` (Yaris Cross, C-HR, XCeed, Kona HEV); eléctrico
`EFAE`/`CFAE` (ver eléctricos abajo).

### SUV Mediano mainstream (~4,45–4,7 m) → `IF**`

SUV familiar 5 plazas. **Validado por Drivalia:** Qashqai=IFMR, 3008=IFAR.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| VW Tiguan | 4,54 | Sí (IFAR) | **IFAR** | Real | |
| Hyundai Tucson | 4,50 | Sí (IFAR + IFMR) | **IFAR / IFMR** | Real | Escalón Hyundai sobre el Kona ✓ |
| Ford Kuga | 4,61 | Sí (IFMR) | **IFAR / IFMR** | Real | |
| Opel Grandland | 4,48 | Sí (IFAR) | **IFAR** | Real | |
| Nissan Qashqai | 4,42 | No | **IFAR / IFMR** | Muy común | **Drivalia = IFMR (validado)** |
| Kia Sportage | 4,52 | No | **IFAR / IFMR** | Común | |
| Toyota RAV4 | 4,60 | No | **IFAH** | Común | Casi siempre híbrido |
| Peugeot 3008 | 4,54 | No | **IFAR** | Común | **Drivalia = IFAR (validado)** |
| Mazda CX-5 | 4,55 | No | **IFAR** | Posible | |
| DFSK 580 | 4,68 | Sí (IFAR) | **IFAR** | Real | 5+2; frontera con SF |

### SUV Grande mainstream 7pl (~4,7–5,0 m) → `SF**` (Standard + SUV)

El peldaño que faltaba por arriba: SUV grandes, normalmente 7 plazas.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Škoda Kodiaq | 4,76 | No | **SFAR** | Posible | (Sixt lo llama "Premium SUV" por tamaño) |
| Hyundai Santa Fe | 4,83 | No | **SFAR** | Posible | Escalón Hyundai sobre el Tucson |
| Kia Sorento | 4,81 | No | **SFAR** | Raro | |
| SEAT Tarraco | 4,74 | No | **SFAR** | Posible | |
| Peugeot 5008 | 4,64 | No | **SFAR** | Posible | DECIDIDO: SUV grande con Kodiaq/Santa Fe. Divergencia deliberada nº2 (Drivalia=FVAR); FV queda para vans reales |
| Toyota Highlander | 4,95 | No | **SFAH** | Casi nunca | |

*(SUV Gigante `FF` —Palisade, Explorer— prácticamente no existe en alquiler Europa; no
materializar hasta ver uno.)*

### SUV Pequeño Premium (~4,0–4,3 m) → `HF**` (Economy Elite + SUV)

El escalón premium POR DEBAJO del SUV compacto premium. Espejo SUV del `H` (A1→HD,
Q2→HF). Existe por la regla relacional: Audi tiene Q2 Y Q3; no comparten tier.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Audi Q2 | 4,21 | Sí (DFAR/DFMR) | **HFAR / HFMR** | Real | **Migra DF→HF.** Base supermini premium (espejo del A1) |
| DS3 Crossback | 4,12 | No | **HFAR** | Posible | Mismo escalón si aparece |

### SUV Compacto Premium (~4,3–4,6 m) → `DF**`

Espejo premium del SUV Compacto. `D` = Compact Elite. Marca premium decide la tier.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Mercedes GLA | 4,41 | Sí (DFAR) | **DFAR** | Real | Escalón de ENTRADA de Mercedes en SUV (equivale al Q3, no al Q2) |
| Audi Q3 | 4,48 | No | **DFAR** | Posible | Ya sin colisión con el Q2 (que baja a HF) |
| BMW X1 | 4,50 | No | **DFAR** | Posible | |
| Volvo XC40 | 4,44 | No | **DFAR** | Posible | |

### SUV Mediano Premium (~4,6–4,9 m) → `RF**`

Espejo premium del SUV Mediano. `R` = Standard Elite. Incluye SUV premium 5+2 (GLB).

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Mercedes GLC | 4,72 | Sí (RFAR) | **RFAR** | Real | |
| Mercedes GLC Coupé | 4,76 | Sí (RFAR) | **RFAR** | Real | Variante coupé = mismo peldaño |
| Mercedes GLB | 4,63 | Sí (RFAR) | **RFAR** | Real | 5+2. OK desde fase a |
| BMW X3 / X4 | 4,71 | No | **RFAR** | Posible | (Sixt: "Premium SUV" por tamaño) |
| Audi Q5 / Sportback | 4,68 | No | **RFAR** | Posible | |
| Volvo XC60 | 4,71 | No | **RFAR** | Posible | |
| Porsche Macan | 4,73 | No | **RFAR** | Raro | |
| Lexus NX | 4,66 | No | **RFAR** | Raro | |

### SUV Grande Premium (~4,9–5,1 m) → `UF**` (Premium Elite + SUV)

El escalón E de la gama premium. Incluye sus variantes coupé (mismo peldaño de precio,
como GLC/GLC Coupé) y las versiones 7 plazas de la misma plataforma.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Mercedes GLE / GLE Coupé | 4,92 | No | **UFAR** | Raro | |
| BMW X5 / X6 | 4,92 | No | **UFAR** | Raro | X6 = variante coupé, mismo peldaño |
| Audi Q7 | 5,06 | No | **UFAR** | Raro | 7 plazas misma plataforma → mismo peldaño |
| Audi Q8 | 4,98 | No | **UFAR** | Raro | Coupé del Q7 → mismo peldaño |
| Volvo XC90 | 4,95 | No | **UFAR** | Raro | |
| Porsche Cayenne | 4,93 | No | **UFAR / LFAR** | Raro | Frontera con lujo (Sixt: "Lujo SUV") |
| Lexus RX | 4,89 | No | **UFAR** | Muy raro | |

### SUV Lujo / Todoterreno Lujo (~4,8–5,2+) → `LF**` / `WF**`

El escalón ostentación: lujo SUV (`LF` = Luxury) y gigantes 7pl (`WF` = Luxury Elite).
Incluye los todoterreno puros de lujo (Clase G, Defender), que son otro producto y otro
precio. **Drivalia valida `LF`:** DS7 = `LFAR` (usa LF para SUV grande de lujo).

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Range Rover / Sport | 5,0+ | No | **LFAR** | Muy raro | |
| Mercedes Clase G | 4,87 | No | **LFAR** | Muy raro | Todoterreno lujo |
| Land Rover Defender 110 | 5,02 | No | **LFAR** | Muy raro | Todoterreno lujo |
| DS7 | 4,59 | No | **LFAR** | Raro | **Drivalia = LFAR (validado).** Ojo: tamaño D pero DS lo posiciona lujo |
| Mercedes GLS / BMW X7 / Audi "Q9" | 5,1+ | No | **WFAR** | Casi nunca | Gigante 7pl. Materializar plausible |

**Escala SUV completa (referencia rápida):**
```
Mainstream:  EF (Bayon/Arona/T-Cross) → CF (Kona/T-Roc/2008) → IF (Tucson/Tiguan/Qashqai) → SF (Santa Fe/Kodiaq)
Premium:     HF (Q2) → DF (Q3/GLA/X1) → RF (Q5/GLC/X3) → UF (Q7/GLE/X5) → LF (RR/Clase G) → WF (GLS/X7)
```
Simetría por peldaño: EF↔HF · CF↔DF · IF↔RF · SF↔UF. Equivalencias por marca (regla
relacional): Hyundai Bayon→Kona→Tucson→Santa Fe ≡ EF→CF→IF→SF; VW T-Cross→T-Roc→Tiguan;
Audi Q2→Q3→Q5→Q7/Q8; Mercedes GLA→GLC→GLE→GLS; BMW X1→X3→X5→X7. Variantes coupé
(X4/X6/Q8/GLC Coupé/GLE Coupé) = mismo peldaño que su hermano. 7 plazas de la misma
plataforma = mismo peldaño.

### SUV Eléctrico → 4º char `E` (autonomía larga) / `C` (corta)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado | Notas |
|---|---|---|---|---|---|
| Ford Mustang Mach-E | 4,71 | Sí (CFAE) | **IFAE** | Real | DECIDIDO: mediano mainstream (Ford no es premium) |
| Hyundai Kona Electric | 4,21 | No | **CFAE** | Creciente | SUV compacto eléctrico |
| VW ID.4 | 4,58 | No | **IFAE** | Creciente | SUV mediano eléctrico |
| Kia Niro EV | 4,42 | No | **CFAE / IFAE** | Creciente | |
| Tesla Model Y | 4,75 | No | **RFAE** | Creciente | SUV mediano premium eléctrico |
| Kia EV6 | 4,68 | No | **IFAE / RFAE** | Creciente | |

---

**Líos que este bloque resuelve (para la migración):**
1. **Migración masiva CG→CF:** todos los SUV compactos del catálogo actual (`CGMR`/`CGAR`/
   `CGAH`) pasan a `CF**`. La `G` deja de usarse (Drivalia confirma `F`).
2. **Fiat 600** `CDAR` (turismo) → `CFAR` (SUV compacto). Mal hoy.
3. **Peugeot 2008** `IGAR` (bundle) → `CFAR`. Deshacer bundle con Astra.
4. **Bundle "2008 + Astra"** → 2008 a CFAR (SUV), Astra a CDAR (turismo C).
5. **Astra duplicado** (IDMR + IGAR) → queda solo en turismo C.
6. **Mustang Mach-E** `CFAE` → `IFAE` (mediano; Ford mainstream). DECIDIDO.
7. **`IGAR`/`CGAR`** (códigos con G) → se vacían; **eliminar de la rejilla** al migrar.
8. **Validación de oro (Drivalia):** C3 Aircross→CFMR, Qashqai→IFMR, 3008→IFAR. El
   clasificador debe reproducirlos tras la realineación.

---

## PREMIUM SEDÁN/EJECUTIVO → ACRISS `P` (Premium) y superiores

Sedán/hatch/coupé de marca premium, de tamaño ejecutivo en adelante. **Solo turismo
premium — los SUV premium (GLA/GLB/GLC/X1/X3…) NO van aquí, van a Crossovers/SUV** (eje
carrocería distinto). Aquí está la escala premium de berlina: compacto-premium (`D`, ya
cerrado en §C.1) → ejecutivo (`P`) → grande (`U`) → lujo (`L`).

**Escala premium por marca (referencia relacional):**
- BMW: Serie 1 (D) · Serie 3 (P) · Serie 5 (U) · Serie 7 (L)
- Audi: A3 (D) · A4 (P) · A6 (U) · A8 (L)
- Mercedes: Clase A (D) · Clase C (P) · Clase E (U) · Clase S (L)

### P — Premium ejecutivo (~4,6–4,9 m)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| BMW Serie 3 | 4,71 | No (ej. YAML) | **PDAR** | Posible | Ancla del ejecutivo |
| Audi A4 | 4,76 | No (ej. YAML) | **PDAR** | Posible | |
| Mercedes Clase C | 4,75 | Sí (PDAR) | **PDAR** | Real | Ya bien clasificado |
| Lexus IS | 4,71 | No (ej. YAML) | **PDAR** | Raro | |
| Volvo S60 / V60 | 4,76 | No (ej. YAML) | **PDAR** | Posible | V60 = wagon premium → `PW` |
| Audi A5 Sportback | 4,77 | No | **PDAR** | Posible | |

> **BMW Serie 2 Gran Coupé — NO va aquí.** Causaba colisión con el Serie 3 (dos BMW en P).
> Por tamaño (4,53) es compacto largo, no ejecutivo; su singularidad es la CARROCERÍA
> (four-door coupé), no el tamaño. Baja densidad ("rara vez pero sí" en flota). → se difiere
> a la sección OTROS / casos especiales, junto al Mercedes CLA (mismo perfil). La regla de
> no-colisión de marca queda INTACTA, sin excepción.

**Powertrain:** ejecutivo híbrido/PHEV frecuente → `PDAH`. Eléctrico ejecutivo (i4, etc.)
→ `PDAE`.

### U — Premium grande / ejecutivo superior (~4,9–5,1 m) → ACRISS `U` (Premium Elite)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| BMW Serie 5 | 5,06 | No | **UDAR** | Raro en alquiler | |
| Audi A6 | 4,94 | No | **UDAR** | Raro | |
| Mercedes Clase E | 4,95 | No | **UDAR** | Raro | Frecuente como taxi/VTC premium |
| Volvo S90 | 4,97 | No | **UDAR** | Raro | |

### L — Lujo (~5,1+ m) → ACRISS `L` (Luxury)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Mercedes Clase S | 5,18 | No | **LDAR** | Muy raro | Chófer/VIP |
| BMW Serie 7 | 5,26 | No | **LDAR** | Muy raro | |
| Audi A8 | 5,17 | No | **LDAR** | Muy raro | |

**Aviso de densidad:** `U` y `L` son **muy poco frecuentes** en alquiler vacacional (Alicante).
Materializar por completar la rejilla premium, pero esperar pocos o cero ocupantes. El grueso
real del premium turismo en tu mercado es `D` (compacto premium) y `P` (ejecutivo).

### Coupés / deportivos premium → RESUELTO en OTROS

Las carrocerías especiales ya tienen criterio fijado (ver sección OTROS): four-door
coupés → carrocería `L` (Gran Coupé; Serie 2 GC=JLAR, CLA=JLAR), coupés 2p reales →
`E` (CLE=PEAR), descapotables → `T`, roadsters → `N`. No se tratan en la escala sedán.

---

---

## MONOVOLÚMENES / MPV → ACRISS carrocería `M` (Monospace)

Monovolúmenes de **plataforma turismo** (coche bajo, dinámica de coche, techo alto para
espacio). Carrocería ACRISS `M` (Monospace). **NO confundir con furgoneta pasajero (`V`)**:
el MPV es coche-derivado (Touran, Scenic); la furgoneta es comercial-derivada (Tourneo,
Spacetourer). La frontera es plataforma: car-platform → `M`; van-platform → `V`. Tamaño en
1ª letra según el segmento de la plataforma; premium en Elite (`J` = Intermedio Elite).

Casi todos automáticos hoy; el manual existe pero es minoritario.

### MPV Compacto mainstream → `CM**`

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| VW Touran | 4,53 | No | **CMAR / CMMR** | Posible | El MPV de referencia. Car-platform, NO van |
| Renault Scenic (clásico) | 4,41 | No | **CMAR** | Descatalogado, raro | El nuevo Scenic es SUV eléctrico, ojo |
| Citroën C4 SpaceTourer | 4,60 | No | **CMAR** | Descatalogado, vivo | Ex-C4 Picasso |
| Ford C-Max | 4,38 | No | **CMMR / CMAR** | Descatalogado | Raro ya |
| BMW Serie 2 Active Tourer | 4,39 | No | **DMAR** (premium → Elite) | Posible | Premium → tier Elite. Monovolumen premium |

### MPV Pequeño / "ludospace" (car-derived, ocio-comercial) → `CM**` o frontera con van

Estos son la zona gris: coches-furgoneta pequeños de pasajeros. Tu reference los trataría
como MPV (`M`) si predomina el uso pasajero car-like, o van (`V`) si predomina lo comercial.

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Peugeot Rifter | 4,40 | No (ej. YAML CMMR) | **CMMR / CMAR** | Posible | Ludospace. Frontera M/V |
| Citroën Berlingo (pasajero) | 4,40 | No (ej. YAML) | **CMMR / CMAR** | Posible | Ludospace |
| Renault Kangoo (pasajero) | 4,49 | No (ej. YAML) | **CMMR / CMAR** | Posible | Ludospace |
| VW Caddy (pasajero) | 4,50 | No (ej. YAML) | **CMMR / CMAR** | Posible | Ludospace. (El Caddy Maxi, más largo, tiende a van) |

### MPV Premium → `JM**` (Intermedio Elite Monospace)

| Modelo | Largo aprox | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| BMW Serie 2 Gran Tourer | 4,61 | Sí (JMAR) | **JMAR** | Posible | 5+2, premium. Ya en YAML |
| Mercedes Clase B | 4,42 | Sí (JMAR ej.) | **JMAR** | Posible | Monovolumen premium |

**Líos / decisiones del bloque MPV:**
1. **Touran NO es van.** El YAML ya lo avisa (nota en CMAR). Mantener: car-platform → `M`.
2. **Ludospaces (Rifter/Berlingo/Kangoo/Caddy)** en la frontera M/V. Propuesta: `M` si el
   listado del proveedor enfatiza pasajeros/ocio; `V` si enfatiza carga/comercial. El Caddy
   **Maxi** (más largo, 4,85) tiende a van (`SV`).
3. **Premium MPV → `JM`** (Serie 2 GT, Clase B). Coherente con premium=Elite.
4. Casi todo **automático** hoy; el manual existe pero decae.

---

## FURGONETAS PASAJERO → carrocería `V`, plazas mueven la 1ª letra

Furgonetas de **plataforma comercial** adaptadas a pasajeros (NO MPV car-platform → ver
bloque MPV). **Regla especial del reference:** con carrocería `V`, la 1ª letra NO es
tamaño sino **PLAZAS**: `IV`=6+ · `SV`=7+ · `FV`=7+ amplio · `PV`=8+ · `LV`=9+ ·
`XV`=12+. Elite (premium): `JV`/`RV`/`GV`/`UV`/`WV` respectivamente.

**Validación externa:** Drivalia codifica el Peugeot 5008 (7 plazas grande) = `FVAR` ✓ y
el Ford Transit = `XVMD` (12+, manual, diésel) ✓. Confirma la mecánica de plazas.

### Mainstream por plazas

| Modelo | Plazas | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Ford Tourneo Connect | 7 | Sí (SVAR ej.) | **SVAR / SVMR** | Posible | 7 plazas compacta |
| Citroën Spacetourer / Peugeot Traveller | 7-9 | Sí (SVAR ej.) | **SVAR (7) / LVAR-LVMR (9)** | Posible | El MISMO modelo cambia de código según plazas |
| Renault Trafic Passenger | 8-9 | Sí (PVAR/LVAR ej.) | **PVAR (8) / LVAR-LVMR (9)** | Común en alquiler | El clásico 9 plazas |
| Opel Vivaro Combi | 9 | Sí (LVMR ej.) | **LVMR / LVAR** | Común | |
| Ford Tourneo Custom | 9 | Sí (LVAR ej.) | **LVAR / LVMR** | Común | |
| VW Caravelle / Transporter | 9 | No | **LVAR / LVMR** | Posible | (Sixt lo tiene como "Lujo Van" — nosotros por plazas+marca) |
| Mercedes Vito Tourer | 8-9 | Sí (PVAR/WVAR ej.) | **RVAR (8, Elite) / WVAR (9, Elite)** | Posible | Mercedes → Elite |
| VW Caddy Maxi | 7 | No | **SVAR / SVMR** | Posible | El Caddy largo; el corto es MPV (ver bloque MPV) |
| Peugeot 5008 | 7 | No | **SFAR (decidido)** | Posible | Drivalia=FVAR pero compite con Kodiaq/Santa Fe → SFAR (ver bloque SUV). FV reservado a vans reales |

### Premium (marca premium → letra Elite de su nivel de plazas)

| Modelo | Plazas | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Mercedes Clase V | 7-8 | Sí (RVAR ej.) | **RVAR (7-8 Elite)** | Posible | VIP/chófer |
| Mercedes Vito Tourer 9 | 9 | Sí (WVAR ej.) | **WVAR** | Posible | 9 plazas Elite |

**Líos / decisiones del bloque Furgonetas:**
1. **Las plazas mueven el código.** Un Spacetourer 7 ≠ Spacetourer 9: SVAR vs LVAR. El
   clasificador necesita el dato `seats` (ya está en el modelo de datos ✓).
2. **El 5008: DECIDIDO → `SFAR`** (SUV grande, con Kodiaq/Santa Fe), divergiendo de
   Drivalia (FVAR). La coherencia de agrupación interna manda; `FV` se reserva a vans reales.
3. **Mercedes → Elite** (RV/WV) por lista de marcas premium.
4. **Caddy corto = MPV (`CM`), Caddy Maxi = van (`SV`).** La longitud decide la plataforma.

---

## WAGON / FAMILIAR → carrocería `W`, hereda el tamaño de su PLATAFORMA

Familiares/estate. Regla ya fijada (decisión transversal nº9): **el wagon hereda el tamaño
de su plataforma base, no su longitud absoluta** (la cola no sube de tier). Premium → Elite.

| Modelo | Plataforma | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Kia Ceed SW | Ceed (C) | Sí (IWAR) | **CWMR / CWAR** | Real | **Migra IWAR→CW.** Decidido |
| VW Golf Variant | Golf (C) | No | **CWMR / CWAR** | Posible | |
| Škoda Octavia Combi | Octavia (I) | Sí (IWAR ej.) | **IWAR / IWMR** | Posible | Octavia es I → su wagon es IW ✓ (este sí estaba bien) |
| Škoda Fabia Combi | Fabia (E) | No | **EWMR / EWAR** | Posible | Supermini wagon, raro ya |
| Peugeot 308 SW | 308 (C) | No | **CWMR / CWAR** | Posible | |
| VW Passat Variant | Passat (S) | No | **SWAR** | Posible | (Sixt: "Standard Familiar" ✓ coincide) |
| Audi A4 Avant | A4 (P premium) | No | **PWAR** | Raro | Wagon premium ejecutivo |
| Volvo V60 | S60 (P premium) | No | **PWAR** | Raro | |
| Audi A6 Avant | A6 (U premium) | No | **UWAR** | Muy raro | (Sixt: "Premium Familiar") |

**Líos / decisiones del bloque Wagon:**
1. **Ceed SW y Golf Variant migran de IWAR→CW** (hereda plataforma C). El Octavia Combi
   se queda en IW (su plataforma es I). El IWAR actual mezclaba las dos cosas.
2. Wagon premium → letra Elite + W (PWAR, UWAR). Baja densidad; materializar plausibles.

---

## OTROS / CASOS ESPECIALES

### Descapotables → carrocería `T` (4 plazas) / `N` (roadster 2 plazas)

Tamaño en 1ª letra según plataforma (mismo criterio que wagon); premium → Elite.

| Modelo | Plataforma | ¿Hoy? | ACRISS futuro | Estado en flota | Notas |
|---|---|---|---|---|---|
| Fiat 500C | 500 (M) | Sí (ETMR/ETAR ej.) | **MTMR / MTAR** | Posible | Migra de E a M (con su hatch) |
| Mini Cabrio | Mini (D Elite) | Sí (ETMR ej.) | **DTAR / DTMR** | Posible | Premium → Elite. (Sixt: "Económico Descapotable" — nosotros premium) |
| VW T-Roc Cabrio | T-Roc (C SUV) | No | **CTAR** | Raro | (Sixt: "Compacto Descapotable" ✓) |
| BMW Serie 4 Cabrio | Serie 4 (P/U) | No | **PTAR / UTAR** | Muy raro | Premium grande |
| Mazda MX-5 | roadster 2 plazas | No | **(roadster `N`)** | Muy raro | Carrocería N, no T. Solo si aparece |

**Regla:** descapotable 4 plazas → `T`; roadster 2 plazas → `N` (carrocería, no confundir
con la `N` de 1ª letra Mini Elite). El 500C migra con su hermano hatch a `M`.

### 2-Ruedas → carrocería `Y`

| Modelo | ¿Hoy? | ACRISS futuro | Notas |
|---|---|---|---|
| Scooters 125cc (PCX, NMAX, Vespa...) | Sí (EYNR) | **EYNR** | Se queda como está. Funciona |

### Four-door coupés premium → CERRADO con dato de Sixt

**Sixt resuelve el caso que teníamos abierto:** clasifica el BMW Serie 2 Gran Coupé como
"**Intermedio Gran Coupé**" — es decir, usa la carrocería **`L` (Gran Coupé 4 puertas)** de
la tabla canónica, con tamaño Intermedio. Adoptamos ese criterio:

| Modelo | ¿Hoy? | ACRISS futuro | Notas |
|---|---|---|---|
| BMW Serie 2 Gran Coupé | No | **JLAR** | `J` (Intermedio Elite, premium) + `L` (Gran Coupé). Ya no colisiona con Serie 3 (PDAR): distinta carrocería Y distinta tier |
| Mercedes CLA | No | **JLAR** | Mismo perfil |
| BMW Serie 4 Gran Coupé | No | **PLAR / ULAR** | Más grande (Sixt: "Fullsize Gran Coupé") |
| Mercedes CLE Coupé | No | **PEAR** | Coupé 2 puertas real → carrocería `E` (Sixt: "Premium Coupé") |

**Resuelto:** la carrocería `L` (Gran Coupé) existe en la tabla canónica y es exactamente
para esto. El Serie 2 GC deja de ser un problema: `JL` lo separa del Serie 1 (`DD`) y del
Serie 3 (`PD`) por carrocería Y tier a la vez. La regla de no-colisión queda intacta.

---

## Decisiones transversales (estado)

1. **ACRISS canónico vs propio** → **REVISADO: NO existe un "ACRISS canónico" único.**
   Sixt y Drivalia clasifican el mismo coche distinto (GLA: Drivalia/reference→DF Elite;
   Sixt→Intermedio+badge). La taxonomía es PROPIA, consistente consigo misma, usando ACRISS
   como vocabulario. Objetivo real: **agrupar coches comparables entre proveedores para
   cruzar precios**, no "acertar" la etiqueta oficial. `display_name` en español.
2. **Escala desplazada** → **RESUELTO: se mete la `M`** y baja la gama un peldaño
   (A→M, B→E, C→C).
3. **Criterio de frontera** → **RESUELTO.** (a) Definición de cada ACRISS fija a priori
   por segmento+tamaño, independiente de su población (evita el bucle de "vecinos").
   (b) Un coche se clasifica según esa definición; los vecinos son validación, no
   requisito. (c) Heurística de apoyo del usuario: si la marca tiene un modelo por
   debajo, el coche va al escalón inmediatamente superior al de ese inferior; si no,
   se valida con los pares de otras marcas del mismo segmento en el ACRISS candidato.
   (d) **Regla relacional / no-colisión de marca:** dos modelos de la MISMA marca no
   comparten ACRISS sin justificación fuerte; el superior ocupa su tier y DESPLAZA al
   inferior al escalón de abajo (aplica dentro del mismo eje de carrocería). Esto hace
   que la frontera sea relativa (posición en la gama), no solo absoluta (cm).
   Casos cerrados: Yaris→E, Swift→E, A1→H, Serie2 desplaza desde D→P,
   Octavia→I (desplazado por Superb que ocupa S).
4. **Granularidad** → **RESUELTO: ir FINO, hasta donde ACRISS lo defina, ni un grado más.**
   Ejes: tamaño × carrocería × transmisión × powertrain (4º char: R/H/E...). NO inventar
   distinciones fuera de la rejilla ACRISS. La clasificación (dato) es fina siempre; la
   agrupación es cosa de la PRESENTACIÓN (vista) y del mapeo a `client_vehicle_groups`.
   Fino es reversible (se agrupa al mostrar); grueso es irreversible sin re-clasificar.
5. **Materialización** → **CAMBIO DE ESTRATEGIA: materializar la rejilla ACRISS plausible
   completa**, aunque algunos códigos tengan pocos/cero ocupantes hoy (p.ej. `H`, `N`),
   para no reabrir el catálogo cada vez que llega un coche nuevo. "Plausible" = lo que el
   mercado ES (alquiler España) verá en 2-3 años; no todas las combinaciones matemáticas.
   (Esto sustituye la política previa de "materializar solo bajo demanda".)
6. **Modelos en bundle:** regla del segmento inferior (el bundle se clasifica por su
   coche más bajo). Pendiente formalizar en el clasificador (GeminiClassificationService).
7. **Modelos descatalogados pero vivos en flota:** incluirlos en los ejemplos del YAML.
8. **Crossovers:** todos los crossover/SUV (incluidos los crossover-B tipo Fiat 600,
   Aygo X, Jeep Avenger) se consolidan en la sección Crossovers, no en los hatchback.
9. **Wagon (`W`) vs MPV (`M`) son ejes paralelos, NO una jerarquía.** Golf Variant (wagon,
   coche bajo con maletero largo) → `CW`. Touran (MPV, caja alta 5+2) → `CM`. No van juntos
   ni uno "por encima" del otro: distinta carrocería ACRISS. La regla de "el superior
   desplaza" aplica dentro de un mismo eje de carrocería, no entre ejes.
   **Frontera wagon:** un wagon hereda el tamaño de su PLATAFORMA base, no su longitud
   absoluta. Ceed SW (4,60) = Compact Wagon `CW` porque el Ceed es C, aunque la cola lo
   alargue. Golf Variant igual → `CW`. (Hoy ambos mal en `IWAR`/Intermediate Wagon.)
10. **Intermediate (`I`) es tier dudosa en este catálogo:** históricamente cajón de edge
    cases mal clasificados. Al llegar al bloque, evaluar si se materializa mínima o si el
    mercado salta de Compact a Standard. Los compactos largos (Civic, Mazda3) se quedan en
    C por segmento, no suben a I por el cm.
11. **Grano de agrupación = código COMPLETO de 4 letras.** Manual ≠ auto, combustión ≠
    eléctrico cuentan como grupos de precio distintos. Dos coches de proveedores distintos
    son comparables si y solo si comparten las 4 letras.
12. **Premium = tier Elite en 1ª letra (Forma B).** El premium vive en la categoría
    (C/D, I/J, S/R, P/U...), NO en un atributo aparte. GLA→DFAR, A3→DDAR, X3→RFAR. Coherente
    con el `acriss_reference.md`. (Sixt usa otra mecánica —tamaño físico + badge— pero no la
    copiamos; nuestra taxonomía es propia.)
13. **Marca premium = lista cerrada determinista**, no decisión del LLM. Audi, BMW, Mercedes,
    Volvo, Lexus, Mini, DS, Porsche, Land Rover, Jaguar → tier Elite. **Cupra = MAINSTREAM**
    (decidido: Sixt no le pone badge premium al León/Formentor). Quita al
    clasificador la decisión difícil (la marca decide la tier).
14. **2ª letra de los SUV = `F` (no `G`).** El reference define `G` (crossover) pero Drivalia
    colapsa todo en `F` (C3 Aircross=CFMR, Qashqai=IFMR). Adoptamos `F` para máxima cobertura
    de agrupación. **Migración masiva CG→CF.** Los códigos con `G` se eliminan de la rejilla.
15. **Frontera por SEGMENTO, no por cm estricto.** El segmento/posicionamiento comercial
    manda; los cm del reference son orientativos. Un Focus (4,38) y un Golf (4,28) van juntos
    en C aunque difieran en cm. Esto corrige la debilidad del reference (rangos de cm que no
    cuadran con sus propios ejemplos).
16. **Validación de oro = códigos reales de Drivalia** (competidor que publica ACRISS en
    Alicante): EDMR(208), EDAR(MG3), CDMR(Focus), CDAR(C4), CFMR(C3 Aircross), IFMR(Qashqai),
    IFAR(3008), DDAR(Mini Cooper), MBAE(500e). El clasificador debe reproducirlos tras
    realinear. Mercado acotado (<100 modelos) → lista de ejemplos generosa es viable.
17. **Bundles (PVCs con modelos de tiers distintos): el INFERIOR EN VALOR manda.** En
    rent-a-car, "o similar" garantiza solo el peor coche del grupo → el grupo se compara
    por su suelo. PRECISIÓN (detectada en producción con el bundle A1+Focus+Astra): el
    inferior se mide en VALOR de mercado, no en posición de letra — la escala intercalada
    (H<C) ordena tamaño+gama, no precio. Por eso, en bundles mixtos premium+mainstream,
    **el premium se DESCARTA primero** (nunca es el suelo garantizado: el "o similar"
    entrega el mainstream, no el premium); entre los mainstream restantes manda el tier
    inferior. Solo si todos los modelos son premium se aplica la escala Elite entre ellos.
    Casos: A1+Focus+Astra → descarta A1 → CDMR (no HDMR). Tiguan+T-Roc (mainstream puro)
    → CFAR. El PVC bundle NO se parte (un grupo = un precio); se clasifica por el suelo
    y queda pending_review para confirmación del operador.

---

## RESUMEN DE MIGRACIÓN — mapa código viejo → nuevo (para ejecutar en una pasada)

> Consolidado de todos los cambios. La migración toca: `acriss_codes.yaml` (rejilla nueva),
> `acriss_reference.md` (añadir lista de marcas premium + regla "segmento manda sobre cm"),
> `_MIXED_GROUPS_REMINDER` en el clasificador (purgar `G` de carrocería), reseed,
> reclasificación masiva vía LLM, y revisión de referencias a códigos viejos
> (pricing_rules, mapeos de tenant, vista). UNA SOLA PASADA — los códigos viejo/nuevo solapan.

### A. Renombrados de código (la misma población cambia de letra)

| Código HOY | Código NUEVO | Población afectada |
|---|---|---|
| EDMR / EDAR (city cars) | **MDMR / MDAR** | 500, Panda, Picanto, i10, Aygo |
| EDAE (eléctrico pequeño) | **MBAE** (500e, 2-3p) y **EDAE** (e-208, Zoe) | partir por tamaño |
| CDMR / CDAR (superminis) | **EDMR / EDAR** | Polo, Corsa, 208, Clio, Ibiza, Yaris, Swift |
| IDMR / IDAR (compactos) | **CDMR / CDAR** | Focus, Astra, 308, i30 |
| CGMR / CGAR / CGAH (SUV urbano) | **partir: EFMR/EFAR (mini-SUV: Arona, Bayon, Puma, Crossland) y CFMR/CFAR/CFAH (SUV compacto: T-Roc, Kamiq, MG ZS, XCeed)** | por escalón de marca |
| IGAR (crossover intermedio) | **eliminar** (población migra: 2008→CFAR, Astra→CDAR/CDMR) | bundle 2008+Astra |
| IWAR (wagon, mezclado) | **CWMR/CWAR** (Ceed SW, Golf Variant) y **IWAR/IWMR** (Octavia Combi) | partir por plataforma |
| ETMR / ETAR (descapotable econ.) | **MTMR / MTAR** (500C) y **DTAR/DTMR** (Mini Cabrio) | partir por tamaño/marca |

### B. Reubicaciones de modelo concreto (cambio de código por error actual)

| Modelo | HOY | NUEVO | Motivo |
|---|---|---|---|
| Audi A1 | IDMR / DDAR | **HDMR / HDAR** | Economy Elite (su sitio canónico) |
| Audi Q2 | DFAR / DFMR | **HFAR / HFMR** | SUV pequeño premium (espejo SUV del A1); no colisiona con Q3 |
| Fiat 600 | CDAR | **EFAR** | Es mini-SUV, no turismo |
| Peugeot 2008 | IGAR (bundle) | **CFAR** | SUV compacto; deshacer bundle |
| SEAT Ateca | (IF previsto) | **CFAR** | Por escalón SEAT: sobre Arona, bajo Tarraco |
| C3 Aircross | — | **EFMR** | ⚠️ Diverge de Drivalia (CFMR): nuestra rejilla es más fina |
| Opel Astra | IDMR + IGAR (dup) | **CDMR / CDAR** | Turismo C; resolver duplicado |
| VW ID.3 | EDAE (ej.) | **CDAE** | Es compacto, no mini |
| Mustang Mach-E | CFAE | **IFAE** | Mediano mainstream (Ford no premium). DECIDIDO |
| Škoda Octavia | SDAR (ej.) | **IDAR** | Desplazado por Superb (regla relacional) |
| Mini Cooper | (apéndice N) | **DDAR / DDMR** | Drivalia lo valida en DDAR |

### C. Códigos nuevos a materializar (no existen hoy)

| Código | Qué es | Ejemplos ancla |
|---|---|---|
| MDMR / MDAR / MBAE / MTMR / MTAR | Mini (city cars + 500e + 500C) | 500, Panda, Aygo, 500e, 500C |
| HDMR / HDAR | Economy Elite | A1, Mini 5p, DS3, Ypsilon |
| HFAR / HFMR | SUV pequeño premium (Economy Elite SUV) | Q2, DS3 Crossback |
| UFAR | SUV grande premium (Premium Elite SUV) | GLE, X5, Q7, Q8, XC90 |
| LFAR | SUV lujo / todoterreno lujo | Range Rover, Clase G, Defender, DS7 (Drivalia=LFAR ✓) |
| WFAR | SUV gigante 7pl lujo | GLS, X7 |
| CDAE / CDAH | Compact eléctrico / híbrido | ID.3, Born, MG4 / Corolla HEV |
| CFMR / CFAR / CFAH / CFAE | SUV Compacto (escalón 2) | T-Roc, Kona, 2008, C-HR, XCeed |
| EFMR / EFAR / EFAH / EFAE | SUV Urbano / mini-SUV (escalón 1) | Arona, Bayon, T-Cross, Yaris Cross, Fiat 600 |
| SFAR / SFAH | SUV Grande 7pl mainstream | Kodiaq, Santa Fe, Tarraco, 5008 |
| IDAR (re-significado) | Intermedio turismo (solo Octavia) | Octavia |
| IFAH / IFAE | SUV Mediano híbrido / eléctrico | Tucson HEV / ID.4 |
| JMAR (existe) / DMAR | MPV premium | Serie 2 GT / Active Tourer |
| JLAR | Four-door coupé premium | Serie 2 GC, CLA |
| PEAR / PWAR / UWAR / PTAR | Coupé/wagon/cabrio premium | CLE / A4 Avant, V60 / A6 Avant / S4 Cabrio |
| CMAR/CMMR (existen) | MPV compacto | Touran, Rifter, Berlingo |
| CWMR / CWAR / EWMR / SWAR | Wagons por plataforma | Ceed SW, Golf Variant / Fabia Combi / Passat Variant |
| CTAR | Descapotable compacto | T-Roc Cabrio |
| FVAR (existe vía 5008) | Van amplio 7 plazas | Peugeot 5008 (Drivalia=FVAR) |
| UDAR / LDAR | Premium Elite / Lujo sedán | Serie 5, A6 / Clase S |
| SDAH | Standard híbrido | Camry |

### D. Códigos que se quedan igual (verificados)

`EDMR/EDAR` (re-poblado con superminis), `CDMR/CDAR` (re-poblado con compactos),
`IFAR/IFMR` (SUV mediano — Drivalia valida), `DFAR/DFMR` (SUV compacto premium),
`RFAR` (SUV mediano premium, GLB incl.), `PDAR` (premium ejecutivo), `SDAR` (standard),
`DDAR/DDMR` (compact elite, A3/Serie1/ClaseA/Mini), `JMAR`, `SVAR/PVAR/LVAR/LVMR/RVAR/WVAR`
(furgonetas — ojo regla plazas), `CMMR/CMAR` (MPV), `EYNR` (scooter).

### E. Cambios fuera del YAML

1. **`acriss_reference.md`:** añadir (a) lista cerrada de marcas premium → tier Elite
   (Audi, BMW, Mercedes, Volvo, Lexus, Mini, DS, Porsche, Land Rover, Jaguar; Cupra=NO,
   es mainstream); (b) regla "el segmento comercial manda sobre el cm"; (c) regla de plazas en
   vans ya está ✓; (d) aviso de que `G` (crossover) NO se usa en nuestra rejilla (colapsa
   a `F`).
2. **`_MIXED_GROUPS_REMINDER` (gemini_classification_service.py):** purgar `G` de la
   jerarquía de carrocería y de la tabla de tiers; alinear ejemplo Tiguan+T-Roc (T-Roc
   ahora = CFAR, no CGAR).
3. **Revisión de consumidores de códigos:** pricing_rules, vehicle_group_mappings de
   tenants, vista (display_names: "SUV Urbano"→"SUV Compacto", etc.).
4. **display_name nuevos coherentes:** Mini/Económico/Compacto/Intermedio/Estándar/
   Premium + "SUV Compacto/Mediano" + "Premium" como sufijo de marca en Elite.

### F. Validación final (criterio de "hecho")

Tras reseed + reclasificación, el clasificador debe reproducir los códigos de Drivalia:
208→EDMR · MG3→EDAR · Focus→CDMR · C4→CDAR · Qashqai→IFMR · 3008→IFAR · 5008→SFAR (divergencia deliberada nº2; Drivalia=FVAR) · Mini Cooper→DDAR · 500e→MBAE. **Divergencia deliberada:** C3 Aircross →
EFMR en nuestra rejilla (Drivalia=CFMR; nuestra escala SUV es más fina con el peldaño EF).
Y los casos propios verificados en fases a/b: GLB→RFAR, GLA→DFAR, A3→DDAR, Tucson
manual→IFMR, Kuga manual→IFMR.
Cualquier divergencia se revisa antes de dar la migración por buena.


---

## ESTADO: MIGRACIÓN EJECUTADA ✓

La Fase 1 (ficheros) y la Fase 2 (reseed + reclasificación masiva + borrado de huérfanos)
se ejecutaron con éxito. Resultado:

- **72 códigos** materializados en `acriss_codes` (los 8 viejos —CGMR, CGAR, CGAH, IGAR,
  ETMR, ETAR, EDMH, FVAR— borrados físicamente, 0 referencias residuales).
- **Casos de oro presentes en flota: todos ✓** (208→EDAR, Golf→CDAR, T-Roc→CFAR,
  Arona→EFMR, GLB→RFAR, GLA→DFAR, A3→DDAR, A1→HDAR, Q2→HFAR/HFMR, 500e→MBAE,
  Fiat 600→EFAR, Tucson/Kuga manual→IFMR, Mach-E→IFAE, Ceed SW→CWAR...).
- **Validación pendiente de primera aparición** (sin PVC en flota aún): Focus, Qashqai,
  3008, 5008 (divergencia deliberada SFAR), C3 Aircross (divergencia deliberada EFMR), MG3.
- **3 bundles mixtos** corregidos a mano con la regla nº17 (inferior manda):
  A1+Focus+Astra→HDMR · 2008+Astra→CD** · Tiguan+T-Roc→CFAR.
- **Tier-order fix** aplicado al `_MIXED_GROUPS_REMINDER` y al reference: escala completa
  intercalada (M < N < E < H < C < D < I < J < S < R < P < U < L < W) + regla del inferior.
- Tenant mappings: 0 afectados. Backup pre-migración conservado.

Este documento queda como **registro de criterio**: las fronteras, reglas y ejemplos que
alimentan `acriss_codes.yaml` y `acriss_reference.md`. Cualquier cambio futuro de
taxonomía pasa por actualizar este documento primero (mismo principio que DATA_MODEL.md).
