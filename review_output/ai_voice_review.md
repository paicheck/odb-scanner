# AI and voice review — 2026-10-31

Branch `ai-voice`, from `navigation-intelligence` (`e66ccb4`), the latest
navigation work. The `navigation` branch and the APK being tested
(`tested-apk-20261031c`, sha256 `a4b5d207…`) are not touched. No release build
is made on this branch.

## 1. What there is (read before changing anything)

### Providers and capabilities

| Capability | Implementation | Where | Leaves the phone? |
|---|---|---|---|
| Speech in (STT) | Android `SpeechRecognizer` (Google's service on most phones), en-GB, one utterance | `ai/Voice.kt` `Listener` | Usually yes, to the phone's speech service (Google); on-device only if the phone supports it and is asked |
| Wake word | Vosk small English model, grammar-limited to the phrase | `ai/WakeWord.kt` | No |
| AI chat (LLM) with tools | Claude via Anthropic's Java SDK (beta Messages); Gemini, Groq, Mistral, OpenAI, OpenRouter, DeepSeek, xAI and a custom endpoint via the OpenAI-style chat completions request | `ai/ClaudeChat.kt`, `ai/Chat.kt` | Yes, to the chosen service |
| Fallback | the chosen service, then each backup in order; a turn that already acted is not retried | `ai/FallbackChat.kt` | — |
| Speech out (TTS) | Android `TextToSpeech`, en-GB, navigation-guidance audio attributes, ducking | `ai/Voice.kt` `Speaker` | Depends on the engine (Google's can use the network for some voices) |

**No cloud provider here does STT or TTS.** The LLM providers are chat only,
and speech in and out are the phone's own engines. The setup screen must
show that rather than pretend every provider offers all three.

Settings: `AiPrefs` (SharedPreferences `ai`) holds the provider, model, base
URL, effort, backups, wake word and spoken alerts. Keys go per provider in
`KeyVault`: AES-GCM under an Android Keystore key, never in plain prefs.
Models can be listed (`listModels`) for Anthropic and the OpenAI-style
services.

### The voice lifecycle

`VoiceAssistant.converse()` is one exchange:
1. pause the wake word;
2. `Speaker.stop()`, then a beep;
3. LISTENING: `Listener.listenOnce` (15 s);
4. THINKING: `Assistant.ask`, which takes a Mutex for the whole turn and
   runs the chat session (and its tools);
5. SPEAKING: `Speaker.sayAndWait(answer)`;
6. IDLE: the wake word resumes.

It is started from three places: the phone dialog, the car screen, and the
wake word service. Each launches it in its own scope and keeps no handle.
Navigation guidance, trip alerts and the Google Maps yes/no question share
`Speaker`, and the yes/no question also shares `Listener`.

## 2. Findings

### Why a failed voice answer cannot be interrupted

1. **There is no Stop.** The phone dialog's Speak is disabled unless IDLE,
   and nothing else acts on the voice. The car screen ignores Speak unless
   IDLE. The wake word pauses its own microphone for the whole exchange. A
   long answer, or a long error sentence, plays to the end.
2. **Nobody holds the job.** Each caller launches `converse` and forgets it,
   so there is nothing to cancel.
3. **The network calls cannot be cancelled.**
   - The OpenAI-style request is a blocking `HttpURLConnection` inside
     `withContext(IO)`, with a 15 s connect and 90 s read. Cancelling the
     coroutine does not interrupt it.
   - The Claude request is a blocking SDK call (90 s).
   - A turn can make up to six such requests (tool rounds), so its worst
     case is about nine minutes.
4. **Cancellation is swallowed.** `Assistant.ask` catches `Exception`, which
   includes `CancellationException`. A cancelled turn returns "Sorry,
   something went wrong (CancellationException)", is added to the
   transcript, and its caller carries on.
5. **Errors are read aloud in full.** For example, "Sorry, api.x returned
   HTTP 400: {…200 characters of JSON…}".
6. **The Mutex makes a new question wait for the old one.** `Assistant.ask`
   holds the Mutex for the whole turn.

### Errors

- **Free-text strings.** `AiException` carries only a sentence; there is no
  category. HTTP 401 and 403 are merged ("refused the API key"), 402 and
  quota errors look like any other HTTP error, and a timeout is told apart
  from a failure to connect only by its wording.
- **No reason for STT failures.** `Listener` returns null for every
  `SpeechRecognizer` error, so a missing permission, a busy recogniser, no
  network and "nothing heard" all become "I didn't catch that".
- **TTS errors are lost.** The engine failing to start drops every pending
  utterance, unlogged and unshown.
- **Raw bodies are shown and spoken.** The provider's own error body goes
  out as is. No key has been seen in one, but nothing would stop it.

### Concurrency and stale output

- `VoiceAssistant` has a single state flag. `Assistant.busy` is a second
  one, set separately, and the UI combines the two. Typed questions use
  `busy` only.
- Nothing identifies a turn, so a slow answer from an abandoned turn could
  still be spoken after a newer one (it cannot today only because there is
  no way to abandon a turn).
- `Speaker` queues every utterance into one Android TTS queue. `stop()`
  silences everything (navigation too), and there is no way to drop only
  the assistant's speech or a stale answer.
- The Google Maps yes/no question checks the state is IDLE but does not set
  it, so a Speak tapped during that question starts a second listener.

### Configuration and diagnostics

There is no way to test a provider. The only signal is a failed question:
- no connection test, no key test, no latency;
- no record of the last error;
- nothing that says which backup is answering, except a transcript line.

Providers cannot be turned off without removing them as backups, and there
is no voice choice for speech output and no on-device choice for
recognition.

### Kept as it is

- Keys in `KeyVault`.
- The fallback rule that a turn which acted is not retried.
- The append-only conversation (which this work extends with rollback of a
  cancelled turn).
- The assistant tools' privacy: no VIN, no exact position.
- The navigation audio attributes and ducking.
- The urgent navigation cue that cuts in.

## 3. Design

- **One state, one owner.**
  - `VoiceMachine` (pure, unit-tested) is the authoritative voice state:
    IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, ERROR.
    It has explicit transitions, and every turn has an id. An event for a
    turn that is not current is refused.
  - `VoiceController` owns the current turn's job and drives the machine.
    The phone, the car and the wake word all start, stop and barge in
    through it.
- **One speech queue, by owner.**
  - `SpeechQueue` (pure) decides what the TTS engine plays. Each utterance
    has an owner: the assistant's turn n, navigation, or an alert.
  - Stopping the assistant drops only the assistant's speech; navigation
    carries on. An utterance of a turn that is no longer current is never
    played.
  - An urgent navigation cue still cuts in, as now.
- **Cancellable, bounded calls.**
  - Each provider request runs through one helper. Cancelling the turn
    returns at once and aborts the HTTP connection where the transport
    allows it; a request that cannot be aborted (the SDK's) finishes in the
    background and its result is dropped.
  - Every request keeps its own timeout, and each turn has an overall
    deadline.
  - A cancelled or failed turn rolls the conversation back to before it, so
    the history stays valid and nothing of that turn comes back.
- **Structured errors.**
  - `AiError(category, provider, message, detail, status)`, using the twelve
    categories asked for. Classification is one pure function per source:
    HTTP status and provider error JSON (OpenAI, Gemini and Anthropic
    shapes), exceptions (DNS, TLS, connect, timeout), the Anthropic SDK's
    exceptions, and the `SpeechRecognizer` and `TextToSpeech` error codes.
  - Messages are redacted (keys, `Bearer …`, `x-api-key`, key-shaped
    strings) before they are shown, stored or logged.
  - What is spoken is a short phrase for the category; the detail is shown.
- **Diagnostics behind the providers' own code.**
  - The connection, key and LLM tests use the provider classes (the same
    request shapes and the same transport). Results (status, latency, last
    error, time; never a key) are stored per provider and capability.
  - STT, TTS and end-to-end tests go through the same `Listener` and
    `Speaker` the assistant uses, each with Stop.
  - Test prompts and phrases are synthetic and send no vehicle data.
- **Setup screen.** "AI & Voice Setup" groups by capability and provider and
  shows:
  - what is configured, the active provider and the backups;
  - which providers are turned off;
  - the speech-output voice;
  - "on the phone only" recognition;
  - who receives audio and text;
  - each test, with Stop.

  The existing settings dialog stays where keys and models are entered.

## 4. Results

| Finding (§2) | Now | Commit |
|---|---|---|
| No Stop; nobody held the job | VoiceAssistant owns the turn: Stop in the phone dialog, on the car screen and on the setup screen; Interrupt (Speak over it, or the wake phrase) | `3040f06` |
| Network calls could not be cancelled | one transport aborts the connection; the SDK call is walked away from at once and its reply dropped; SDK retries off | `0272003` |
| Cancellation was swallowed | CancellationException goes on up; the stopped turn is rolled out of the conversation | `0272003` |
| Errors read aloud in full | a few words for the category; the detail is shown | `0272003`, `3040f06` |
| The mutex made a new question wait | the old turn is cancelled first, so the lock is free at once | `3040f06` |
| Free-text errors; STT and TTS reasons lost | twelve categories, with a reason (dns, tls, expired, region, silence…); recogniser and TTS codes kept | `0272003` |
| Several flags; no turn ids; one TTS queue | VoiceMachine (one state, numbered turns, stale events refused); SpeechQueue (by owner and turn) | `3040f06` |
| The Google Maps question could overlap a conversation | it is a voice turn of its own | `3040f06` |
| No way to test a provider | AI & Voice Setup: connection, key, AI, speech in, speech out, whole chain; results stored without keys | `9ad14a5` |
| No off switch, voice or on-device choice | turn off or on, main and backup, voice picker, on-device recognition | `0272003`, `9ad14a5` |

The verification is in `ai_voice_handover.md`, and what needs a real phone
is listed there too.
