# Product Scope

> Source of truth para qué entrega el producto en cada versión.
> Define el techo del producto: si una capacidad no está aquí, no
> es parte del producto.
>
> Documentos relacionados:
> - `ROADMAP_ARCHITECTURE.md` — arquitectura del SaaS y fases de
>   implementación. Producto y arquitectura son separables: la misma
>   arquitectura puede soportar varios alcances.
> - `DATA_MODEL.md` — schema de datos. Deliberadamente más amplio que
>   v0; soporta capacidades fuera del alcance actual.
> - `MILESTONES.md` — log de implementación. Cuándo se construyó qué.
>
> Toda decisión de alcance pasa por actualizar este documento antes
> de tocar código.

---

## v0 — Primeros tenants

### Las tres consultas

El producto v0 expone tres consultas sobre los datos de mercado que
el tenant monitoriza a través de sus suscripciones:

1. **Tarifario por proveedor.** El tarifario de un proveedor concreto
   al que el tenant está suscrito, para un rango de fechas elegido.

2. **Tarifario medio.** El tarifario agregado (media por celda) entre
   todos los proveedores a los que el tenant está suscrito, para un
   rango de fechas elegido.

3. **Tarifario mínimo.** Igual que el medio, agregando con `min` en
   lugar de `mean`.

El input común de las tres es `(date_range, vehicle_categories,
durations)`. La consulta por proveedor añade `provider`. La consulta
opera siempre en el contexto de un tenant — las suscripciones del
tenant determinan qué datos son visibles.

Por defecto, `vehicle_categories` se refiere a las **categorías
canónicas** del producto (ver "Taxonomía canónica" más abajo). Si el
tenant ha declarado `tenant_vehicle_groups` propios, los inputs
pueden expresarse en esos términos y el sistema los traduce.

### Forma del output

Las tres consultas devuelven Format A: una tabla con una fila por
(categoría × tramo de fechas) y una columna por duración del bracket
`{1, 2, 3, 4, 5, 6, 7, 14, 21, 28}` días.

Ejemplo (tarifario por proveedor, sin tenant_vehicle_groups
declarados):

```
Category               | Period            | 1d | 2d | ... | 7d | 14d | 21d | 28d
ECONOMY_PASSENGER      | 01–14 Jul         | 45 | 42 | ... | 38 |  35 |  33 |  32
ECONOMY_PASSENGER      | 15 Jul – 31 Aug   | 65 | 60 | ... | 52 |  48 |  46 |  44
COMPACT_PASSENGER      | 01–14 Jul         | 55 | 51 | ... | 47 |  43 |  40 |  39
COMPACT_PASSENGER      | 15 Jul – 31 Aug   | 78 | 72 | ... | 62 |  58 |  55 |  53
```

Cuando el tenant tiene `tenant_vehicle_groups` declarados, las filas
se etiquetan con los nombres del tenant (ej. "Compactos", "Familiar")
en lugar de los códigos canónicos. La estructura y los datos son los
mismos.

Las filas no se etiquetan con identidad de proveedor ni con nombres
internos de zona. Solo aparecen como rangos de fechas.

La rejilla de filas se construye así:

- **Tarifario por proveedor.** Una fila por cada zona detectada del
  proveedor que toca el rango pedido.

- **Tarifarios medio y mínimo.** Una fila por cada tramo de fechas
  resultante de la unión de cortes de zona de los proveedores
  suscritos dentro del rango. Cada vez que cualquier proveedor cambia
  de zona, se abre una fila nueva.

Definición canónica de Format A: ver `DATA_MODEL.md` Decisión 10.
Anatomía de la query SQL que lo materializa: `DATA_MODEL.md` Parte 3.

### Taxonomía canónica como espina dorsal

El producto v0 se organiza alrededor de una taxonomía canónica de
vehículos (`canonical_vehicle_types`), curada por el operador y
mantenida en `taxonomy.yaml` versionado en git. Categorías típicas:
`ECONOMY_PASSENGER`, `COMPACT_PASSENGER`, `MID_SUV`, `LUXURY_AUTO`,
`COMMERCIAL`, `MOTORCYCLE`, etc. La taxonomía es deliberadamente
coarse (10-15 categorías) y estable.

**Tres consecuencias importantes:**

1. **Onboarding mínimo.** Un tenant nuevo puede consumir el producto
   inmediatamente sin configurar nada. La taxonomía canónica le ofrece
   un lenguaje listo para usar.

