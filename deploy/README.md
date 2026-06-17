# Deploy — SaaS API on a VPS (Apache reverse proxy)

Runbook for **Option A**: the existing Apache (already serving PHP on 80/443)
acts as a pure reverse proxy in front of a standalone `uvicorn` process. Apache
runs none of the Python code — it only forwards HTTP to `127.0.0.1:8000`.

```
internet ──443/TLS──► Apache ──proxy──► 127.0.0.1:8000 (uvicorn: API + React SPA)
                         └── your PHP vhosts keep working untouched
```

The project lives **outside** any DocumentRoot (e.g. `/opt/rentradar`) so source
and `.env` are never web-servable.

Artifacts:
- `systemd/rentradar-api.service` — supervises uvicorn.
- `apache/rentradar.conf` — the reverse-proxy vhost.
- `postgres/init/` — DB role bootstrap (runs only on first volume init).

---

## 0. Access the private repo on the VPS (one-time)

`smart-rental-scraper` is a **private** GitHub repo, so the VPS needs read
credentials to clone it. Recommended: a **read-only SSH deploy key** scoped to
this repo (not your personal account).

```bash
# On the VPS — dedicated key, no passphrase (for unattended git pulls):
ssh-keygen -t ed25519 -C "rentradar-vps-deploy" -f ~/.ssh/rentradar_deploy -N ""
cat ~/.ssh/rentradar_deploy.pub
```

Paste that public key in GitHub → repo **Settings → Deploy keys → Add deploy
key**, leaving **"Allow write access" unchecked**. Then point the VPS at it and
clone:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-rentradar
  HostName github.com
  User git
  IdentityFile ~/.ssh/rentradar_deploy
EOF

sudo mkdir -p /opt/rentradar && sudo chown $USER /opt/rentradar
git clone github-rentradar:jomosen/smart-rental-scraper.git /opt/rentradar
```

Future updates are then just `cd /opt/rentradar && git pull` (see "Redeploy").

Alternative (quicker, less clean): a GitHub **Personal Access Token** with the
`repo` scope over HTTPS —
`git clone https://<PAT>@github.com/jomosen/smart-rental-scraper.git /opt/rentradar`.
The deploy key is preferred: read-only and limited to this single repo.

## 1. Virtualenv

```bash
cd /opt/rentradar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. `.env` (not in git)

Copy `.env.example` to `.env` and fill the production values — especially
`ADMIN_DATABASE_URL`, `APP_DATABASE_URL`, `SUPER_DATABASE_URL` and the auth
secret(s). `load_dotenv()` reads it from the working directory (the repo root).

```bash
cp .env.example .env && nano .env
```

## 3. Database

Bring up Postgres (Docker compose or a managed/native instance) and apply
migrations **as the admin role** (Alembic reads `ADMIN_DATABASE_URL`):

```bash
docker compose up -d postgres        # if using the bundled compose
.venv/bin/alembic upgrade head
```

Remember: the `deploy/postgres/init/` scripts only run when the volume is first
created. After `docker compose down -v`, re-init and re-run migrations.

## 4. Build the front-end (dist/ is gitignored)

`uvicorn` serves the SPA from `src/saas/presentation/web/dist/`, so the build
must exist on the server. Either build on the box (needs Node 20+):

```bash
npm --prefix src/saas/presentation/web ci
npm --prefix src/saas/presentation/web run build
```

…or build locally and copy the `dist/` folder up (e.g. `rsync`). Without it the
app returns a "Front-end not built" message.

## 5. uvicorn service

```bash
sudo cp deploy/systemd/rentradar-api.service /etc/systemd/system/
# edit User=/paths if you didn't use /opt/rentradar + a 'rentradar' user
sudo systemctl daemon-reload
sudo systemctl enable --now rentradar-api
curl -s http://127.0.0.1:8000/api/health      # → {"status":"ok",...}
```

## 6. Apache reverse proxy + TLS

```bash
sudo a2enmod proxy proxy_http headers ssl rewrite
sudo certbot certonly --apache -d radar.tudominio.com
sudo cp deploy/apache/rentradar.conf /etc/apache2/sites-available/
# replace radar.tudominio.com with your real (sub)domain in the file
sudo a2ensite rentradar
sudo apache2ctl configtest && sudo systemctl reload apache2
```

Visit `https://radar.tudominio.com` — Apache terminates TLS and proxies to
uvicorn, which serves the dashboard and the API.

---

## Redeploy (after a code change)

```bash
cd /opt/rentradar && git pull
.venv/bin/pip install -r requirements.txt          # if deps changed
.venv/bin/alembic upgrade head                      # if migrations changed
npm --prefix src/saas/presentation/web run build    # if front-end changed
sudo systemctl restart rentradar-api
```

> Front-end edits (TSX/CSS) are invisible until you rebuild `dist/` AND restart
> is not even needed for static assets — but a browser hard-refresh is.
