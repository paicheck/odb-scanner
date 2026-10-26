# Deploying the cloud ingest API on cPanel ("Setup Python App")

If your cPanel plan has **Software → Setup Python App**, it runs the ingest API
on your shared account via CloudLinux Passenger, connected to your cPanel MySQL
on `localhost`. The same `cloud/api.py` runs here as in the Docker/VPS deploy —
`a2wsgi` bridges FastAPI (ASGI) onto Passenger (WSGI), so `cloud/mapping.py`
stays the single source of truth.

> **Check first:** create a throwaway application and look at the Python
> version list. You need **3.9+** (ideally 3.10+). Some hosts (including some
> Hostinger plans) show the icon but restrict Python to VPS — if the highest
> version is 3.7/3.8 or the screen errors, use `cloud/DEPLOY.md` on a VPS
> instead.

## 1. Create the database and user

cPanel → **MySQL Databases**:

1. **Create database** `odb_cloud`.
2. **Create user** `odb_ingest` with a strong password.
3. **Add the user to the database** with **ALL PRIVILEGES**.

The API creates its own tables on first request; it needs CREATE/ALTER on this
database, which "ALL PRIVILEGES" gives it.

## 2. Upload the app

Create the application in **Setup Python App** with an application root such as
`cloud-api` under your home directory, and a URL on a dedicated **subdomain**
(e.g. `cloud.yourdomain.tld`) rather than a path — path mounts complicate the
API's base URL.

Into the application root, put:

```
passenger_wsgi.py        (from cloud/)
requirements-cpanel.txt  (from cloud/, rename to requirements.txt)
cpanel.env               (created from cloud/cpanel.env.example, filled in)
cloud/                   (the whole package: __init__.py, api.py, config.py,
                          db.py, mapping.py, schema.sql)
```

* Rename `requirements-cpanel.txt` to `requirements.txt` so Passenger installs
  it, or install manually from the app's "Run pip install" / terminal:
  `pip install fastapi pydantic pymysql a2wsgi`.
* `schema.sql` must sit next to `db.py` — `cloud/db.py` reads it at that
  relative path, so copy it as part of `cloud/`.

## 3. Configure

Edit `cpanel.env` (gitignored) with the `ODB_CLOUD_*` values from step 1 plus a
generated token:

```
ODB_CLOUD_API_TOKEN=<generated-token>
ODB_CLOUD_DB_HOST=localhost
ODB_CLOUD_DB_PORT=3306
ODB_CLOUD_DB_USER=odb_ingest
ODB_CLOUD_DB_PASSWORD=<the-user-password>
ODB_CLOUD_DB_NAME=odb_cloud
```

Then **Restart** the application from its cPanel page.

## 4. Verify

```bash
curl https://cloud.yourdomain.tld/healthz
# -> {"ok":true}

curl -H "Authorization: Bearer <token>" https://cloud.yourdomain.tld/status
# -> {"ok":true,"measurements":0,"sources":0}
```

## 5. Point the collector at it

In `config.local.yaml` on the collecting machine:

```yaml
cloud:
  enabled: true
  api_url: "https://cloud.yourdomain.tld"
  token: "<generated-token>"
```

Then `python main.py sync-cloud` backfills; the scheduled task and the Android
app (Settings → Cloud) use the same URL + token.

## Troubleshooting

* **502 / "cannot import"**: check the app's stderr log (Setup Python App →
  your app → log file) — usually a missing dependency or a `cpanel.env` typo.
* **"connection refused" on /status**: the DB host must be `localhost` (or the
  `127.0.0.1` cPanel shows for the user), not the remote MySQL hostname.
* **Passenger idle**: Passenger may sleep the app between requests; that is
  harmless here — the API opens a fresh MySQL connection per request, so a
  cold start just takes a little longer on the first hit.
