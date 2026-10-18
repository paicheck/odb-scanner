# Container for the odb_scanner cloud ingest API.
#
# Build from the REPOSITORY ROOT, so the cloud package is on the path:
#     docker build -f cloud/Dockerfile -t odb-cloud-ingest .
#
# It needs the environment variables documented in cloud/README.md
# (ODB_CLOUD_API_TOKEN and the ODB_CLOUD_DB_* set). It refuses to start
# without a token, by design.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY cloud/requirements.txt ./cloud/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt

COPY cloud ./cloud

# Most PaaS platforms inject PORT; the default matches the EXPOSE below.
ENV PORT=8080
EXPOSE 8080

# --factory because the app is built from create_app(), which reads the
# environment and validates the token at startup rather than at import.
CMD ["sh", "-c", "uvicorn cloud.api:create_app --factory --host 0.0.0.0 --port ${PORT}"]
