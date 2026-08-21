# RentRadar — API (v1) · Guía de integración

Documento único y autocontenido para implementar el **consumidor** de la API: el
sistema externo que va a **copiar los precios** calculados por RentRadar e
inyectarlos en su propio motor (booking, ERP, web, etc.).

> **TL;DR:** todo se consume con un `GET` autenticado por API key, en JSON, de
> **solo lectura** (*pull*; no hay push/webhook).

### Endpoints

| Endpoint | Para qué | Sección |
|----------|----------|---------|
| `GET /api/v1/prices` | Precios finales de venta por **categoría ACRISS × temporada × duración**, con procedencia. | §1–§10 |
| `GET /api/v1/classify` | Clasifica un **modelo en texto libre** ("Peugeot 208 Manual") a su código ACRISS. | §11 |
| `GET /api/v1/provider-groups` | **Catálogo** de grupos de proveedor con su **cobertura** de precios hacia delante. | §12 |

Ambos comparten **autenticación** (§2) y el concepto de **código ACRISS** (§4, §9).

---

## 1. Qué te devuelve esta API

Los **precios finales de venta del tenant** — es decir, RentRadar ya ha cruzado
los precios de los competidores y ha aplicado la regla de pricing configurada por
el cliente (markup, suelo/techo, redondeo…). **Lo que recibes es el precio que se
debe publicar tal cual**; no tienes que aplicar ninguna lógica de markup encima.

No recibes datos crudos de competidores ni configuración interna: solo el
resultado accionable.

---

## 2. Autenticación

- Cada tenant tiene una o varias **API keys** (token largo tipo `rr_live_…`).
- Se envía en cada petición en la cabecera HTTP:

  ```
  Authorization: Bearer rr_live_xxxxxxxxxxxxxxxxxxxxxxxx
  ```
  (También se acepta `X-API-Key: rr_live_…`.)

- La key **identifica al tenant**: no hay que enviar ningún `tenant_id`; los
  precios devueltos son siempre los de ese tenant.
- La key se entrega **una sola vez** al crearla y no se puede volver a mostrar.
  Guárdala como un secreto (variable de entorno / gestor de secretos). Si se
  pierde o se filtra, el operador de RentRadar la **revoca** y emite otra.

**Cómo obtener una key:** la genera el operador de RentRadar con
`scripts/create_api_key.py` y te la entrega de forma segura.

---

## 3. Endpoint

```
GET https://radar.mardrive.com/api/v1/prices
```

### Parámetros (query string, todos opcionales)

| Parámetro | Tipo | Por defecto | Descripción |
|-----------|------|-------------|-------------|
| `location_id` | int | todas | Restringe a un mercado/ubicación canónica concreta. Si se omite, devuelve todas las ubicaciones mapeadas. |
| `zone_from` | int | 0 | Índice de temporada inicial (0 = la más próxima). Inclusive. |
| `zone_to` | int | última | Índice de temporada final. Inclusive. |

Para "copiar todo el calendario" no pases nada: `GET /api/v1/prices`.

### Respuesta — nivel raíz

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `tenant` | string | Nombre del tenant. |
| `currency` | string | Moneda ISO-4217 de todos los precios (p. ej. `EUR`). |
| `generated_at` | string (ISO-8601 UTC) | Momento en que se generó esta respuesta. |
| `location_id` | int \| null | El filtro aplicado (null = todas). |
| `durations` | int[] | Duraciones (días) que el sistema maneja: `[1,2,3,4,5,6,7,14,21,28]`. |
| `total_zones` | int | Nº total de temporadas disponibles (independiente del rango pedido). |
| `providers` | objeto[] | Proveedores en el radar: `[{code, name}]`. Son los `code` que aparecen en `provenance`. |
| `prices` | objeto[] | Lista de precios. Ver abajo. |

