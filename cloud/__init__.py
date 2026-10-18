"""Cloud logging for odb_scanner.

Two halves that deliberately never share a process:

  * ``cloud/client.py`` -- runs on the machine (or phone) that collects data and
    only ever POSTs to the ingest API. It holds an API token, never a database
    password.
  * ``cloud/api.py`` + ``cloud/db.py`` -- the ingest service, deployed beside
    MySQL. It holds the database credentials and is the only writer.

See ``cloud/README.md`` for deployment.
"""
