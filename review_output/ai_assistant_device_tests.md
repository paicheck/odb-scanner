# AI assistant — Pixel 7 device tests, release candidate

Branch `ai-assistant-rc` at `b42186c`, on `main` (`9fc7a2f`). What was fixed
for this candidate, and why: `ai_assistant_review.md` §6–8 and §10.

## The build

| | |
|---|---|
| Candidate | `~/odb-scanner-tested/odb-scanner-0.2.0-release-rc-7aedf52b.apk`, sha256 `7aedf52b…963944` |
| Kind | release, not debuggable, signed with the usual key (`d6967d0e…37feee`) |
| Installs as | an update over 20261031e: the app's data, keys and settings stay |
| Under test now | 20261031e, `7f94cf57…`, untouched in `~/odb-scanner-build/app/outputs/apk/release/` and `~/odb-scanner-tested/odb-scanner-0.2.0-release-20261031e.apk`. Installing it again goes back. |
| Built with | `./gradlew -PodbBuildDir=$HOME/odb-scanner-build-rc :app:assembleRelease`, which builds into a directory of its own and never over the APK being tested |

**Nothing is on the phone yet.** Copying the candidate to its Download folder
waits for your OK.

## Before you start

1. **Android Auto.** The app is not from the Play Store, so Android Auto
   shows it only with **Unknown sources** on:
   - On the phone: Android Auto settings → tap **Version** ten times →
     Developer settings → **Unknown sources** on.
   - Then Android Auto settings → **Customise launcher**: make sure
     ODB Scanner is ticked.
   - That is the likely reason the app was missing in the car on 2026-10-31.
2. **Services.** In Ask AI → Setup & tests, set up two services with keys,
   for example Groq as main and Gemini as backup. Run each one's **Key** and
   **AI test**; both should pass.
3. **Answers.** Leave "Speak as it arrives" on.
4. **History.** Set it to keep 7 days, and turn "keep the questions' words"
   **on** for this session, so the records show what was asked. E2 turns it
   off again.
5. **Trip.** Plan a trip of about an hour in Route. Start navigation for the
   car tests.
6. **Times.** Note the phone's time when you start each group: the history
   shows times.

## How to report a failure

For each failure, send me:

1. **The test** (for example B3) and the time on the phone.
2. **What happened:**
   - what you did;
   - what the table says should happen;
   - what you heard, and what the screen said. A photo of the screen is
     fine: no screen shows a key.
3. **Setup:** a screenshot of Setup → History → the last ten turns, and each
   service's **Health** line.
4. **Context:** whether navigation was running, and whether the phone was
   on Android Auto.
5. **If it happens again:** leave the phone plugged into the PC and say so.
   With your go-ahead I read its log (`adb logcat -d`). That only reads: it
   installs and runs nothing.

Do not delete the history or the app's data before you report.

## A. Speaking as the answer arrives (Groq main)

| ID | Do | Expected |
|---|---|---|
| A1 | Ask: "Tell me about my battery and my trip." | Speech starts with the first sentence, a second or two after you finish. The sentences come in order, with no long gaps and none twice. In History, "first words" is well under the whole answer's time. |
| A2 | Ask A1 again, and press **Stop** while it speaks. | Silent within about half a second; the screen shows "Stopped", then idle. Nothing more is said, even a minute later. Music comes back up. |
| A3 | Ask A1 again; mid-answer tap **Interrupt** (or say the wake phrase), and ask "What's my range?" | The old answer stops, there is a beep, and it listens. Only the new answer is spoken; nothing of the old one follows it. |
| A4 | Ask, and press **Stop** while it shows "Thinking…". | Nothing is spoken afterwards. History: stopped, stage AI. |
| A5 | Turn "Speak as it arrives" off, and repeat A1. | The answer is spoken whole, once it has all arrived (a longer wait). |

## B. Backups and health

| ID | Do | Expected |
|---|---|---|
| B0 | With a Groq key, and no model typed for it: in Setup, run Groq's **Key** and **AI test**. | The Key test lists `openai/gpt-oss-120b`, and the AI test answers. The old default, `llama-3.3-70b-versatile`, was shut down by Groq; that was the in-car failure on 2026-10-31. If you typed a model for Groq in Settings, clear it. |
| B1 | In Settings, give the main service a wrong key (`xxx`), then ask something. | The backup answers. A line says "(Answered by Gemini: Groq failed (INVALID_CREDENTIALS…))". Groq's Health in Setup: "needs attention: INVALID_CREDENTIALS". |
| B2 | Ask a follow-up: "And how much will I have there?" | The backup answers knowing what "there" was. |
| B3 | Tap **New chat** and ask again. | Groq is skipped with no wait: "(… Groq skipped (needs_attention…))". |
| B4 | Give the backup a wrong key too; New chat; ask "Tell me a joke". | An answer at once, with no request sent: "The AI service refused the key. Without an AI service I can only tell you the battery…". "How much battery have I got?" is answered from the phone instead. |
| B5 | With a service still needing attention, change only the speech speed in Setup. | Its Health still says "needs attention" (it used to reset). |
| B6 | Put both right keys back. | That service's Health is "in use" again at once. |
| B7 | Turn airplane mode on. Ask "Tell me a joke" three times. | Each time: "There's no connection." and the offline sentence. After three, Health says "resting … until hh:mm". Turn airplane mode off; after that time, ask again: it answers, and Health is "in use". |