### Respuesta — cada elemento de `prices`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `acriss_code` | string (4) | **Código ACRISS** de la categoría de vehículo (la clave para mapear a tus categorías). |
| `category` | string | Nombre legible de la categoría (informativo). |
| `example_models` | string[] | Modelos de ejemplo representativos de la categoría (p. ej. `["Fiat 500", "Opel Corsa", …]`). Lista del catálogo, constante entre temporadas; `[]` si no hay. Informativo (para mostrar "o similar"); no es la clave de mapeo. |
| `zone.index` | int | Índice de la temporada (0 = la más próxima). |
| `zone.date_from` | date (`YYYY-MM-DD`) | Inicio de la temporada (inclusive). |
| `zone.date_to` | date (`YYYY-MM-DD`) | Fin de la temporada (inclusive). |
| `prices_total` | objeto | `{ "<días>": precio_total }` — **precio total del alquiler** para esa duración. |
| `prices_per_day` | objeto | `{ "<días>": precio_por_día }` — precio por día (= total / días). |
| `provenance` | objeto | **Procedencia** por duración: `{ "<días>": { base_provider, base_total, by_provider } }`. De dónde sale cada precio recomendado (ver abajo). |

**Procedencia (`provenance[<días>]`)** — para informar al cliente del origen de cada precio, igual que el SaaS:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `base_provider` | string \| null | `code` del proveedor cuyo precio fija la base del recomendado. `null` si la base es una agregación sin un proveedor único (p. ej. media). |
| `base_total` | number \| null | Precio total de ese `base_provider` (antes de aplicar tu regla de pricing). |
| `by_provider` | objeto | `{ "<code>": { total, model, external_code, groups } }` — por **cada** proveedor con dato en esa celda: su precio `total`, el/los `model`(s) que lista para la categoría, el `external_code` del grupo al que corresponde ese `total`, y `groups`: el desglose sin colapsar (ver abajo). |

> **Sobre `external_code`.** Es el código propio del proveedor para el grupo
> ("Grupo A", "FR", "D2"). Un proveedor puede tener **varios grupos** dentro de
> la misma categoría ACRISS: en ese caso `total` es el del más barato y
> `external_code` nombra ese grupo, mientras que `model` sigue listando los
> modelos de todos. Es `null` si el proveedor no expone códigos de grupo.

**Desglose por grupo (`by_provider[<code>].groups`)** — la vista sin colapsar:
una entrada por cada grupo del proveedor con precio en esa celda, **el más
barato primero**. Úsala para leer el precio de un grupo concreto aunque no sea
el más barato de su proveedor (imprescindible si tu sistema empareja contra
grupos específicos):

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `group_key` | string | Identificador estable del grupo — el mismo que devuelve `/api/v1/provider-groups` (§12). |
| `models` | string[] | Modelos que el proveedor lista para **ese** grupo. |
| `total` / `per_day` | number | Precio de ese grupo (antes de tu regla de pricing). |
| `is_base` | boolean | `true` en el grupo del que se derivó el precio recomendado. |

> **Compatibilidad:** `total`, `model` y `external_code` no han cambiado;
> `groups` es un campo añadido — un consumidor existente puede ignorarlo.

Hay **una entrada por cada (categoría ACRISS × temporada)**. En el ejemplo real:
437 entradas = 32 categorías a lo largo de 15 temporadas.

---

## 4. Semántica imprescindible (léelo antes de implementar)

1. **Los precios son finales.** Cópialos tal cual; no apliques markup.
2. **Clave de mapeo = `acriss_code`.** Es un estándar de la industria (4 letras,
   p. ej. `MDMR`, `CDMR`). Mapea cada código ACRISS a tu categoría interna. El
   campo `category` es solo texto de ayuda, **no** lo uses como clave.
3. **Las "zonas" son temporadas de precio.** Cada zona es un rango de fechas
   `[date_from, date_to]` con precio homogéneo. Para un día de recogida concreto,
   busca la zona cuyo rango lo contiene.
4. **Calendario hacia adelante.** Solo hay fechas futuras (desde ~hoy en
   adelante). No hay histórico ni precios para fechas pasadas.
5. **`duración` = días de alquiler.** Solo se publican las del bracket
   `{1,2,3,4,5,6,7,14,21,28}`. Para una duración intermedia (p. ej. 10 días) la
   API **no** da un valor; aplica tu propia interpolación si la necesitas.
6. **No todas las duraciones aparecen en cada entrada.** Si una categoría no tuvo
   dato para una duración en esa temporada, **esa clave no estará** en
   `prices_total`/`prices_per_day` (ausente = sin dato; nunca verás `null`).
   Tu código debe tolerar duraciones faltantes.
7. **Usa `prices_total` como verdad.** `prices_per_day` es `total / días` sin
   redondear (puede traer muchos decimales). Si muestras precio/día, redondéalo tú.
8. **Precios en `currency`** (una sola moneda por tenant).

---

## 5. Errores

