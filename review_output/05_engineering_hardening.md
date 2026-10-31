# 05 Engineering hardening

## Identified weaknesses (verify in code)

- Collector: Bluetooth disconnect handling, partial responses, timeouts, reconnection, long-running resource leaks need review.
- Database: WAL behaviour, concurrent access, migration rollback, backup verification, growth/retention.
- Security: token handling, CSRF, host validation, API key storage, prompt injection, untrusted external data.
- AI: provider failures, fallback correctness, rate limits, cost controls, evidence grounding.
- Android: background collection, process death, Bluetooth lifecycle, migrations, nav state, Android Auto lifecycle.

## Potential failure modes

- Adapter disconnect mid-collection → data gaps; need reconnect + mark gaps.
- Malformed OBD response → decoder crash; need fuzz tests.
- DB corruption → need backup verification and restore drill.
- Prompt injection via measurement text → AI must treat data as evidence only.
- DNS rebinding on loopback bind → already mitigated; verify tests.

## Security improvements

- Keep token auth; consider short-lived sessions for network binds.
- Validate all inputs; SSRF checks for provider URLs (already server-side).
- Dependency audit (pip-audit) already; keep pinned actions.
- Redact secrets in logs.

## Data integrity

- Transactional writes; verify summary cache rebuild after deletes.
- Clock change handling; timestamp validation.
- Duplicate measurement detection.

## Collector reliability

- Reconnect with backoff; detect sleep/wake; resume.
- Partial response assembly; timeout handling.

## Database resilience

- Backup verification (integrity check, restore test).
- Retention policies (prune already manual).
- Migration versioning and rollback.

## AI reliability

- Structured evidence retrieval; citation validation.
- Fallback correctness tests.
- Rate-limit handling; cost caps.

## Android stability

- Lifecycle tests; migration tests; background collection reliability.

## Testing strategy

- Property/fuzz tests for decoders.
- Malformed OBD response tests.
- Migration tests.
- Backup/restore tests.
- Security tests (CSRF, host, XSS).
- Provider failure simulation.
- Python/Kotlin differential tests.

## Monitoring and observability

- Health endpoint; metrics (collection cycle time, success rates, DB size).
- Structured logs with redaction.

## Flaky test note

`tests/test_trips.py::test_trip_log_totals_and_what_the_cold_costs`: possible causes include time/date dependence, floating accumulation, or test data ordering. Recommend deterministic fixtures and repeated runs; do not modify without diagnosis. (No change made.)
