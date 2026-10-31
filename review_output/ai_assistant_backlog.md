# AI assistant backlog — 2026-10-31

Branch `ai-assistant` (from `ai-voice`, `7a53162`). Nothing pushed, merged or
installed. Builds here: `testDebugUnitTest` and `lintVitalRelease` only (no
APK is packaged, so neither tested APK is overwritten).

| ID | Task | Priority | Status |
|---|---|---|---|
| AA01 | Investigation and the architectural problem (each session has its own memory) | — | done (review §1–2) |
| AA02 | Provider health: circuit breaker per service (consecutive failures, cooldown with backoff, half-open trial, no flapping); bounded retry policy (transient only, Retry-After, never after the request may have been processed, never a cancelled one); credentials/quota not retried; shown in Setup | highest | pending |
| AA03 | Conversation carried across services: a seeded backup or rebuilt session keeps the conversation's words | highest | pending |
| AA04 | Usage and cost: tokens per service per day from the replies; cost only from prices you enter; daily request and spending limits, warned at 80 % | highest | pending |
| AA05 | Speech fallback: online recognition → on the phone on a network failure (more private, allowed); an online voice → the engine's offline voice | highest | pending |
| AA06 | Interaction history: one structured record per turn (timings, provider, model, outcome, category, interrupted, fallback), local, bounded, with retention and deletion; statistics and filters in Setup | high | pending |
| AA07 | Streaming for the OpenAI-style services (SSE, tool calls in pieces) and speech by sentence as it arrives; time to first word recorded; Claude documented | high | pending |
| AA08 | Conversation and voice settings: answer style (brief / normal / detailed, brief while driving), memory on/off, a new journey starts a new conversation, speech rate, language | high | pending |
| AA09 | Vehicle awareness: the app's live arrival check in the trip tool, charging history, provenance on readings | high | pending |
| AA10 | Offline: answers from the phone's own data when no AI service can be reached (battery, next stop, arrival, trip, faults); what works offline, in Setup | later | pending |
| AA11 | In the car: Retry after a failure, a prominent Stop | high | pending |
| AA12 | Docs and handover | — | pending |
