# AI and voice backlog — 2026-10-31

Branch `ai-voice` (from `navigation-intelligence`, `e66ccb4`). Nothing pushed;
no release build; nothing installed on a phone.

| ID | Task | Status |
|---|---|---|
| AV01 | Structured errors: AiError with the 12 categories; classification of HTTP status + provider error JSON, exceptions, Anthropic SDK errors, SpeechRecognizer and TTS codes; redaction; a short spoken phrase per category | pending |
| AV02 | Cancellable, bounded provider calls: one helper that returns at once on cancel and aborts the connection where it can; turn deadline; the cancelled turn rolled out of the conversation; CancellationException never swallowed | pending |
| AV03 | VoiceMachine: one authoritative state (IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, ERROR), explicit transitions, turn ids, stale events refused; deterministic tests for every scenario asked | pending |
| AV04 | SpeechQueue: utterances by owner; stop drops only the assistant; a stale turn's speech never plays; navigation's urgent cue still cuts in; Speaker built on it | pending |
| AV05 | VoiceController: owns the turn; Stop and barge-in on the phone, the car and the wake word; typed questions cancellable too; recogniser errors kept; backgrounding releases the microphone | pending |
| AV06 | Diagnostics: capability registry; connection, key, LLM, STT, TTS, end-to-end tests with latency and stage; results stored (no secrets); mocked transports in tests | pending |
| AV07 | AI & Voice Setup screen: by capability and provider; status, last result and error, latency, test buttons with Stop; main / backup / off; on-device recognition; speech voice; who receives what | pending |
| AV08 | Docs and handover; unit tests, lint, debug build | pending |