| Código | Significado |
|--------|-------------|
| `200` | OK. Cuerpo = JSON descrito arriba (puede tener `prices: []` si aún no hay datos). |
| `401` | Falta la API key, o es inválida o ha sido revocada. |
| `404` | Ruta incorrecta (revisa la URL / versión). |

Cuerpo de error: `{ "detail": { "error": "..." } }`.

---

## 6. Ejemplo

### Petición
```bash
curl -H "Authorization: Bearer rr_live_xxxxxxxxxxxxxxxx" \
     "https://radar.mardrive.com/api/v1/prices"
```

### Respuesta (recortada)
```json
{
  "tenant": "Mardrive",
  "currency": "EUR",
  "generated_at": "2026-06-19T12:52:13.572212+00:00",
  "location_id": null,
  "durations": [1, 2, 3, 4, 5, 6, 7, 14, 21, 28],
  "total_zones": 15,
  "providers": [
    { "code": "centauro", "name": "Centauro" },
    { "code": "solcar", "name": "Solcar" },
    { "code": "victoria", "name": "Victoria Rent a Car" }
  ],
  "prices": [
    {
      "acriss_code": "MDMR",
      "category": "Pequeño Manual",
      "example_models": ["Fiat 500", "Fiat Panda", "Kia Picanto", "Toyota Aygo", "VW up!"],
      "zone": { "index": 0, "date_from": "2026-06-21", "date_to": "2026-06-27" },
      "prices_total": {
        "1": 48.37, "2": 63.36, "3": 89.21, "4": 112.43, "5": 173.49,
        "6": 197.23, "7": 230.10, "14": 483.39, "21": 754.07, "28": 1024.76
      },
      "prices_per_day": {
        "1": 48.37, "2": 31.68, "3": 29.736666666666668, "4": 28.1075,
        "5": 34.698, "6": 32.87166666666667, "7": 32.871428571428574,
        "14": 34.527857142857144, "21": 35.908095238095235, "28": 36.598571428571425
      },
      "provenance": {
        "7": {
          "base_provider": "centauro",
          "base_total": 230.10,
          "by_provider": {
            "centauro": {
              "total": 230.10, "model": "Fiat 500 / Kia Picanto", "external_code": "Grupo A",
              "groups": [
                { "group_key": "Grupo A",  "models": ["Fiat 500"],
                  "total": 230.10, "per_day": 32.87, "is_base": true },
                { "group_key": "Grupo A1", "models": ["Kia Picanto"],
                  "total": 236.48, "per_day": 33.78, "is_base": false }
              ]
            },
            "solcar":   { "total": 255.85, "model": "Fiat Panda, Kia Picanto", "external_code": "Grupo B",
                          "groups": [ { "group_key": "Grupo B", "models": ["Fiat Panda", "Kia Picanto"],
                                        "total": 255.85, "per_day": 36.55, "is_base": false } ] },
            "victoria": { "total": 238.00, "model": "Fiat Panda Hybrid", "external_code": "FR",
                          "groups": [ { "group_key": "FR", "models": ["Fiat Panda Hybrid"],
                                        "total": 238.00, "per_day": 34.00, "is_base": false } ] }
          }
        }
      }
    }
  ]
}
```

---

## 7. Cómo consumirlo (pseudo-código de referencia)

Objetivo típico: para una **(fecha de recogida, duración, categoría)**, obtener el
precio total a publicar.

```python
import requests
from datetime import date

API = "https://radar.mardrive.com/api/v1/prices"
KEY = "rr_live_xxxxxxxxxxxxxxxx"

data = requests.get(API, headers={"Authorization": f"Bearer {KEY}"}).json()

def precio_total(acriss_code: str, pickup: date, duracion_dias: int):
    for row in data["prices"]:
        if row["acriss_code"] != acriss_code:
            continue
        desde = date.fromisoformat(row["zone"]["date_from"])
        hasta = date.fromisoformat(row["zone"]["date_to"])
        if desde <= pickup <= hasta:
            # clave faltante = sin dato para esa duración
            return row["prices_total"].get(str(duracion_dias))
    return None

# Ejemplo: pequeño manual (MDMR), recogida 22/06/2026, 7 días
print(precio_total("MDMR", date(2026, 6, 22), 7))   # -> 230.10
```

Patrón recomendado:
1. **Descarga una vez** el JSON completo (`GET /api/v1/prices`) y trabájalo en
   memoria; no llames al endpoint por cada precio.
