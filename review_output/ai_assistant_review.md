# AI assistant review — 2026-10-31

Branch `ai-assistant`, from `ai-voice` at `7a53162`.

The versions being tested are preserved:
- **`navigation`** is unchanged (`eb646d3`).
- **`ai-voice`** is unchanged (`7a53162`, local tag `tested-ai-voice-7a53162`).
  The only `ai-voice` APK ever built is the debug build (sha256 `4d6dd612…`).
  A copy is in `~/odb-scanner-tested/`, because a debug build on this branch
  would overwrite `~/odb-scanner-build/.../debug/app-debug.apk`. No release
  APK was built for `ai-voice`.
- **The release APK** (`a4b5d207…`) is unchanged.

Nothing is pushed or installed.

## 1. What already meets the roadmap (`ai-voice`, read before changing)

| Roadmap item | Already there | Where |
|---|---|---|
| Error classification | twelve categories with reasons (dns, tls, expired, region, overloaded…) | `ai/AiErrors.kt` |
| LLM fallback | chosen service, then backups in order; a turn that acted is not repeated on a backup; sticky for the conversation; who answers is shown | `ai/FallbackChat.kt`, `Assistant.active` |
| Cancellation | one transport that aborts; Cancellable for the SDK; rollback of a stopped turn; nothing stale plays | `ai/Http.kt`, `ai/SpeechQueue.kt` |
| Voice state machine | IDLE…ERROR with turn ids; Stop, barge-in (tap, wake phrase) | `ai/VoiceMachine.kt`, `ai/Voice.kt` |
| Deadline | 120 s per question; per-request timeouts | `Assistant.TURN_TIMEOUT_MS` |
| Diagnostics | last result per provider and test; connection, key, AI, STT, TTS, whole chain | `ai/Diagnostics.kt`, `AiVoiceSetupScreen.kt` |
| Conversation context | one append-only session per conversation, 16 turns or 10 min | `Assistant.sessionFor` |
| Brief spoken answers | the system prompt asks for 1–3 short sentences | `AssistantTools.SYSTEM_PROMPT` |
| Read-only vehicle tools | 17 tools: battery, faults, trip, directions, chargers, readings, history, navigation, route changes; state changes (plan, re-plan, navigate, reroute, avoid/use/network) need a yes (ConfirmGate) | `ai/AssistantTools.kt` |
| Privacy | the VIN is redacted from every tool result; no exact position is sent; keys are encrypted | `AssistantTools.redactVin`, `KeyVault` |
| Freshness | every reading carries its age | `AssistantTools.reading` |

## 2. Gaps, and one architectural problem

### The problem: each service has its own memory

Each AI service keeps its own copy of the conversation (`OpenAiChatSession`,
`ClaudeChatSession`), and so does a session that is replaced. Context is
therefore lost exactly when it is needed:
- **A fallback:** a backup taking over mid-conversation starts with no
  history. "How much battery will I have *there*?" fails just after the main
  service did.
- **A new session:** changed settings (or a later change of answer style)
  start with no history either.

**Fix, without a redesign:** the conversation's plain words (questions and
answers, no tool results) are kept outside the sessions. A new session for
the same conversation (a backup, a rebuilt session) is seeded with them.
Each session's own history stays append-only, as the providers need.

### Reliability

- **Fallback is the only recovery.** There is no retry, even for a 429 or 503
  that says when to come back, and no memory across conversations: a service
  that failed is asked first again on the next question.
- **No circuit breaker or health view.** A dead service costs a timeout
  (up to 90 s) on every new conversation.
- **The Retry-After header is never read.** `HttpResponse` carries no
  headers.
- **No usage or cost record, and no limits.**
- **Speech has no fallback.** The recogniser and the voice are each one
  mode: online recognition does not fall back to on-device on a network
  error, and an online voice does not fall back to an offline one.

### Voice experience

- **No streaming.** The answer is spoken only once it is complete.
  OpenAI-style services stream server-sent events (SSE), with tool calls
  arriving in pieces. Claude's SDK can stream (`createStreaming`), but
  version 3.2.0 has no helper to rebuild the final message, and Claude's
  reply must go back exactly as it came, thinking blocks included. That is
  documented and not done here.
- **No answer styles, speech rate or language choice.** The memory cannot be
  turned off, and a new journey does not start a new conversation.

### Diagnostics

Only the last result of each test is kept. There is no per-interaction
record (timings, outcome, interruptions, fallbacks) and no statistics.

### Vehicle awareness

- **"Will I make it?"** The trip tool gives the planned arrival, but not the
  app's own live check against the battery (`EnRoute.Assessment`, which
  `TripMonitor` already keeps).
- **No charging-history tool,** although the phone stores charging sessions.
- **Provenance.** A reading says its age but not its provenance
  (measured / calculated / simulated).
- **Drives exist only on the PC.** The phone has no drive splitting, so
  "last journey" questions need a port of `analysis/trips.py` (with shared
  vectors). Not done here.

### Offline

- **Nothing answers without an AI service,** even "what's my battery?",
  which the phone knows.
- **Local LLM.** Ollama over plain HTTP on the home network is blocked:
  there is no network security config, so plain HTTP is not allowed. The
  supported route is HTTPS, for example `tailscale serve` (already used for
  `odb-ingest`) in front of Ollama on the PC. The custom service accepts
  that.

### In the car

- **No Retry** after a failure.
- **Stop is one button among several** on the phone dialog.

## 3. Design

*Filled in as each item is built (`ai_assistant_progress.md`).*
