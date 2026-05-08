# De PoC a SaaS multi-tenant: roadmap y arquitectura

> Documento de diseño para evolucionar `smart-rental-scraper` de prueba de concepto a producto SaaS multi-tenant.

## Marco mental

El cliente no quiere "ver precios de la competencia". Quiere **fijar sus propios precios automáticamente**. Eso significa que el scraper es solo el *input* del producto. El producto real es un **motor de pricing dinámico** con scraping como fuente de datos. Esa distinción condiciona todas las decisiones que siguen.

---

## 1. Qué debería ser el MVP (y qué no)

El error más común aquí es construir un MVP que sea "el scraper, pero con login y multi-tenant". Eso deja al cliente con un dashboard de competidores que mira y luego usa para cambiar precios a mano en su sistema — exactamente lo que ya hace hoy. No has resuelto su problema.

El MVP mínimo que entrega valor real tiene cinco capacidades, en este orden de prioridad:

### (1) Ingesta y normalización multi-proveedor

Lo que ya existe en el PoC, pero con un modelo de datos canónico estable: un precio scrapeado de Provider A y uno de Provider B se guardan con la misma forma. Es la base de todo lo demás y donde la mayoría de equipos meten deuda técnica que luego paga el resto del producto.

### (2) Histórico y series temporales

Sin histórico no hay producto. El cliente necesita saber "el competidor X subió un 8% el grupo B la semana pasada", no solo "hoy cuesta 47€". Cada scrape es un snapshot temporal y eso es lo que da valor analítico.

### (3) Reglas de pricing declarativas

El cliente define reglas tipo *"mi precio del grupo C en temporada alta = mínimo del mercado + 3%, con suelo de 35€/día y techo de 90€/día"*. Es el corazón del producto. Empezar con un DSL muy simple (JSON con operadores básicos), no con un editor visual.

### (4) Output accionable

Generar el pricing recomendado en un formato que el cliente pueda usar: CSV listo para importar en su sistema, o mejor, una API/webhook para que su sistema lo consuma. Sin esto el producto sigue siendo un dashboard.

### (5) Observabilidad de la ingesta

Cuando un scraper se rompe (y se romperá), el cliente y el equipo tienen que enterarse antes de que se tomen decisiones de pricing con datos viejos. Alertas de staleness, gaps detectados, % de éxito por proveedor.

### Lo que NO debe tener el MVP

Por mucho que lo pida el cliente:

- Aplicación automática de precios en su sistema. Que sea él quien apriete el botón las primeras semanas — riesgo de bug × dinero real es inaceptable.
- Forecasting / ML.
- Optimización por elasticidad de demanda.
- A/B testing.
- Editor visual de reglas.

Todo eso es v2+.

Multi-tenant **a nivel de datos** desde el día uno (cada fila lleva `tenant_id`), pero con un solo plan, sin billing automatizado y onboarding manual. No construir Stripe + self-service + roles granulares en el MVP.

---

## 2. Relacional vs NoSQL: la pregunta correcta es otra

La pregunta no es "relacional o NoSQL", es "qué cargas de trabajo tengo". Hay tres muy distintas:

### Carga A — Configuración y estado del tenant

Usuarios, proveedores configurados, reglas de pricing, vehículos del cliente, mapeos. Volumen bajo, lecturas/escrituras transaccionales, queries con joins, integridad referencial crítica. Esto es **PostgreSQL puro**, sin discusión.

### Carga B — Series temporales de precios

Cada scrape genera N filas (proveedor × ubicación × grupo × fecha pickup × duración × timestamp_scrape). En el PoC con 90 días × 9 duraciones × ~5 grupos × 2 proveedores son ~8.000 puntos por scrape. Con 50 tenants haciendo daily scrape, son 400k filas/día, ~150M/año. Queries típicas: "evolución del precio del grupo B en Provider A para pickup el 15 julio durante el último mes". Esto es **time-series**.

### Carga C — Resultados intermedios y artefactos

El JSON crudo del scrape, capturas de pantalla de debugging, logs de sesiones. **Object storage** (S3/R2) + referencia en Postgres.

### Recomendación

**PostgreSQL para todo en el MVP**, con la tabla de precios bien diseñada (particionada por mes, índices apropiados). Postgres aguanta perfectamente cientos de millones de filas si está bien modelada, y tener una sola base de datos en el MVP ahorra una cantidad enorme de complejidad operativa.

Cuando empiece a notar dolor (probablemente por encima de 500M-1B filas o queries analíticas pesadas concurrentes), migrar la tabla de precios a **TimescaleDB** (es una extensión de Postgres, migración casi gratis) o **ClickHouse** si se hace analítica seria.

NoSQL documental (Mongo) no aporta nada aquí: el modelo es muy estructurado y las queries son relacionales (joins entre tenants, proveedores, vehículos, precios). Meter Mongo complica el stack sin beneficio.