2. **Capa propia opcional.** El tenant que prefiera su propio lenguaje
   declara `tenant_vehicle_groups` y los mapea sobre categorías
   canónicas vía `tenant_vehicle_group_mappings`. La capa propia es
   opt-in; el producto funciona sin ella.

3. **Comparabilidad entre tenants.** Como todos los tenants ven el
   mercado en términos de la misma taxonomía base, los outputs son
   estructuralmente comparables. Esto habilita futuras capacidades
   de benchmark sin re-modelado.

### Clasificación automática provider → categoría canónica

Cada vehículo extraído durante el scrape se clasifica en una categoría
canónica antes de persistirse en `provider_vehicle_categories`. La
clasificación la realiza un LLM a través de una interfaz abstracta
(`ClassificationService`):

- **Clasificación batch por provider.** El LLM recibe el catálogo
  completo del proveedor (todos sus grupos a la vez) junto con un
  precio representativo de 7 días para cada grupo. Esto permite al
  LLM razonar sobre la jerarquía interna de pricing del proveedor:
  si dos grupos tienen precios distintos, probablemente pertenecen
  a tiers distintos y debe distribuirlos en categorías canónicas
  adyacentes en vez de colapsarlos.
- **Modelo primario.** Gemini Flash (tier gratuito en v0).
- **Fallback condicional.** Gemini Pro cuando Flash devuelve
  cualquier confianza inferior a 0.85 en la respuesta del batch.
- **Cuando ambos quedan por debajo del umbral para un vehículo
  concreto**, ese vehículo se persiste con `canonical_type_id = NULL`
  y `pending_review = true`. Queda fuera del producto hasta que el
  operador clasifique manualmente o amplíe la taxonomía.
- **El LLM nunca crea categorías nuevas autónomamente.** Si ninguna
  categoría existente encaja, queda pending_review.

**Cuándo se reclasifica un proveedor:**

- Cuando aparece un grupo nuevo en su catálogo (la nueva pieza puede
  desplazar la interpretación de la jerarquía interna).
- Cuando se incrementa `taxonomy_version`.
- Cuando el operador lo fuerza vía comando explícito.

Detalles de implementación (caché por provider, precio representativo
como media de 7-day prices del probe, comportamiento ante fallo del
LLM, identidad de PVC por external_code o hash de atributos) viven en
`DATA_MODEL.md` Decisiones 1 y 2.

### Heterogeneidad intra-provider: respetada, no agregada

El producto preserva fielmente la estructura de grupos que cada
proveedor expone. **Un proveedor crea tantos grupos como tiers de
precio quiere distinguir.** Si Solcar separa "Grupo EA" (Peugeot
2008 a 57€/día) de "Grupo GA" (Kia XCeed Hybrid a 69€/día), está
diciéndonos que para él son tiers distintos — aunque ambos sean
crossovers automáticos que encajan semánticamente en
`INTERMEDIATE_AUTO`.

Cada grupo del proveedor → su propia fila en
`provider_vehicle_categories`. La columna `canonical_type_id` es
metadato de clasificación, no identidad. Pueden coexistir múltiples
filas del mismo proveedor con la misma `canonical_type_id`. No hay
agregación intra-proveedor en BD.

Esto difiere de una decisión inicial del diseño que asumía
agregación dentro del proveedor. La decisión se revirtió cuando
una clasificación real produjo violaciones de unicidad sobre
`(provider, canonical_type)` — síntoma de que los proveedores
reales segmentan más fino que nuestra taxonomía canónica. La
historia completa de la decisión está en `DATA_MODEL.md` Decisión 1.

### Política N:M de agregación (en query, no en persistencia)

Cuando varios `provider_vehicle_categories` rows contribuyen al
precio de una celda dada en la respuesta a una consulta, la política
de agregación por defecto es **mínimo**.

Justificación: la utilidad del producto es competir con el mercado.
El precio relevante para una categoría canónica es el más bajo que
está accesible para ese tipo de coche, no el promedio entre variantes.

Esto aplica en tres niveles, **todos resueltos en query time**, no
en persistencia:

- **Múltiples PVCs del mismo proveedor con la misma categoría
  canónica.** Si Solcar tiene dos grupos clasificados como
  `INTERMEDIATE_AUTO` con precios 57€ y 69€, la celda del tarifario
  por proveedor muestra 57€ (el mínimo). Cada PVC mantiene su
  histórico de precios completo en BD; la agregación se aplica
  solo al servir.
