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

## 3. What was built

| Area | What | Commit |
|---|---|---|
| Health | Circuit breaker per service: 3 curable failures rest it 60 s, doubling to 10 min; one trial request at a time when the rest ends; a refused key, quota or permission is marked NEEDS_ATTENTION, not retried, until the key or settings change; resting services skipped, the soonest tried if all rest | `f8de645` |
| Retries | Only what cannot have been processed (connect failures, 429, 503/529), once, after Retry-After / retry-after-ms up to 5 s; never after a read timeout or a dropped connection once sent, never for keys, quota, permission, model or capability, never once stopped | `f8de645` |
| Context | The conversation's words kept apart from any one service; a backup or rebuilt session is seeded with them; a new trip, New chat, 10 min idle or 16 turns starts afresh; memory can be turned off | `f8de645` |
| Usage | Tokens per service per day from replies; cost only from your prices (none when a reply gave no counts); daily request and spending limits, warned at 80 %; a reached limit moves to a backup | `f8de645`, `df021d3` |
| Speech fallback | Online recognition that cannot connect → on the phone ("Say that again"), no consent needed (more private); on-the-phone → online only with consent; a chosen voice needing the network → the phone's own, shown | `f8de645` |
| Answer style | brief / normal / detailed, "auto" = brief on a trip, navigating or from the car; speech speed; en-GB or en-US | `f8de645`, `df021d3` |
| Streaming | OpenAI-style services: server-sent events (SSE) → the same reply shape (tool calls from pieces, error events); sentence by sentence to the speech queue as the turn's own, so a stop drops the rest and nothing resumes; a non-streaming service is asked normally; no backup over spoken words | `64da35e` |
| History | One structured record per turn (times for recognition, first words, answer, speaking; outcome, category, stage, stopped, backup); local, 30 days / 500 records, words only by consent; statistics by service and capability; delete | `64da35e`, `df021d3` |
| Vehicle | get_trip's live_check (the app's en-route check), get_charging_history, provenance on every reading; the prompt forbids the AI's own battery figures | `64da35e` |
| Offline | LocalAnswers: battery, range, next stop, arrival, trip left, faults from the same tools when no service could answer; Setup says what works offline | `64da35e`, `df021d3` |
| In the car | A full-width Stop while anything runs; Retry after a failure (phone, car) | `64da35e`, `df021d3` |

## 4. Investigated, not built

### True barge-in (talking over the assistant without the wake phrase)

What it needs:
- **A microphone open during speech output.** The recogniser is not running
  then, but the wake-word recorder is, and it could be.
- **Voice activity detection on it.**
- **Echo cancellation good enough** that the assistant's own voice does not
  count as speech.

What stands in the way:
- **No recogniser during speech.** Android's SpeechRecognizer cannot run
  while text-to-speech plays without hearing it.
- **The wake-word recorder uses VOICE_RECOGNITION.** That source has no
  echo cancellation on many phones; `AcousticEchoCanceler`, now enabled
  where offered, depends on the phone.
- **Bluetooth.** Through a car's Bluetooth the microphone is the car's, the
  speech goes through the car's speakers, and the phone's echo canceller
  has no reference signal.
- **Android Auto** owns the audio routing.
- **Echo looks like speech.** A detector that reacts to any voice would
  react to the assistant's own voice through the car's speakers. That is
  the "interprets its own voice as a command" loop the roadmap warns of.

The wake phrase avoids all of this: Vosk only listens for one phrase, which
the assistant itself does not say. So the safe barge-in is the wake phrase,
or a tap.

A free-speech barge-in would be a setting, off by default, measured on the
Pixel 7 and in the car first. It is not claimed here, and nothing was built
for it.

### Local AI

- **Ollama on the PC.**
  - **The working route:** the phone reaches it through the custom
    OpenAI-compatible service at an **https** address, for example
    `tailscale serve --bg --https=443 http://localhost:11434` (the PC
    already uses Tailscale for `odb-ingest`), with the base URL
    `https://<pc>.<tailnet>.ts.net/v1` and a model such as
    `llama3.2:3b` or `qwen2.5:7b`. Tool calling depends on the model.
  - **Why not plain HTTP:** a plain `http://192.168…` address is refused,
    because the app allows no cleartext. Allowing it would need a
    network-security config naming each host, so it was left as it is.
  - **What it brings:** streaming and the setup tests work with it, and
    nothing leaves the home network.
- **A model on the phone.** Gemini Nano (AICore) is not on the Pixel 7.
  MediaPipe's LLM inference or llama.cpp would need a model download of
  1–4 GB and a new native dependency. Speed, memory and battery could only
  be measured on the phone, so it was not attempted.
- **Recognition on the phone:** on-device SpeechRecognizer where Android
  has it (Setup shows whether), plus the fallback above. Vosk's large
  English model (1.8 GB) could do full recognition offline. That is not
  done; only the wake word uses Vosk.
- **Speech on the phone:** the engine's voices. Setup marks those that need
  the network "(online)", and the fallback uses the engine's own.

### Drives on the phone

"How much did my last journey use?" needs the PC's drive splitting
(`analysis/trips.py`) ported to Kotlin with shared vectors, as the planner
is. That was not done here. The phone has the readings; the PC's Trips page
answers it.

## 5. Results

See `ai_assistant_handover.md` (verification, what needs the phone).