2. Indéxalo en una estructura `{(acriss_code, zona): {duración: total}}` o agrupa
   por categoría para búsquedas rápidas.
3. Para cada combinación que necesites publicar, localiza la zona que cubre la
   fecha y lee `prices_total[str(duración)]`.

---

## 8. Notas operativas

- **Frecuencia de actualización.** Los precios se recalculan tras cada scrape
  (programado **L/X/V**). Usa `generated_at` para saber la frescura. Una sincronía
  diaria del consumidor es más que suficiente; no tiene sentido pollear cada
  pocos minutos.
- **Idempotencia.** Es un `GET` de solo lectura: puedes llamarlo tantas veces como
  quieras sin efectos secundarios.
- **Versionado.** La ruta incluye `/v1`. Cambios incompatibles irían en `/v2`; este
  contrato `v1` se mantiene estable.
- **Gestión de keys.** Si necesitas rotar la key (o crees que se ha filtrado),
  pide al operador que la **revoque** y emita una nueva. Una key revocada empieza a
  devolver `401` de inmediato.
- **Tamaño.** La respuesta completa puede pesar varios cientos de KB (cientos de
  combinaciones categoría × temporada). Si solo te interesa una ubicación o unas
  temporadas, filtra con `location_id` / `zone_from` / `zone_to`.

---

## 9. Ciclo de vida de los códigos ACRISS

Los `acriss_code` son **identificadores estables** y la clave con la que debes
mapear tus categorías. Garantías y matices:

- **No se borran ni se reutilizan.** Un código nunca cambia de significado ni se
  elimina; como mucho se "retira" (deja de asignarse). Puedes mapear por
  `acriss_code` de forma permanente y fiarte de ello.
- **Un código puede quedarse sin precio en una respuesta.** Dos causas, que desde
  la API se ven igual (el código simplemente **no aparece** en `prices`):
  (a) **transitorio** — ningún vehículo de esa clase disponible ese ciclo (vuelve
  solo cuando reaparece); (b) **permanente** — el código se retiró del catálogo.
- **El contenido de un código puede cambiar.** Una recategorización puede mover
  modelos entre códigos (p. ej. un ludospace que antes salía en `CMAR` ahora sale
  en `CVAR`). `CMAR` sigue existiendo, pero su conjunto de vehículos cambió. Mapea
  contra el **código**, no contra "el modelo que esperabas ahí".

**La detección de huecos es responsabilidad del consumidor** — tu mapping vive en
tu sistema; RentRadar no lo conoce y no puede vigilarlo. Patrón recomendado:

1. Mantén tu tabla `categoría_interna → acriss_code`.
2. En cada sincronización, comprueba si el `acriss_code` de cada categoría mapeada
   aparece en `prices`.
3. Si **no** aparece: trátalo como **sin actualización** (mantén el último precio
   publicado) y, opcionalmente, **alerta internamente** para revisar el mapping.
   **Nunca** borres precios por una ausencia puntual.

## 10. Checklist de integración

- [ ] Recibir y guardar la API key de forma segura (no en el código fuente).
- [ ] Implementar la llamada `GET /api/v1/prices` con la cabecera `Authorization`.
- [ ] Mapear cada `acriss_code` a tu categoría interna de vehículo.
- [ ] Resolver, por fecha de recogida, la **zona** que la cubre.
- [ ] Leer `prices_total[str(duración)]`, tolerando duraciones ausentes.
- [ ] Detectar categorías mapeadas cuyo `acriss_code` no viene en la respuesta (sin-dato → mantener último / alertar; nunca borrar). Ver §9.
- [ ] Tratar `401` (key inválida/revocada) y reintentos/errores de red.
- [ ] Programar la sincronización (p. ej. diaria) y registrar `generated_at`.

---

## 11. Endpoint auxiliar: clasificar un modelo

**`GET /api/v1/classify?model=<texto>`** — dado un modelo en texto libre, devuelve su
**código ACRISS** + descripción + ejemplos. Misma autenticación (API key Bearer).

### Petición
```bash
curl -H "Authorization: Bearer rr_live_xxxx" \
     "https://radar.mardrive.com/api/v1/classify?model=Peugeot%20208%20Manual"
```

