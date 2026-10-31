# AI and voice handover — 2026-10-31

Branch `ai-voice`, on `navigation-intelligence` (`e66ccb4`). **Nothing has been
pushed, merged, released or installed on a phone.**

The tested version is untouched:
- the `navigation` branch is still at `eb646d3`;
- the APK on the Pixel (`a4b5d207…`) is unchanged, since only unit tests,
  lint and a *debug* build ran here;
- a copy of it is in `~/odb-scanner-tested/`.

No vehicle, adapter or database was involved.

| Commit | What |
|---|---|
| `3002747` | Findings report, before any change (`ai_voice_review.md`) |
| `0272003` | Failures in twelve categories; requests that stop when asked |
| `3040f06` | One voice state machine; Stop and barge-in everywhere; speech by owner and turn |
| `9ad14a5` | AI & Voice Setup screen with a test for each capability |

## Providers and what they do

| Provider | STT | LLM | TTS | Receives |
|---|---|---|---|---|
| The phone's speech recogniser (Google's on most phones) | ✓ | | | your voice, while you ask; stays on the phone if "Recognise on the phone only" is on and the phone can |
| The phone's text-to-speech | | | ✓ | the answer's text (online voices use the network) |
| Vosk wake word | (the phrase only) | | | nothing leaves the phone |
| Claude (Anthropic), Gemini, Groq, Mistral, OpenAI, OpenRouter, DeepSeek, xAI, a custom OpenAI-compatible service | | ✓ (chat with tools) | | your question and the app data the assistant looks up; never the VIN or your exact position |

No cloud service does speech here, and the screen says so.

## Configuring

- **Keys and models:** Ask AI → **Settings**, as before. Each key is stored
  encrypted (Android Keystore).
- **Everything else:** Ask AI → **Setup & tests**:
  - **Make main**, **Use as backup** / **Not a backup**, **Turn off** /
    **Turn on**, per service. A service that is turned off is never asked
    but keeps its key. The "Asked in order" line shows the chain, and
    "Answered last" shows which one answered, including "(backup)".
  - **Recognise on the phone only:** off by default, as before.
  - **Choose voice** for speech output. The list can be empty the first
    time while the engine starts; tap again.

## Running the tests

Each test runs as its own voice turn: it cannot overlap a conversation, and
**Stop** ends it at once. Results stay on the phone with no key in them.

| Test | What it does | What it shows |
|---|---|---|
| Connection | a request without a key | reached / DNS / TLS / connect / timeout; any HTTP reply counts as reached |
| Key | lists the models with your key | accepted, or refused / expired / not permitted / quota; whether the chosen model is listed |
| AI test | one fixed sentence ("Reply with the single word: ready"), no tools | the answer, model, latency, tokens |
| Speech recognition | say a short sentence | the words, the time, or why not (permission, busy, network, nothing heard, language pack missing) |
| Speech output | speaks a test phrase | time to start, total; Stop silences it |
| Whole voice chain | microphone → recognition → the main AI (no tools, your test words) → speech | which stage failed, and the ms for each |

## Diagnosing a rejection

| Category | Usually | Do |
|---|---|---|
| NOT_CONFIGURED | no key, no model, no address, or every service turned off | add it in Settings; turn one on |
| INVALID_CREDENTIALS | wrong or revoked key; "has expired" if the service says so | make a new key at the service's site |
| PERMISSION_DENIED | the key may not use that model, or the service is not offered here ("region") | choose another model or service |
| QUOTA_EXCEEDED | credit or free allowance used up, or a daily limit (Groq TPD/RPD, Gemini RESOURCE_EXHAUSTED) | wait for the reset, add credit, or use a backup |
| RATE_LIMITED | too many requests a minute | try again shortly; a backup takes over meanwhile |
| MODEL_UNAVAILABLE | the model name is wrong or retired, or the service is overloaded (503/529) | Key test lists the models; pick one |
| NETWORK_ERROR | `dns`: no internet or the name cannot be looked up; `tls`: the phone's clock, a Wi-Fi sign-in page or a proxy; `connect`: refused | check the connection; Connection test says which |
| TIMEOUT | `connect`: the phone's connection; `read`: the service thinking too long; or the 120 s limit on a question | try a quicker service (Groq) |
| UNSUPPORTED_CAPABILITY | the model cannot call tools, or the recogniser has no British English | choose another model |
| INVALID_RESPONSE | a reply that cannot be read, or no answer | try again; report if it repeats |
| AUDIO_ERROR | the microphone could not record, nothing was heard, the recogniser is busy, or speech could not play | close other apps using the microphone; check the volume |
| UNKNOWN_ERROR | anything else (HTTP 500…), with the service's own words | see the detail |

