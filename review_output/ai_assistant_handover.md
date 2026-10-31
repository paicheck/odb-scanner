# AI assistant handover — 2026-10-31

## Release candidate (latest)

Branch `ai-assistant-rc`, from `main` at `9fc7a2f`. **Not pushed, not
merged, not installed.**

The work being tested is untouched:
- `navigation` (`eb646d3`), `ai-voice` (`7a53162`) and `ai-assistant`
  (`f3fc46e`) are unchanged.
- The APK on the Pixel, 20261031e (`7f94cf57…`), is unchanged, in
  `~/odb-scanner-build/app/outputs/apk/release/` and `~/odb-scanner-tested/`.

The candidate was built beside it with `-PodbBuildDir=$HOME/odb-scanner-build-rc`:
`~/odb-scanner-tested/odb-scanner-0.2.0-release-rc-9cab53b2.apk`
(sha256 `9cab53b2…8e1270`). It is a release build, not debuggable, signed with the
usual key `d6967d0e…37feee`.

| Commit | What |
|---|---|
| `d11567e` | `-PodbBuildDir`: build a candidate without touching the tested APK |
| `9fb4efd` | Backups: a stopped trial is let go; a refused key is not asked again; health is reset only for what changed |
| `6af6487` | Speech: an engine that stops answering is given up on; utterance ids from any thread; Repeat can be stopped |
| `943c304` | History: settings apply to what is stored; each turn is credited as it went |
| `35fbc66` | Offline: old readings said as old; no "from here" without a position; an honest reply to anything else |
| `1be9fb7` | Setup: today's usage shown before the first question |
| `48b9a0f` | Retry stays until the next turn, never the question before |
| the next one | Review §6–9, the device test plan, this handover |

There are 17 defects in all. Each one, why it mattered, and what was found
sound: `ai_assistant_review.md` §7–8.

**Verified:**
- 259 phone unit tests, 0 failures (13 new);
- each commit built and tested on its own;
- `lintVitalRelease` passes;
- the certificate checked.

No PC code changed. No vehicle, adapter or database was involved.

**To do next:**
1. Your OK to copy the candidate to the Pixel's Download folder.
2. Run `ai_assistant_device_tests.md`: sections A–G, with how to report a
   failure. The Android Auto part needs the car.
3. Then push and merge, if you want it, after the device tests.

## The assistant as built (`ai-assistant`, now on `main`)

Branch `ai-assistant`, on `ai-voice` (`7a53162`). It was merged into `main`
and pushed after this was first written. Your roadmap message was cut off
in section 6, after "Never stop navigation guidance when stopping the AI.";
the work covers sections 0–6 as far as they go.

| Commit | What |
|---|---|
| `7a37a6c` | Investigation and the architectural problem, before any change (`ai_assistant_review.md` §1–2) |
| `f8de645` | Service health and safe retries; context carried across services; usage and limits; speech fallback; answer style, speech speed, language |
| `64da35e` | Answers spoken as they arrive; interaction history; the app's live check; charging history; answers without an AI service |
| `4258973` | A flaky test fixed (the code was right) |
| `df021d3` | Setup: health, usage, limits, history and answer settings; a big Stop; Retry on the phone and the car |

### The architectural problem, and its fix

Each AI service kept its own copy of the conversation. A backup taking over
mid-conversation, or a session rebuilt after a settings change, started with
no history, so "How much battery will I have *there*?" broke exactly when a
fallback happened.

The fix:
- The conversation's words are kept apart from any one service.
- Each new session is seeded with them, while each service's own history
  stays append-only, as the services need.

A new trip, New chat, 10 minutes idle or 16 questions starts afresh, and
memory can be turned off.

### How it behaves now

- **When a service fails:**
  1. The failure is classified (as before).
  2. It is retried only if the request cannot have reached the model: a
     connection that never opened, a rate limit, an overload. That happens
     once, after the wait the service asks for (up to 5 s). It is never
     retried after a read timeout, so no question is asked twice, and never
     for a key, quota, permission or model problem.
  3. Otherwise the next service is asked, seeded with the conversation.
  4. The screen says who answered; the history records it.

  A service that fails three times running is rested for 60 s, then 2, 4…
  up to 10 minutes, with one trial request each time. A refused key or a
  used-up quota is marked "needs attention" and not asked again until you
  change the key or settings. Health is shown per service in Setup.
