# Deploying the cloud ingest API to a VPS

The Python collector and the Android app both POST decoded measurements to one
shared MySQL/MariaDB database. This guide deploys that database plus the ingest
API (`cloud/`) on a VPS, with Caddy in front for automatic HTTPS.

```
  collector / Android ──POST (HTTPS, bearer token)──►  Caddy ──►  ingest API ──►  MariaDB
```

Only Caddy is reachable from the internet; the API and the database sit on an
internal Docker network and publish no host ports. See `cloud/README.md` for
how the pieces fit together, and `cloud/docker-compose.prod.yml` for the stack.

Works on any Linux VPS with Docker (Hostinger VPS, DigitalOcean, Hetzner, …).
Shared/cPanel hosting will **not** work: the API is Python, and cPanel shared
plans do not run Python.

## 0. Prerequisites

* A VPS running Ubuntu 22.04 or 24.04, with SSH access.
* A domain or subdomain whose DNS you control (for the free TLS certificate).
  If you have none, use a free one from e.g. DuckDNS, or start with the
  self-signed fallback at the bottom of `cloud/Caddyfile`.

## 1. Get the code onto the VPS

```bash
ssh root@<vps-ip>

# Install Docker (includes the compose plugin).
curl -fsSL https://get.docker.com | sh
# Re-login so your user is in the docker group (or use `sudo` for docker cmds):
exit
ssh root@<vps-ip>

git clone https://github.com/paicheck/odb-scanner.git
cd odb-scanner/cloud
```

If the repo is private, clone with a deploy key instead, or copy just what the
build needs (the whole repository is the build context):

```bash
# from your machine
scp -r /path/to/odb_scanner root@<vps-ip>:~/odb-scanner
```

## 2. Set the secrets

```bash
cp .env.example .env
nano .env
```

Fill in `DB_ROOT_PASSWORD`, `DB_PASSWORD`, `API_TOKEN`, and `DOMAIN`. Generate
the two passwords and the token on your own machine with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`API_TOKEN` is the value you will put in the collector's `config.local.yaml` and
the Android app's Settings → Cloud.

## 3. DNS and firewall

* Add an **A record** for `DOMAIN` pointing at the VPS's public IP.
* Open inbound **80** and **443** (and keep **22** for SSH). On a VPS with
  `ufw`: `ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 22/tcp && ufw enable`.

## 4. Start the stack

```bash
cd ~/odb-scanner
docker compose -f cloud/docker-compose.prod.yml up -d --build
docker compose -f cloud/docker-compose.prod.yml ps
```

The first `up` builds the API image (a minute or two), then waits for MariaDB to
be healthy before starting the API.

## 5. Verify

```bash
# liveness (unauthenticated)
curl https://$DOMAIN/healthz
# -> {"ok":true}

# status (needs the token)
curl -H "Authorization: Bearer <API_TOKEN>" https://$DOMAIN/status
# -> {"ok":true,"measurements":0,"sources":0}
```

If `/healthz` fails: check DNS has propagated, ports 80/443 are open, and
`docker compose -f cloud/docker-compose.prod.yml logs caddy api`.

## 6. Point the collector at it

On the collecting machine, in `config.local.yaml` (gitignored):

```yaml
cloud:
  enabled: true
  api_url: "https://<DOMAIN>"
  token: "<API_TOKEN>"
```

Then backfill everything already collected:

```bat
.venv\Scripts\python main.py sync-cloud
```

The first sync mirrors the whole local history and can take a while on a large
database; it resumes from a watermark if interrupted. A running `collect` also
uploads in the background, and a `sync-cloud` scheduled task just works with no
further change.

The **Android app** uses the same URL and token in **Settings → Cloud**.

## Operations

```bash
cd ~/odb-scanner
COMPOSE="docker compose -f cloud/docker-compose.prod.yml"

$COMPOSE logs -f api          # follow the API log
$COMPOSE restart api          # pick up a token or env change
$COMPOSE down                 # stop (keep data)
$COMPOSE down -v              # stop AND wipe the database (destructive)

# look inside the database (it has no host port, so exec a client in the box):
$COMPOSE exec db mariadb -u odb_ingest -p$DB_PASSWORD odb_cloud \
  -e "SELECT source_id, COUNT(*) FROM measurements GROUP BY source_id;"
```

### Update the API

```bash
git pull
$COMPOSE up -d --build api
```

The API creates/widens its own tables on first request (`cloud/db.py`), so there
is no migration step.

### Rotate the token (e.g. a phone was lost)

Edit `API_TOKEN` in `cloud/.env`, then `$COMPOSE up -d api`. Every client
(collector + phone) must be given the new token; the old one stops working.

### Back up the database

```bash
$COMPOSE exec db mariadb-dump -u root -p$DB_ROOT_PASSWORD --single-transaction odb_cloud > cloud-backup.sql
```

Copy `cloud-backup.sql` somewhere off the VPS.

## No domain yet (self-signed, test only)

If you have no domain, use the commented `:443 { tls internal ... }` block in
`cloud/Caddyfile`, then set `cloud.verify_tls: false` in `config.local.yaml` on
every client and use `https://<vps-ip>` as the URL. This encrypts the token in
transit but is not authenticated — treat it as a throwaway test and move to a
real domain before putting real data through it.