The screen shows the category, a sentence and the service's own words.
The assistant says only a few words, such as "The AI service refused the
key."

## Stopping and interrupting

- **Stop** (phone dialog, car screen, setup screen) cancels the turn:
  - the recogniser is destroyed and the microphone freed;
  - the AI request is aborted. For the OpenAI-style services the socket is
    closed. Claude's SDK call cannot be aborted, so the turn returns at once
    and its late reply is dropped;
  - the assistant's speech is dropped, playing or queued, while navigation
    guidance carries on.

  The state goes to INTERRUPTED, then IDLE. Pressing Stop again does
  nothing more.
- **Interrupt**: tap Speak while it thinks or talks, on the phone or the
  car, or say the wake phrase. The old turn stops in the same way, and a new
  one listens at once.
- **Failure:** the state goes to ERROR, a few words are spoken, and the
  detail is shown. The state returns to IDLE after 6 s, or at once if you
  press Speak.
- **Stale answers:** every turn has a number. An answer, recognition result
  or speech from a turn that was stopped or replaced is refused and never
  plays.
- **Timeouts:**
  - connect 15 s; read 90 s for a question, 20–30 s for a test;
  - 120 s for a whole question;
  - 15 s of listening;
  - speech bounded by its length.
- **Leaving the app:** a phone question that is still listening lets go of
  the microphone. An answer being worked out or spoken carries on, and the
  car and the wake word keep listening.

## Verified

- **Phone unit tests:** 219, 0 failures (39 new), mocked services, no
  network. The new tests cover:
  - every voice scenario asked for (`VoiceMachineTest`, 13);
  - the speech queue (7);
  - stopping an in-flight request, rollback, and no stop handed to a backup
    (`CancelTest`, 7);
  - error classification and redaction (7);
  - the diagnostics (5).

  Each commit was also built and tested on its own in a temporary
  worktree.
- **`lintVitalRelease`:** passes. This is the release lint gate that CI
  runs, run here without building a release.
- **Full `lintDebug`:** 24 warnings and 4 "errors". The 4 are lint crashing
  in a Compose check that cannot read the Kotlin 2.1 metadata (a
  tool-version bug). One of the four files, `TripService.kt`, is not
  touched by this work.
- **Debug APK:** built (`~/odb-scanner-build/app/outputs/apk/debug/`), not
  installed.

## Needs a real phone

1. **Stop and Interrupt while speaking**, on the phone and on Android Auto
   (the car screen's Stop and Interrupt actions).
2. **The wake phrase said over the assistant's own voice.** Echo
   cancellation is turned on where the phone offers it, and how well it
   works depends on the phone and the volume.
3. **On-device recognition** (`EXTRA_PREFER_OFFLINE`) on the Pixel 7 and
   the HONOR.
4. **The voice picker** with the phone's engine.
5. **Leaving the app while it listens:** go to the home screen mid-question.
6. **Each test against real services:** a wrong key, an expired key and a
   used-up free tier should each show their category.
7. **Navigation guidance during a stopped answer:** guidance should
   continue, and an urgent cue should still cut in.

## Remaining limitations

- **Claude can't be aborted mid-request.** Its SDK call keeps running in the
  background until its own timeout, though nothing of it is used.
- **Barge-in is by tap or by the wake phrase.** There is no free-speech
  barge-in: listening for any speech over the assistant's own voice would
  need echo-cancelled recognition the phone's recogniser does not offer.
- **The end-to-end test asks the main service without tools.** It checks
  the chain, not the assistant's tools.
- **Lint warns about a static field.** It flags `Speaker` and
  `VoiceAssistant` for holding a context in a static field (`StaticFieldLeak`).
  It is the application context, as `Navigator` already does.
- **Not part of this branch:** the HONOR X8a still runs 20261029c. Delivery
  to either phone waits for your approval.