### Respuesta 200
```json
{
  "model": "Peugeot 208 Manual",
  "acriss_code": "EDMR",
  "description": "Económico Manual",
  "example_models": ["VW Polo", "Opel Corsa", "Renault Clio"],
  "confidence": 0.95,
  "pending_review": false,
  "cached": true
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `acriss_code` | string \| null | Código ACRISS clasificado; `null` si no se pudo clasificar. |
| `description` | string \| null | Nombre legible de la categoría (igual que el `category` de `/prices`). |
| `example_models` | string[] | Modelos de ejemplo del catálogo para ese código. |
| `confidence` | number | Certeza del clasificador (0–1). |
| `pending_review` | bool | `true` si la clasificación es dudosa (revisión recomendada). |
| `cached` | bool | `true` si vino de la cache (sin coste de LLM). |

Notas:
- El `model` va **URL-encoded** (espacios, acentos…).
- `400` si `model` falta o está vacío; `401` si falta/ es inválida la API key.
- **Cacheado:** la primera consulta de un modelo llama al LLM; las siguientes salen
  de cache hasta que cambie el catálogo o el clasificador. Es de **solo lectura**.

---

## 12. Endpoint de catálogo: grupos de proveedor y cobertura

`GET /api/v1/provider-groups` — misma autenticación por API key que el resto.

Mientras `/prices` responde "cuánto cuestan", este endpoint responde "**qué
grupos existen y cuántos días de precios tienen por delante**". Es la base para
emparejar grupos propios contra grupos concretos de un competidor y para
validar periódicamente esos emparejamientos.

### Parámetros (query string, todos opcionales)

| Parámetro | Descripción |
|-----------|-------------|
| `provider` | Restringir a un proveedor (`?provider=centauro`). |
| `location_id` | Restringir a un mercado canónico. `404` si no existe. |
| `duration` | Duración de referencia (días) para el calendario de cobertura. Default `7`. Debe ser uno de los brackets (`1,2,3,4,5,6,7,14,21,28`); si no, `422`. |

### Respuesta

Nivel raíz: `provider`, `location_id`, `duration` (eco de los parámetros),
`generated_at`, `total` y `groups[]`. Cada elemento de `groups`:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `provider_code` / `provider_name` | string | Proveedor al que pertenece el grupo. |
| `group_key` | string | **Identificador estable** del grupo dentro de su proveedor: el `external_code` del proveedor, o `attributes_hash` cuando no expone códigos. Es el valor a persistir si tu sistema referencia un grupo. |
| `external_code` / `attributes_hash` | string \| null | Las dos identidades por separado (exactamente una respalda a `group_key`). |
| `models` | string[] | Modelos que el proveedor lista para el grupo. |
| `transmission` | string \| null | Transmisión observada. |
| `acriss_code` | string \| null | Clasificación ACRISS. **Puede ser `null`** (grupo pendiente de clasificar): sigue siendo un target válido para emparejamiento directo. |
| `pending_review` | bool | Clasificación dudosa. |
| `location_ids` | int[] | Mercados canónicos donde se ha visto el grupo. Hay **una entrada por grupo lógico**, no por oficina. |
| `coverage` | objeto | Cobertura de precios hacia delante (ver abajo). |

### `coverage` — semántica

Calculada contra **hoy** en cada petición: no la cachees más de un día.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `from` | string | Hoy (inicio de la ventana). |
| `through` | string \| null | Último día con precio real a la duración de referencia. `null` si ninguna temporada tiene precio. |
| `covered_days` | int | Días con precio real desde hoy (inclusive), a la duración de referencia. |
| `horizon_days` | int | Días desde hoy hasta `through` (inclusive). **`covered_days < horizon_days` significa huecos**: hay temporadas detectadas sin precio que las respalde. |
| `by_duration` | objeto | `covered_days` recalculado por cada bracket. La cobertura **varía por duración** — un grupo puede tener 236 días a 7d y 208 a 28d, u otro 6 días a 1d y **cero** a 7d+. |
| `ranges` | objeto[] | Las temporadas desde hoy: `{from, through, priced}`. Con esto se pinta un calendario de disponibilidad; `priced` refleja la duración de referencia. |
| `last_observed_at` | string \| null | Cuándo se observó por última vez un precio de este grupo (frescura — independiente de la cobertura). |

Notas:
- Un grupo sin ninguna temporada vigente o futura devuelve la forma vacía
  (`covered_days: 0`, `ranges: []`), no desaparece del catálogo.
- Emparejar contra un grupo con `covered_days` bajo es posible pero arriesgado:
  decide con el calendario delante.