---

## 3. Modelo de datos canónico

> El detalle completo del modelo de datos vive en `docs/DATA_MODEL.md` (decisiones, esquema en pseudo-DDL, anatomía de la query principal, diferidos).
>
> Esta sección recoge solo los principios estructurales que afectan a la arquitectura general. No duplica ese documento.

**Principios estructurales:**

- **Multi-tenant físico:** shared database, `tenant_id` en cada tabla multi-tenant, Row-Level Security activo desde el día uno.
- **Catálogo curado por el operador, no por el tenant.** Los proveedores, ubicaciones y tarifas viven en el catálogo global; los tenants se *suscriben* a tuplas `(provider, location, rate)`. Esto deduplica scrapes entre tenants suscritos a la misma tupla.
- **Observaciones de precio compartidas globalmente.** No llevan `tenant_id`. El aislamiento se enforza por join con la tabla de suscripciones.
- **Append-only on change con heartbeats** para histórico de precios. No se persisten datos sintéticos (los expande la capa de aplicación al vuelo).
- **Auditabilidad económica completa** vía `pricing_outputs.inputs_snapshot_jsonb` y versionado explícito de `pricing_rules`.
- **Autenticación delegada a proveedor externo** (a decidir cuando llegue el primer login real). El modelo solo guarda la identidad local vinculada.

Cualquier decisión nueva sobre el modelo de datos pasa por actualizar `DATA_MODEL.md` antes de tocar código.

---

## 4. Arquitectura del sistema

El código actual ya está bien estructurado (clean architecture, DI, interfaces) y reorganizado en estructura monorepo bajo `src/`. Eso da una ventaja enorme: el dominio y la aplicación apenas cambian. Lo que cambia es lo que hay alrededor.

### Estructura monorepo actual

```
src/
  shared/          # modelos de dominio compartidos (BookingProvider, BookingSearch, BookingResult, etc.)
  scraper/         # motor de scraping completo (domain / application / infrastructure / presentation)
  saas/            # futuro backend SaaS (placeholder)
```

`src/shared/` es el único lugar donde viven los tipos de dominio que el scraper y el SaaS comparten. Esto permite que en el futuro `src/saas/` consuma los mismos tipos sin introducir dependencias circulares ni duplicaciones. Cada módulo se puede empaquetar y desplegar de forma independiente.

### Principio estructural: dos planos separados

La arquitectura del sistema se divide en dos planos que se comunican vía API:

**Plano de aplicación (SaaS):** API pública, dashboard, base de datos, scheduler de jobs, motor de pricing. Es el "cerebro" del producto.

**Plano de scraping (worker):** un proceso (o varios) que ejecuta los scrapes con Playwright. Vive en infraestructura distinta del SaaS y se comunica con él vía HTTP **iniciando siempre las conexiones desde el worker hacia el SaaS** (modelo pull). El worker pide jobs, los ejecuta, devuelve resultados.

Esta separación es **una decisión arquitectónica firme**, independiente de dónde se despliegue cada plano. La ubicación física concreta (qué proveedor cloud para el SaaS, dónde corre el worker) se decide cuando toque desplegar el primer entorno real, no antes.

**Por qué pull, no push:**

- El worker no necesita exponer puertos a internet. Cero superficie de ataque en su red.
- Si el worker pierde conectividad y vuelve, se reconecta solo sin que el SaaS tenga que descubrir su nueva IP.
- Escala horizontalmente sin tocar el SaaS: añadir un segundo worker (en otra IP, otra red, otro proveedor) es solo configuración del nuevo worker.
- No asume IP estable del worker, lo cual encaja con escenarios de IPs residenciales dinámicas.

### Componentes del SaaS

**API pública** (FastAPI o equivalente). Endpoints REST para CRUD de configuración, consulta de precios observados, gestión de reglas, descarga de outputs. Autenticación JWT (delegada a proveedor externo) + tenant isolation por middleware (cada request lleva `tenant_id` derivado del token, y todas las queries lo filtran — esto es no-negociable y se enforza a nivel de repository, no confiando en que cada endpoint se acuerde).

**Endpoints internos para workers.** Un set de endpoints separado (autenticado por API key del worker, no por JWT de usuario) para que los workers pulleen jobs y suban resultados. No expuestos al dashboard ni al cliente.

**Scheduler.** Lo más simple posible: cron + tabla de jobs en Postgres, o APScheduler. Decide cuándo lanzar scrapes por tupla `(provider, location, rate)`. No usar Airflow ni nada pesado en el MVP.

**Cola de jobs.** Una tabla en Postgres con estado (`pending`, `assigned`, `done`, `failed`) y timestamps. Suficiente para el MVP. Cuando crezca, se sustituye por Redis/RQ/SQS sin tocar la lógica de scheduling.

