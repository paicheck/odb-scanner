# AI and voice backlog — 2026-10-31

Branch `ai-voice` (from `navigation-intelligence`, `e66ccb4`). Nothing pushed;
no release build; nothing installed on a phone.

| ID | Task | Status |
|---|---|---|
| AV01 | Structured errors: AiError with the 12 categories; classification of HTTP status + provider error JSON, exceptions, Anthropic SDK errors, SpeechRecognizer and TTS codes; redaction; a short spoken phrase per category | done `0272003` |
| AV02 | Cancellable, bounded provider calls: one helper that returns at once on cancel and aborts the connection where it can; turn deadline; the cancelled turn rolled out of the conversation; CancellationException never swallowed | done `0272003` |
| AV03 | VoiceMachine: one authoritative state (IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, ERROR), explicit transitions, turn ids, stale events refused; deterministic tests for every scenario asked | done `3040f06` |
| AV04 | SpeechQueue: utterances by owner; stop drops only the assistant; a stale turn's speech never plays; navigation's urgent cue still cuts in; Speaker built on it | done `3040f06` |
| AV05 | VoiceController: owns the turn; Stop and barge-in on the phone, the car and the wake word; typed questions cancellable too; recogniser errors kept; backgrounding releases the microphone | done `3040f06` |
| AV06 | Diagnostics: capability registry; connection, key, LLM, STT, TTS, end-to-end tests with latency and stage; results stored (no secrets); mocked transports in tests | done `9ad14a5` |
| AV07 | AI & Voice Setup screen: by capability and provider; status, last result and error, latency, test buttons with Stop; main / backup / off; on-device recognition; speech voice; who receives what | done `9ad14a5` |
| AV08 | Docs and handover; unit tests, lint, debug build | done |
| AV09 | Real-device checks: Stop/Interrupt on phone and Android Auto, wake phrase over speech, on-device recognition, voice picker, backgrounding, real rejections | owner |
| AV10 | Free-speech barge-in (any speech over the assistant, not only the wake phrase) | not started: needs echo-cancelled recognition the phone recogniser does not offer |