## C. Limits and usage

| ID | Do | Expected |
|---|---|---|
| C1 | Set requests a day to today's count + 2 (Setup shows today's count). Ask three questions. | A warning on the screen at 80 %. The third question goes to the backup, or says the daily limit is reached. Set the limit back to 0 (no limit) afterwards. |
| C2 | Swipe the app away, open it again, and go straight to Setup. | Today's requests and tokens are shown before any question is asked (they used to show nothing). |

## D. Without internet (airplane mode, a trip planned, today's readings from the car)

| ID | Do | Expected |
|---|---|---|
| D1 | "How much battery have I got?" | A recent reading: "The battery is at N per cent (read X min ago)." One over an hour old: "The last battery reading, 3 h ago, was N per cent." An old reading is never said with "is at". The screen adds "(answered from the phone's own data …)". |
| D2 | "What's my range?" | The same pattern, with the range the car shows. |
| D3 | "Where's my next charging stop?", with Location on and navigating. | "Your next stop is X, about N miles away. Planned arrival charge is P per cent." |
| D4 | The same with Location off. | "The first charging stop on the plan is X, about N miles from the start. … The phone doesn't know where you are on the route right now." Never "away". |
| D5 | "How far is left?", Location off. | "The whole trip to … is about N miles. The phone doesn't know where you are…" |
| D6 | "Will I make it?" | "At the rate you're using, about P per cent at X; the plan said Q, from the battery reading N min ago." Before the trip watch has a reading, it gives the plan's figure instead. |
| D7 | "Tell me a joke." | "There's no connection. Without an AI service I can only tell you the battery, the range, the next charging stop, the arrival charge, the trip left and fault codes." |
| D8 | "Any fault codes?" | The number stored, or "No fault codes are stored." |

## E. History

| ID | Do | Expected |
|---|---|---|
| E1 | Setup → History, after A–D. | One record per question. A stopped one shows "stopped" and where (recognition, AI or speech), with no service unless one had answered. A failure names the failing service, with no model. A backup's answer is marked as a backup. |
| E2 | Turn "keep the questions' words" off, then tap Refresh. | No record shows a question, older ones included. |
| E3 | Set "Keep for" to off, tap Refresh, then ask a question. | The history is empty, and stays empty. Set it back to 30 days. |
| E4 | Tap Delete history, then ask one question. | Only the new question's record is there. |

## F. A speech engine that stops answering

| ID | Do | Expected |
|---|---|---|
| F1 | During a long answer, force-stop the speech engine: Settings → Apps → Speech Services by Google → Force stop. | The answer stops. Then ask again: the answer is spoken, by a fresh engine, and navigation guidance still speaks. If the engine never reported the stop, recovery takes up to about a minute plus the answer's length; before this fix it never came. |

## G. Android Auto (in the car, navigating)

| ID | Do | Expected |
|---|---|---|
| G1 | Assistant → **Speak**; ask A1; tap **Stop** during the answer. | The assistant stops at once. Navigation guidance carries on, and the next instruction is spoken. The screen shows "Stopped", then Speak. |
| G2 | Ask a long question just before a turn. | The turn instruction cuts in. The rest of the answer is not said after it. |
| G3 | Phone in airplane mode: ask "Tell me a joke". Wait 10 s after the error, then tap **Retry**. | Retry is still there after the error has gone from the screen. It asks the same question again, without listening. |
| G4 | Tap Speak and say nothing after the beep. | "Sorry, I didn't catch that." No Retry is offered (Speak instead). Retry must never ask an earlier question. |
| G5 | After an answer, tap **Repeat**, then **Speak** while it is repeating. | The repeat stops at once and the assistant listens. It used to keep talking over you. |
| G6 | With music playing in the car, ask, then Stop; ask again and let it finish. | The music ducks during each answer and comes back afterwards, after a Stop too. |
| G7 | Have someone ring the phone during an answer. | The call takes over. After it, the next answer is heard normally. |

## What only the phone or the car can show

None of these can be shown by unit tests. No instrumented tests were run on
your phones.

- **Speech timing:** the engine's real timing, and the gaps between
  sentences (A1, A3).
- **Audio:** audio focus with music and calls (G6, G7), and Bluetooth
  routing.
- **Failures in use:** a service's real refusals (B), losing the network
  mid-answer (B7), and an engine that dies (F1).
- **The car:** Android Auto's own screens and button limits (G).