- **Streaming:** the OpenAI-style services (Gemini, Groq, Mistral, OpenAI,
  OpenRouter, DeepSeek, xAI, a custom service or Ollama) are spoken sentence
  by sentence as the answer arrives. Stop or a barge-in drops the rest, and
  an interrupted answer cannot resume. Claude answers whole (review §4).
  Streaming can be turned off in Setup → Answers.
- **Speech fallback:**
  - Online recognition that cannot connect → "Say that again", on the phone.
  - On-the-phone recognition that can't → online, only if you allow it.
  - A chosen voice that needs the network → the phone's own voice, said on
    screen.
- **Without any AI service** (no connection, none set up, out of quota,
  refused key): questions about the battery, range, next stop, arrival
  charge, trip left and fault codes are answered from the phone's own data,
  in fixed sentences, marked as such.
- **"Will I make it?"** The trip tool now carries the app's own live check:
  the battery now against the plan, and the projected arrival at the next
  stop, with the reading's age. The assistant is told to explain the app's
  figures and never to work out its own.
- **In the car:** while anything runs, Stop is the biggest control on the
  phone (full width, red). After a failure Retry asks the same question
  again, on the phone and on the car screen. Stopping the assistant never
  stops navigation guidance (the speech queue drops only the assistant's
  words).

### Setting it up

AI & Voice Setup (Ask AI → Setup & tests) now also has:

| Section | Settings |
|---|---|
| Answers | How long (auto = brief while driving / brief / normal / detailed), remember the conversation, speak as it arrives, speech speed, en-GB or en-US |
| Speech recognition | On the phone only; allow the online service as a fallback (your consent) |
| Each AI service | Health, today's requests and tokens, your price per million tokens (for a cost estimate; never assumed), tests as before |
| Limits | Requests a day and spending a day, across every service. A reached limit moves to a backup; 80 % gives a warning |
| History | Keep for off / 7 / 30 / 90 days; keep the questions' words (off by default); delete; statistics by service and by capability (STT, LLM, TTS); the last ten turns |
| Without internet | What still works and what does not |

**Ollama on your PC:** run `tailscale serve --bg --https=443 http://localhost:11434`
on the PC. Then on the phone choose the custom service, with the address
`https://<pc>.<tailnet>.ts.net/v1` and a model such as `llama3.2:3b`. Plain
`http://` is refused, because the app allows no cleartext.

### Verified

- **Phone unit tests:** 242, 0 failures (23 new), against simulated services
  with a fake clock. They cover:
  - the breaker's rest, trial, recovery and attention;
  - the retry rules;
  - Retry-After honoured;
  - a stopped wait never retried;
  - token usage recorded;
  - a backup seeded with the conversation;
  - resting services skipped;
  - limits and cost;
  - speech fallback directions;
  - answer style;
  - streaming events and tool calls in pieces;
  - sentence splitting;
  - a non-streaming service asked normally;
  - no backup over spoken words;
  - history statistics and retention;
  - phone-only answers.
- **`lintVitalRelease`:** passes. This is CI's lint gate, run without
  building an APK.

### Needs the phone, and the car

1. **Streaming:** speech starts on the first sentence, has no gaps, and
   stops at once.
2. **"Say that again":** test it with mobile data off, in a car park.
3. **Health:** a wrong key shows "needs attention", and a dead service rests
   and comes back.
4. **Limits:** set requests a day to 3 and ask a fourth question.
5. **Phone-only answers:** use airplane mode with a trip planned.
6. **Retry and the big Stop,** on the phone and in Android Auto.
7. **History timings:** check they look right.
8. **Ollama through Tailscale HTTPS**, if you want it.

### Not done, and why

- **Barge-in without the wake phrase.** It needs the microphone open during
  speech with echo cancellation good enough that the assistant's own voice
  does not trigger it. Through a car's Bluetooth that is not in the phone's
  hands. It would have to be measured on the Pixel and in the car first.
  The wake phrase and a tap remain the barge-in (review §4).
- **Claude streaming.** The SDK streams, but rebuilding the reply with its
  thinking blocks, as the history requires, is not done.
- **"My last journey" on the phone.** It needs the PC's drive splitting
  ported with shared vectors. The PC's Trips page has it.
- **A model on the phone.** Not on the Pixel 7 without a 1–4 GB download
  and a native library; to be measured first.
