package com.odbscanner.ai

/**
 * The chosen AI service, then each backup in turn when one fails (Assistant
 * settings → Backups). A free tier's rate limit, an outage, a refused key or
 * a missing one all arrive as [AiException]; the same words then go to the
 * next service.
 *
 * Once a backup has answered, the rest of the conversation stays with it: a
 * rate-limited service would otherwise be waited on, and fail, before every
 * answer. A new conversation starts with the chosen service again.
 *
 * A failed turn is handed on only if it changed nothing. If the failing
 * service had already run a tool that acts -- planned a trip, started
 * navigation ([changesState]) -- asking a backup could do it twice, so the
 * failure is reported instead, saying the action was taken.
 *
 * Sessions are made lazily by [members] (label, factory): a backup costs
 * nothing until it is needed, and one that cannot be made -- no key, no
 * model -- counts as one more failure, not a crash.
 */
class FallbackChatSession(
    private val members: List<Pair<String, () -> ChatSession>>,
    private val changesState: (String) -> Boolean,
) : ChatSession {
    private var index = 0
    private val sessions = arrayOfNulls<ChatSession>(members.size)
    private var note: String? = null

    init {
        require(members.isNotEmpty()) { "no AI service to ask" }
    }

    override val model: String get() = sessions.getOrNull(index)?.model ?: ""

    /** Which service answered, and why the ones before it did not; once. */
    fun takeNote(): String? = note.also { note = null }

    override suspend fun ask(text: String, tools: ToolRunner): String {
        val failed = mutableListOf<String>()
        while (index < members.size) {
            val (label, make) = members[index]
            var acted = false
            val watched = ToolRunner { call ->
                if (changesState(call.name)) acted = true
                tools.run(call)
            }
            try {
                val session = sessions[index] ?: make().also { sessions[index] = it }
                val answer = session.ask(text, watched)
                note = if (failed.isEmpty()) null
                else "Answered by $label: ${failed.joinToString("; ")}."
                return answer
            } catch (e: AiException) {
                if (acted) {
                    throw AiException("${e.message} -- after it had already acted on " +
                        "that, so the backup was not asked", e)
                }
                failed += "$label failed (${e.message})"
                index++
            }
        }
        throw AiException("every AI service failed: ${failed.joinToString("; ")}")
    }
}