- **Entre proveedores en tarifario mínimo.** Se hace `min` entre
  proveedores suscritos.
- **Entre proveedores en tarifario medio.** Se hace `mean` entre
  proveedores; el `min` intra-proveedor del primer nivel sigue
  aplicando antes del cruce.

Configurabilidad futura: cuando un tenant requiera otra política
para sus `tenant_vehicle_group_mappings` (media, máximo, primero
declarado), se añade un campo `aggregation_policy` al mapeo. No es
necesario en v0.

### Cobertura

En los tarifarios medio y mínimo, cada celda lleva un campo `coverage`
que indica cuántos proveedores suscritos aportan dato a esa celda.

Casos típicos donde coverage es menor que el total de suscripciones:

- Un proveedor no cubre el rango temporal de la celda (su `period_days`
  se queda corto).
- Un proveedor no tiene observación para ese (categoría × duración) en
  esa zona — fallo de scrape, o el proveedor no ofrece esa duración.
- Un proveedor tiene la suscripción en estado distinto de `active`.
- Un proveedor no ofrece nada en esa categoría canónica (no tiene
  ningún grupo clasificado allí).

Sin este campo, el cliente consume agregados de muestras desiguales
sin saberlo. Es un error de producto, no una opcionalidad de UI.

El cálculo de media y mínimo se hace **solo con los proveedores que
aportan dato** (no se rellenan ceros). Una celda con `coverage = 0`
se muestra explícitamente marcada como sin dato (null o `—`); la fila
no se omite del output, porque la ausencia de dato es información
relevante para el cliente.

### Fuera de v0

Lo siguiente no es parte de v0. Cualquier petición o feature request
que caiga aquí se evalúa para v1+ siguiendo la política de cambios
de alcance (al final del documento).

- **Tarifario propio del cliente.** El producto no recibe ni almacena
  los precios actuales del cliente. Por tanto, no hay comparativa
  cliente-vs-mercado en v0.

- **Motor de pricing recomendado.** Las reglas declarativas y la
  generación de tarifarios recomendados son Fase 4 del roadmap. El
  alcance v0 es estrictamente consulta del mercado.

- **Alertas / detección de cambios.** El producto v0 es pull (el
  cliente entra y consulta). No hay emails, notificaciones ni
  resúmenes periódicos. Importante: esto tiene implicación de
  retención. Ver "Decisiones diferidas" más abajo.

- **Comparativa estructural.** Timeline de cómo cada proveedor
  segmenta las temporadas. Útil para entender el mercado pero no
  parte de v0 porque las zonas detectadas no son superficie del
  producto.

- **Detección de huecos / oportunidades.** Análisis de dispersión y
  consenso entre proveedores.

- **Forecasting / predicción de precios.** Requiere ML y volumen de
  datos que no existe.

- **Optimización por elasticidad de demanda.** Requiere datos de
  bookings reales del cliente.

- **Aplicación automática de precios al sistema del cliente.** El
  producto nunca aplicará precios automáticamente. Es decisión de
  producto, no técnica.

- **Edición de la taxonomía canónica por el tenant.** La taxonomía
  es del operador. Los tenants que necesiten lenguaje propio usan
  `tenant_vehicle_groups`.

### Implicación de modelo

El alcance v0 introduce cambios estructurales en el modelo de datos
respecto al cierre del Hito 4: la taxonomía canónica como capa
intermedia, la clasificación automática con LLM, y el renombrado de
las tablas para reflejar la nueva semántica. El detalle está en
`DATA_MODEL.md` Decisión 1. La lógica de consulta vive en
`PriceQueryService` (Hito 5D, adaptado al nuevo modelo).

---

## Decisiones diferidas

Cosas decididas explícitamente *fuera* de v0, con el contexto que
costó construir y el trigger que las reabre. Espejo de la sección
"Deliberadamente diferido" de `DATA_MODEL.md`.

### Alertas como capa transversal

**Por qué se difiere.** v0 es pull para acotar alcance y llegar antes
a producción. Las alertas no son funcionalidad nueva sino la forma
*activa* de las tres consultas existentes (qué cambió en el tarifario
desde la última vez).

**Riesgo a vigilar.** El producto sin alertas depende de que el
cliente recuerde entrar. La frecuencia de uso real en los primeros
tenants es la señal a medir. Si es baja, las alertas suben de
prioridad para v1 no por feature sino por retención.