**Pricing engine.** Un servicio (puede ser otro tipo de worker, o invocado desde el API) que toma reglas + `price_observations` y produce `pricing_outputs`. Empezar síncrono, invocado tras cada scrape exitoso. Más adelante puede ser su propia cola.

**Storage.** Postgres + Redis (cache, quizás cola en el futuro) + S3/R2 (artefactos). Tres piezas, no más.

**Observability.** Sentry para errores, structured logs (JSON) a stdout que recoja la plataforma, un dashboard básico de métricas de scraping. Un endpoint `/admin/health` que muestre stats de las últimas 24h llega para empezar.

### Componentes del worker

**Loop principal:** pedir job al SaaS → si hay job, ejecutar `SmartScraperOrchestrator` (el código actual del PoC) → subir resultados → repetir. Si no hay job, esperar y reintentar.

**Resiliencia:** reintentos exponenciales en cualquier llamada al SaaS. Si la API no responde durante minutos, el worker espera y vuelve a intentar; no abandona. El SaaS, por su parte, marca como "lost" cualquier job sin heartbeat tras un timeout configurable y permite reasignarlo.

**Sin estado persistente:** si el worker se reinicia, lo único que se pierde es el job en curso (que el SaaS reasigna). El estado real vive en el SaaS.

**Autenticación:** una API key con scope limitado (solo pull jobs y subir resultados) en el `.env` del worker.

### Multi-tenancy a nivel de scraping

Cuidado con que un tenant problemático (proveedor lento, captchas) bloquee a los demás. Cuotas por tenant en la cola y timeouts agresivos por job son obligatorios desde el día uno.

---

## 5. Fases del trabajo

El roadmap se aborda en cuatro fases conceptuales. El log de lo construido y cuándo vive en `docs/MILESTONES.md`.

**Fase 1 — Persistencia.** Modelo de datos canónico, migraciones, multi-tenancy físico (RLS, roles), capa de repositorios y conexión del scraper a la base de datos. Estado: completada.

**Fase 2 — Exposición.** API HTTP de lectura, autenticación básica, primer dashboard mínimo. Permite al cliente consultar el sistema sin tocar la BD directamente. Estado: pendiente.

**Fase 3 — Scheduling y workers.** Mover el scraping de CLI a jobs encolados; comunicación pull worker → SaaS; primer worker en infraestructura real. Estado: pendiente.

**Fase 4 — Motor de pricing.** Reglas declarativas v0 (hardcoded primero, DSL solo cuando haya 2-3 clientes pidiéndolo). Estado: pendiente.

### Punto de revisión

Cuando haya 3-5 clientes reales en producción, revisar este roadmap. Habrá aprendizajes que ahora no se pueden anticipar:

- ¿Las reglas declarativas son suficientes o necesitan ML?
- ¿Los clientes quieren push automático a su sistema?
- ¿La frecuencia de scrape diaria es suficiente o necesitan intra-day?
- ¿La infraestructura de scraping (IPs, redundancia) está aguantando?

Esa será la versión que decida la v2.

---

## Hitos pendientes (decidir cuando toque, no antes)

- **Elección de proveedor de identidad** para autenticación. Trigger: antes del primer endpoint que requiera login real. Depende de presupuesto, perfil de cliente típico (¿SSO empresarial pronto?), preferencia managed vs self-hosted.
- **Elección de hosting concreto del SaaS** (VPS vs managed: DigitalOcean, Hetzner, Fly.io, Railway, Render…). Trigger: cuando se deploy el primer entorno persistente.
- **Ubicación física del worker de scraping.** Trigger: cuando se vaya a poner en producción la primera ingesta automatizada. Las opciones (residencial propio, residencial alojado, proxies residenciales contratados, datacenter con proxies) se evalúan con la información del momento.
- **Plan B de IPs / proxies residenciales contratados.** Trigger: detectar bloqueos o captchas frecuentes en algún proveedor que afecte a la entrega de datos, o crecimiento de catálogo más allá del volumen que una sola IP residencial soporta cómodamente.

---

## Áreas de profundización pendientes (cuando haya señal real)

Las dos zonas donde típicamente se la juega un producto así:

1. **Diseño del DSL de reglas de pricing.** Es donde la abstracción correcta marca la diferencia entre un producto flexible y uno que se reescribe en seis meses. Diseñarlo antes de tener clientes reales escribiendo reglas es arquitectura especulativa. Empezar con reglas hardcodeadas para 1-2 clientes y extraer el DSL de los patrones reales.
2. **Modelo de mapeo de grupos de vehículos entre competidores.** El problema más subestimado del dominio. Ya resuelto a nivel de datos (ver `DATA_MODEL.md` §1) pero la UX de mapeo y las heurísticas de sugerencias automáticas se diseñan con clientes reales delante.
