# RentRadar — API de Precios (v1) · Guía de integración

Documento único y autocontenido para implementar el **consumidor** de la API:
el sistema externo que va a **copiar los precios** calculados por RentRadar e
inyectarlos en su propio motor (booking, ERP, web, etc.).

> **TL;DR:** haces un `GET` autenticado con una API key, recibes un JSON con los
> precios finales de venta por **categoría de vehículo (ACRISS) × temporada ×
> duración**, y los aplicas en tu sistema. Es de **solo lectura** y *pull* (lo
> consultas cuando quieras; no hay push/webhook).

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