**Trigger.** Un tenant que entra menos de N veces por semana en el
primer mes (umbral a definir cuando haya datos), o petición explícita
de más de un tenant.

**Implicación cuando llegue.** Trabajo principal en aplicación, no en
modelo. Requiere job programado, plantillas de email, estado mínimo
de "qué se ha visto / qué no".

### Tarifario propio del cliente como input

**Por qué se difiere.** El alcance del producto v0 es consulta del
mercado, no comparación. El motor de pricing de Fase 4 produce
tarifarios directamente a partir de reglas del cliente y datos de
mercado, sin requerir que el cliente suba su tarifario actual.

**Trigger.** Petición concreta de un cliente que quiera ver su
posición actual respecto al mercado **antes** de adoptar pricing
automático (utilidad de benchmark independiente).

**Implicación cuando llegue.** Tabla `tenant_own_prices` con
versionado al estilo de `pricing_rules`. Habilita la utilidad de
benchmark cliente-vs-mercado como capacidad separada del motor
de pricing.

### Pricing automático: dos casos de uso, dos rejillas

**Por qué se difiere.** Pricing es Fase 4, fuera de v0.

**Marco anticipado para evitar ambigüedad cuando llegue:**

Hay dos casos de uso del cliente sobre los datos del sistema:

1. **CONSULTA DE MERCADO (v0).** El cliente pregunta entre fechas
   sobre un rango de su elección. El output usa la rejilla del
   **mercado** (zonas detectadas, multi-proveedor agregado). El
   cliente no declara nada.

2. **TARIFARIO OPERACIONAL (Fase 4).** El cliente declara reglas
   con rangos. El sistema produce un tarifario para 6-9 meses
   organizado en la rejilla del **cliente** (los rangos de sus
   reglas). La declaración de reglas es prerrequisito.

**Terminología.** Se reserva "zona" para unidad observada del
mercado (`homogeneous_zones`) y "temporada" para unidad declarada
del cliente. Las dos son rangos de fechas estructuralmente
similares, pero su procedencia es diferente y conviene mantenerlo
en el lenguaje.

**Estructura del cálculo en caso 2:**
- El cálculo opera sobre la rejilla intersectada (rangos de las
  reglas × tramos del mercado). Ahí la regla aplica con precios
  reales y sin ambigüedad.
- El output se ofrece en dos vistas:
  - **Detallada** (rejilla intersectada): para auditoría y
    diagnóstico.
  - **Operacional** (rejilla del cliente, re-agregada): exportable
    al sistema del cliente. Default de re-agregación: mínimo del
    subtramo, coherente con la filosofía competitiva del producto.

**Sobre temporadas como entidad de modelo:**
- No hay tabla `tenant_seasons` separada en Fase 4. Los rangos de
  las reglas SON las temporadas operacionales del cliente.
- Si en el futuro varias reglas comparten rango y eso se repite,
  se puede extraer la abstracción. Por ahora, embebido en la regla.

**Sobre el rol del sistema:**
- El sistema **propone** temporadas (a partir de las zonas del
  mercado, vía utilidad de sugerencia en el dashboard de Fase 4).
- El cliente **adopta** las propuestas, las edita, o declara las
  suyas desde cero. Una vez adoptadas, son del cliente.
- El sistema **no define** temporadas autónomamente. No las
  modifica una vez adoptadas. Si el mercado cambia y las zonas
  se redetectan, las temporadas adoptadas no se mueven; el sistema
  avisa al cliente de que hay nueva información disponible y el
  cliente decide si refrescarlas.

**Trigger.** Inicio de Fase 4 (motor de pricing).

### Periodo de scraping operacional largo

**Por qué se difiere.** v0 usa periodos cortos (default 90 días)
porque la utilidad es consulta puntual del mercado. Pricing
automático requiere horizonte mayor para que el cliente pueda
"fijar y olvidarse".

**Marco anticipado:**
- El periodo operacional para clientes con pricing automático
  será 180-270 días (6-9 meses), no 365. La razón: la fiabilidad
  del precio del mercado decae con la distancia (los proveedores
  no setean precios serios para fechas a 11 meses). Un horizonte
  de 6-9 meses captura la temporada operativa relevante y filtra
  ruido lejano.
- Ampliar a este rango con la pipeline actual costaría más tiempo
  por proveedor en daily scrape (ver `SCRAPING_OPTIMIZATIONS.md`),
  manejable para 1-2 clientes pero doloroso a partir de un
  catálogo más amplio.
