# Cloud logging — one MySQL schema, two writers

The Python collector and the Android app both mirror their decoded
measurements into **one** MySQL/MariaDB database, so a reading taken in the car
is in the same archive as a reading taken on the laptop.

```
  Python collector ─┐
  (Windows)         ├─►  cloud ingest API  ─►  MySQL / MariaDB
  Android app      ─┘     (FastAPI, token)      (shared schema)
```

Neither writer talks to the database. Both POST batches to the ingest API,
which holds the credentials and is the only thing that writes. That is what
lets the phone carry a **revocable token** instead of a database password, and
means the database port never has to be open to the internet.

## Pieces

| File | Runs where | Does |
|---|---|---|
| `schema.sql` | — | The MySQL DDL (`sources`, `measurements`). Applied automatically on first use. |
| `api.py` | cloud | The FastAPI ingest service. `POST /ingest/measurements`. |
| `db.py` | cloud | The only module that opens a MySQL connection. |
| `mapping.py` | both | Shared, pure validation/normalisation (timestamps, ids). |
| `client.py` | collector host / phone | Reads local rows, batches, uploads, advances a watermark. |
| `Dockerfile` | — | Container for the API. |

## Idempotency (why retries are safe)

Every row carries a client-generated UUID as its primary key. A sender that
never learns whether its upload landed simply sends it again; the API uses
`INSERT IGNORE`, so a row that already exists is left untouched and the reply
reports it as a duplicate.

* The **Python collector** derives the id deterministically from
  `(source_id, local row id)` (`mapping.cloud_measurement_id`), so the same
  local row always maps to the same cloud id — including across restarts.
* The **Android app** stores a random UUID on the row when it is written.

The id namespace in `mapping.CLOUD_ID_NAMESPACE` **must never change**: it is
what makes a retry the same row instead of a duplicate.

## Try it locally first (no hosting, no accounts)

`cloud/docker-compose.yml` brings up MariaDB plus the ingest API, with the same
shape as a real deploy — the API holds the credentials, the database is not
published, and there is one token. Prove logging works before paying for
anything:

```bash
docker compose -f cloud/docker-compose.yml up -d --build
python tools/cloud_local_check.py
docker compose -f cloud/docker-compose.yml down -v      # -v also wipes the data
```

### On Windows, without Docker and without admin rights

Docker Desktop needs administrator rights, WSL2 and a reboot. Where that is not
available, `tools/local_cloud.py` runs the *same* pair — a real MariaDB 11.8
(the official Windows ZIP, unprivileged) and the same `cloud.api` under uvicorn:

```bat
python tools/local_cloud.py install      :: once: download + initialise MariaDB
python tools/local_cloud.py up           :: start MariaDB and the ingest API
python tools/cloud_local_check.py
python tools/local_cloud.py down         :: stop both

python tools/local_cloud.py status                        :: what is running
python tools/local_cloud.py sql "SELECT COUNT(*) FROM measurements;"
```

It uses port **3307**, not 3306, so it cannot collide with a MySQL already
installed. Everything it creates lives under `%LOCALAPPDATA%\odb_scanner`,
deliberately outside the repository: the repository sits in OneDrive, and a live
database directory would be synced and file-locked continuously. The names and
passwords match the compose file, so `cloud_local_check.py` and the collector
overrides below work unchanged against either stack.

The API needs its database driver on the host, which the container would
otherwise bundle. `up` checks for it and names anything missing:

```bat
python -m pip install -r cloud/requirements.txt
```

`cloud_local_check.py` seeds a scratch SQLite database with a few clearly-fake
rows, uploads them through the real API into the real database, and reads the
counts back from `/status`. Nothing in `data/real_car.db` is touched. Exit code
`0` means it works end to end; `2` means the stack is not up.

To drive the *real* collector against the local stack instead, override the
target from the environment (no config edit needed — `ODB_CLOUD_ENABLED`,
`ODB_CLOUD_API_URL`, `ODB_CLOUD_TOKEN` all work):

```bat
set ODB_CLOUD_ENABLED=true
set ODB_CLOUD_API_URL=http://127.0.0.1:8080
set ODB_CLOUD_TOKEN=local-dev-token
.venv\Scripts\python main.py sync-cloud
```

The database is deliberately not reachable except through the API — the API
holds the credentials, and the local stack binds MySQL to loopback. To look
inside it, use a client that goes through the API's database user:

```bash
# Docker
docker compose -f cloud/docker-compose.yml exec db \
  mariadb -u odb_ingest -pingest-local-only odb_cloud \
  -e "SELECT source_id, COUNT(*) FROM measurements GROUP BY source_id;"

# Docker-free (Windows)
python tools/local_cloud.py sql "SELECT source_id, COUNT(*) FROM measurements GROUP BY source_id;"
```

Every default in the compose file is a development value. Override any of them
(including the passwords) by creating `cloud/.env`, which is gitignored.

## Deploying the API

### 1. Create the database

Any managed MySQL/MariaDB works (PlanetScale, Azure Database for MySQL, AWS RDS,
a VPS). Create an empty database and a user with rights to it — the API creates
its own tables on first request.

```sql
CREATE DATABASE odb_cloud CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'odb_ingest'@'%' IDENTIFIED BY 'a-long-password';
GRANT ALL PRIVILEGES ON odb_cloud.* TO 'odb_ingest'@'%';
```

### 2. Run the service

```bash
docker build -f cloud/Dockerfile -t odb-cloud-ingest .

docker run -p 8080:8080 \
  -e ODB_CLOUD_API_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  -e ODB_CLOUD_DB_HOST=your-db.example.com \
  -e ODB_CLOUD_DB_PORT=3306 \
  -e ODB_CLOUD_DB_USER=odb_ingest \
  -e ODB_CLOUD_DB_PASSWORD='a-long-password' \
  -e ODB_CLOUD_DB_NAME=odb_cloud \
  odb-cloud-ingest
```

Put it behind TLS (a managed platform or a reverse proxy). The token is a
bearer credential: over plain http it is readable by anyone on the path.

### Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `ODB_CLOUD_API_TOKEN` | yes | The bearer token clients must present. The service refuses to start without it. |
| `ODB_CLOUD_DB_HOST` | yes | Database host. |
| `ODB_CLOUD_DB_USER` | yes | Database user. |
| `ODB_CLOUD_DB_NAME` | yes | Database name. |
| `ODB_CLOUD_DB_PORT` | no | Defaults to `3306`. |
| `ODB_CLOUD_DB_PASSWORD` | no | Password (empty if the user has none). |
| `ODB_CLOUD_DB_SSL_CA` | no | Path to a CA bundle; enables TLS to the database. |

## The HTTP contract

`POST /ingest/measurements`

```http
Authorization: Bearer <ODB_CLOUD_API_TOKEN>
Content-Type: application/json
```

```json
{
  "source": { "source_id": "python:garage-pc", "kind": "python", "label": "garage-pc" },
  "measurements": [
    {
      "id": "b3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d5a44",
      "vin": "WVWZZZE1ZMP087053",
      "ts": "2026-10-17T12:00:00Z",
      "ecu": "bms", "service": "UDS-0x22", "pid": "0x2AB8",
      "key": "hvb_energy_content_kwh",
      "value": 31.4, "unit": "kWh",
      "provenance": "reported", "decoding_method": "scalar",
      "doc_status": "verified", "raw_response": "62 2AB8 ...",
      "success": true, "error": null
    }
  ]
}
```

Response `200`:

```json
{ "accepted": 1, "duplicates": 0, "source_id": "python:garage-pc" }
```

Codes: `401` bad/missing token · `413` batch larger than 5000 rows · `422` a
field is invalid (the whole batch is rejected, never half-written) · `5xx` the
database is unavailable — retry later; nothing was lost.

`GET /healthz` is unauthenticated and returns `{"ok": true}` (liveness only).
`GET /status` requires the token and returns row counts.

## Turning it on for the collector

Put the endpoint and token in `config.local.yaml` (gitignored) — or export
`ODB_CLOUD_TOKEN`:

```yaml
cloud:
  enabled: true
  api_url: "https://odb-cloud.example.com"
  token: "the-same-token-the-api-was-started-with"
```

Then either let a running collector push continuously:

```bat
.venv\Scripts\python main.py collect
```

or upload what has already been collected, once:

```bat
.venv\Scripts\python main.py sync-cloud
```

The first sync backfills the whole local history — every measurement up to the
current watermark. That is deliberate (the cloud is meant to hold the archive),
but on a large database expect the first run to take a while. Cancel it and
re-run at any time; it resumes from the last accepted batch.

### What the watermark means

Progress is one row in the existing SQLite `meta` table:
`cloud_sync.measurements.last_id`. It records the highest local measurement id
the API has *accepted*, and it only moves after a successful upload. If the
cloud is unreachable, the collector logs a warning every interval and carries on
writing locally; nothing is lost and nothing is retried out of order.
