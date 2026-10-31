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

See `ai_assistant_handover.md` (verification, what needs the phone). For
the release candidate's validation, see §6–9 below.

## 6. Release-candidate validation — build state

Done on 2026-10-31, after the assistant had been merged into `main` and
pushed (`9fc7a2f`; CI green on every job). The aim was a release candidate
to test, not new features: each review area was read, and only clear
defects were fixed, each with a regression test.

- **Branch:** `ai-assistant-rc`, from `main` at `9fc7a2f`. Not pushed.
- **Kept as they were:**
  - `navigation` (`eb646d3`);
  - `ai-voice` (`7a53162`, tag `tested-ai-voice-7a53162`);
  - `ai-assistant` (`f3fc46e`).
- **The APK under test:** 20261031e (`7f94cf57…`), as delivered to the
  Pixel. It is untouched in `~/odb-scanner-build/app/outputs/apk/release/`
  and in `~/odb-scanner-tested/`.
- **How a candidate is built without touching it:** `-PodbBuildDir=<dir>`
  (commit `d11567e`). It moves the whole Gradle output, so tests, lint and
  the release build of this candidate all ran in `~/odb-scanner-build-rc`.
  Nothing was written to `~/odb-scanner-build`.

## 7. Defects found and fixed

| Area | Defect | Fix | Commit |
|---|---|---|---|
| Fallback | A trial request that was stopped, or held back by your daily limit, kept the service's one trial reserved. The service was never tried again until the app restarted. | `CircuitBreaker.release`: the trial is let go without counting a success or a failure. | `9fb4efd` |
| Fallback | The breaker was asked about every service before the first was tried. Each ask reserves a trial, so a trial could be reserved for a service never reached. | Each service is asked just before it is tried. | `9fb4efd` |
| Fallback | When every service left needed attention (a refused key, a spent quota), the soonest was asked anyway, and refused again. | Nothing is sent, and the reason is said. Only resting services get the trial. | `9fb4efd` |
| Fallback | Saving any setting (speech speed, history) reset every service's health, so a refused key was asked again at once. | Health resets only for a service now asked with another model, address or effort, or turned back on. A new key still resets its own service. | `9fb4efd` |
| Voice | An engine that never reported finishing an utterance (it died, or lost its audio route) held the speech queue for ever. Nothing after it was said, navigation guidance included. | Each utterance has a limit (`SpeechQueue.limitMs`, the speech test's own wait). Past it the utterance is dropped and a fresh engine says the rest. | `6af6487` |
| Voice | Utterance ids came from a plain counter used on two threads. Two utterances could share an id, so one's "done" would end the other. | The counter is atomic. | `6af6487` |
| Android Auto | Repeat said the answer as the app's own speech, which Stop and Interrupt leave alone, so a long repeat talked over the next question. | Repeat is the assistant's speech; Stop and Speak silence it. | `6af6487` |
| History | "Keep for: off", and turning off the questions' words, applied only to new records. What was already stored stayed, on the phone and on screen. | Both apply to what is stored when the history is next read or added to. "Off" deletes the file. | `943c304` |
| History | A failed turn was recorded with the model, and the backup flag, of the answer before it. | Who answered is taken as the answer comes back. A failure is the failing service's, with no model. | `943c304` |
| History | A stopped turn was credited to whoever had answered the turn before. | It has no service unless one had answered, and it says where it was stopped (recognition, AI or speech). | `943c304` |
| Offline | Without the phone's position, the trip tool gave each stop's distance from the start as "from_here". The assistant, and the phone's own answer, said "about 18 miles away" for a stop perhaps already passed. | It is "from_start", with "position": "unknown". The phone's sentence says it doesn't know where you are. | `35fbc66` |
| Offline | "How far is left?" without a position answered with the whole trip's length. | The answer says it is the whole trip. | `35fbc66` |
| Offline | A battery or range reading over an hour old was said as now ("The battery is at…"). | It is said as old: "The last battery reading, 3 h ago, was…". The arrival projection says how old its reading is. | `35fbc66` |
| Offline | A question the phone cannot answer by itself got only the failure ("There's no connection."). | It also says what the phone can answer without an AI service. | `35fbc66` |
| Usage | After a restart, Setup showed no usage for today until a question had been asked. | Setup loads the stored usage itself. | `1be9fb7` |
| Retry | Retry showed only while the error did, which is 6 s: too short for a driver. | Retry stays until the next turn starts, on the phone and on the car. | `48b9a0f` |
| Retry | After a recognition failure, Retry asked the previous turn's question. | A voice turn starts with nothing to retry until its words are heard. | `48b9a0f` |
| Fallback | Groq shut its default model, `llama-3.3-70b-versatile`, down on 2026-08-16. Every Groq request failed, as "That AI model isn't available." That is always so for Groq as a backup, which runs on its default. Found in the car on 2026-10-31 (§10). | The default is `openai/gpt-oss-120b`, Groq's listed replacement, which calls tools, with `reasoning_effort` "low". The PC's preset changed too. | `f675f0f` |
| Fallback | A retired or misnamed model was treated as a passing failure, and asked again on every question. | It needs attention, as a refused key does. An overloaded service still only rests. | `b42186c` |

## 8. Checked and found sound

**Speaking as the answer arrives**
- **Order:** sentences are queued on the main thread in the order they
  arrive, and the rest of the answer after them, so they cannot overtake
  each other.
- **First sentence:** spoken once it is 24 characters or more.
- **Stopping:** Stop or a barge-in aborts the request (the socket is
  closed) and drops the turn's speech. A stopped turn's late words are
  refused twice over: by the voice state machine (turn and state) and by
  the speech queue (turn).
- **An interrupted answer** cannot resume, and an old answer cannot play
  over a newer one. Each question waits for the one before it to end
  (the assistant's lock).
- **Audio focus** is let go whenever the queue empties, after a Stop too.

**Backups and limits**
- **The conversation** carries to a backup (seeded).
- **Retries:** only what cannot have been processed, once. A key, quota or
  permission problem is never retried. A read timeout is never retried, so
  no question is asked twice.
- **No backup is asked** after a service has acted or spoken.
- **A failed turn** leaves the conversation as it was, so Retry does not
  send the question twice.
- **Limits** are checked before each service is asked.

**Without internet**
- **What Setup's "Without internet" lists** matches what the phone answers
  by itself.
- **Plan figures** are said as planned, never as measured.

**History**
- **What a record holds:** times, outcome, category, stage, service and
  model. It holds no audio, no answer, no key, no position and no error
  detail. The question's words are kept only if you turned that on (off by
  default).

**Android Auto**
- **Stop** shows while anything runs. Stopping the assistant drops only its
  own speech, so guidance carries on, and an urgent cue still cuts in.

**Not changed, noted for the device tests**
- **No key at all:** without any key the car screen asks for one, so the
  phone-only answers are not offered in the car. Once a key is set, they are
  offered when the service cannot be reached.
- **Guidance while listening:** navigation guidance is not held back while
  the assistant listens. A turn instruction spoken into the microphone could
  be heard as part of the question.

## 9. Results

- **Phone unit tests:** 260, 0 failures. 14 are new; `main` had 246. They
  ran against simulated services with a fake clock.
- **Each commit on its own:** built and passed the AI tests in a temporary
  worktree with its own build directory.
- **`lintVitalRelease`:** passes.
- **Release APK:** built into `~/odb-scanner-build-rc`. A copy is
  `~/odb-scanner-tested/odb-scanner-0.2.0-release-rc-7aedf52b.apk`
  (sha256 `7aedf52b…963944`).
  - Certificate SHA-256 `d6967d0e…37feee`, the usual key.
  - Not debuggable.
  - Version 0.2.0 (200), as 20261031e, so it installs as an update.
  - Not installed.
- **No PC code changed.**
- **The device test plan:** `ai_assistant_device_tests.md`.

## 10. In the car, 2026-10-31 (20261031e on the Pixel 7)

You reported two problems: the assistant failed to chat, and the app was
not on Android Auto. The evidence came from the phone over USB, read-only:
its log, its package and permission state, and Android Auto's process
events. The app's own log lines from the drive had rotated out; the
system's event log still had the drive.

**The timeline**

| Time | What |
|---|---|
| 20:15:37 | Android Auto connected (wireless) |
| 20:15:40 | The collector started |
| 20:21:41 | The app said a 27-character sentence: "Sorry, I didn't catch that." or "I can't use the microphone." |
| 20:22:03 | "That AI model isn't available." (30 characters) |
| 20:22:20 | The same again |
| 20:22:51 | Android Auto disconnected |

Mobile data was up and validated the whole time.

**Chat: the cause**
- Groq shut down the app's default Groq model, `llama-3.3-70b-versatile`,
  on 2026-08-16. Groq's reply ("decommissioned") is `MODEL_UNAVAILABLE`,
  said as "That AI model isn't available."
- A backup always runs on its default model, so Groq in the chain failed
  every time.
- Fixed in `f675f0f` and `b42186c`.
- **Not yet confirmed:** which service you have set up. A Gemini overload
  (503) is said with the same words. The Ask AI transcript names the service
  and says either "model … is not available" or "overloaded". It is still
  in the app until the app is closed.

**Android Auto: only an app from Google Play is shown**
- **What Android offers:** the app's car service, to Android Auto (enabled,
  exported, no permission needed, category CHARGING). While connected,
  Android Auto never bound it.
- **With Unknown sources turned on** (Android Auto's own dump showed
  `allow_unknown_sources: true`), its validator still refused the app at
  every scan:
  `W CAR.VALIDATOR: isPackageInstalledByPlayCheck service not connected`,
  then `W CAR.VALIDATOR: Package DENIED; failed all other checks
  [com.odbscanner]`.
- **Why:** Google's testing guide says the Unknown sources setting covers
  media, messaging notification and parked apps, "but doesn't apply to apps
  built using the Android for Cars App Library". To test in a car, "you must
  install it from a trusted source such as Google Play". Internal App
  Sharing or an internal test track skip the Play review. Others report the
  same denial on Android Auto 17.x (the Lapin, Vela and Fermata projects).
- **What it would take:**
  - a Play developer account;
  - an internal test track;
  - a release key Play accepts. Play refuses debug-signed builds, so each
    phone installs the Play build fresh, once, after its readings are saved.
- **The owner's decision.** No code can change this. The earlier note
  here, that turning on Unknown sources would fix it, was wrong.
- **Until then:** the assistant still works on the phone's own screen in
  the car, and its speech plays through the car's speakers.

**Left open**
- The 27-character recognition failure. The log cannot say which of the two
  sentences it was. Device tests A1 and G4 cover it.