- Por tanto, el primer cliente real con pricing automático
  dispara la activación de las optimizaciones diferidas: adaptive
  probe + frequency-decreasing layered strategy.

**Trigger.** Primer cliente que onboardea con `period_days >= 180`
y daily cadence sostenida, o el scrape de todos los providers
supera los 45 minutos en periodo de 180 días.

### Integración con sistema externo del cliente

**Por qué se difiere.** v0 entrega consultas de mercado. La
generación de tarifarios recomendados es Fase 4. La sincronización
con sistemas externos del cliente es Fase 5+, después del motor
de pricing.

**Trigger.** Primer cliente operando con tarifario recomendado en
producción que pide reducir el copy/paste manual al sistema.

**Marco anticipado:**
- El paquete exportable es la vista operacional del caso 2
  (tarifario en la rejilla de las temporadas declaradas por el
  cliente). Las temporadas y los precios viajan juntos como
  unidad consumible.
- Modos de integración considerados:
  - **Pull** (cliente lee nuestra API): primera elección por
    defecto.
  - **Feed** (fichero CSV/JSON en S3 firmado): alternativa para
    sistemas legacy con import por fichero pero sin REST moderno.
  - **Push** (nosotros escribimos al sistema del cliente): solo
    bajo demanda concreta, requiere superficie operacional mayor.
- Implementación bespoke per-cliente inicialmente. Cuando los
  patrones se repitan, extraer adaptadores configurables.
- La integración es producto vendible, no feature: setup fee +
  suscripción más alta. Justifica el coste de bespoke.
- `pricing_outputs.inputs_snapshot_jsonb` (ya en el modelo) es
  prerrequisito de auditoría: para cualquier precio empujado,
  debe ser posible reconstruir las reglas, los datos de mercado
  y los parámetros que lo generaron.

**Implicación cuando llegue.** Ningún cambio al modelo de pricing.
Trabajo nuevo en una capa de "delivery" en `src/saas/` con
adaptadores per-cliente.

### Política N:M configurable por mapeo

**Por qué se difiere.** Default `min` es suficiente para v0. Añadir
configurabilidad sin tenants reales pidiéndolo es arquitectura
especulativa.

**Trigger.** Primer tenant que pida agregación distinta de mínimo
con justificación clara.

**Implicación cuando llegue.** Columna `aggregation_policy` en
`tenant_vehicle_group_mappings` con default `'min'`. Migración trivial.

### Pool de scrapers / paralelismo de sesiones

**Por qué se difiere.** Con 2 proveedores activos el tiempo total
es manejable (~25 min). El diseño actual permite paralelismo (cada
`SmartScraperOrchestrator` es independiente), pero no se orquesta
todavía.

**Trigger.** Cuando el tiempo de scraping completo supere el
presupuesto de ventana nocturna (estimado: >4 h con 3+ proveedores
de rendimiento mixto, ver MILESTONES sobre la disparidad ~5s vs
~30s/búsqueda).

### Integración directa con sistemas de reservas (GDS / channel managers)

**Por qué se difiere.** El scraper web es el único mecanismo de
adquisición de precios en v0. No hay integración con APIs de
proveedores, GDS (Amadeus, Sabre), ni channel managers.

**Trigger.** Cuando un proveedor ofrezca API propia con datos
equivalentes y el coste de scraping web de ese proveedor justifique
la migración. O cuando un cliente del tipo channel-manager pida
publicar sus tarifas vía nuestro sistema.

---

## Política de cambios al alcance

Cualquier cambio al alcance v0 (mover algo de "fuera" a "dentro", o
viceversa) requiere argumento explícito. **No se mueve por:**

- Petición aislada de un cliente sin justificación de impacto.
- Idea técnica interesante.
- "Es solo una columna más" / "es solo un endpoint más".

**Se mueve por:**

- Evidencia de que la falta bloquea adopción o retención de los
  primeros tenants (más de un tenant pidiendo lo mismo, o un tenant
  que no contrata por la falta).
- Decisión arquitectónica que requiere anticipar el alcance (raro;
  argumentar caso por caso).

Cuando se mueva algo, se actualiza este documento en la misma sesión
en que se decide, no después.

Las versiones v1+ se planifican cuando v0 esté en producción y haya
datos de los primeros tenants. No se anticipan aquí.

---

## v1 — Reservado

> A llenar cuando v0 esté en producción y haya señal de los primeros
> tenants. No anticipar contenido aquí; las decisiones de v1 dependen
> de lo aprendido en v0.
