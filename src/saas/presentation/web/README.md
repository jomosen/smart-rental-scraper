# RentRadar Web — comandos de desarrollo

Todos los comandos asumen PowerShell desde la raíz del repo
(`c:\xampp\htdocs\python\booking-scraper`).

---

## Arrancar la API FastAPI

La auth es **email + contraseña** (JWT de 30 días en cookie httpOnly). Necesita
`JWT_SECRET` en `.env`. En desarrollo, `DEV_TENANT_ID` hace que los endpoints de
datos sirvan ese tenant sin cookie; aun así la SPA muestra el login porque
`/api/auth/me` no aplica el bypass. Da de alta usuarios con
`python scripts/create_tenant.py` (o `scripts/seed_user.py` para fijar contraseña).

```powershell
# Terminal 1 — DESDE LA RAÍZ del repo (donde está src/)
# python -m uvicorn añade el cwd a sys.path; el comando uvicorn directo no lo hace
python -m uvicorn src.saas.presentation.api.app:app --reload --port 8000
```

La SPA muestra la pantalla de **login** (email + contraseña); tras autenticarse
entra en la vista **Radar de precios**. Los controles
de base / redondeo / maestro / regla global y la navegación de zona disparan un refetch
con query params efímeros (`?base=&round=&master=&zone=&rule_op=&rule_val=&rule_mode=&rule_floor=&rule_ceiling=`);
nada se persiste. El cálculo vive solo en el servidor (assembler + cross_tariff_calc).

Verificar:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
# → status: ok  service: rentradar-api

Invoke-RestMethod http://localhost:8000/api/noexiste
# → 404  detail: @{error=API endpoint not found}
```

---

## Dev con hot-reload (front + API)

```powershell
# Terminal 1 — API (ver arriba)
uvicorn src.saas.presentation.api.app:app --reload --port 8000

# Terminal 2 — front Vite
cd src\saas\presentation\web
npm run dev
```

Abrir http://localhost:5173 — el proxy reenvía `/api` a `:8000`.
El placeholder debe mostrar **"API: ok · rentradar-api"**.

---

## Build de producción y servir desde uvicorn

```powershell
# Paso 1 — construir la SPA
cd src\saas\presentation\web
npm run build

# Paso 2 — arrancar solo uvicorn (sirve la SPA desde dist/)
cd ..\..\..\..
uvicorn src.saas.presentation.api.app:app --port 8000
```

Abrir http://localhost:8000 — misma pantalla servida por FastAPI.

Verificar que /api/noexiste sigue siendo 404 JSON (nunca index.html):

```powershell
Invoke-RestMethod http://localhost:8000/api/noexiste
# → 404  detail: @{error=API endpoint not found}
```

---

## Instalar dependencias (primera vez)

```powershell
# Python
pip install -r requirements.txt

# Node (desde la carpeta web/)
cd src\saas\presentation\web
npm install
```
