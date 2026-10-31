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
    /** Each member's preset id, for its health and usage; the labels when not given. */
    private val ids: List<String> = members.map { it.first },
    /** Services that keep failing are rested (ProviderHealth); null: no breaker. */
    private val health: CircuitBreaker? = null,
    /** The conversation so far, for sessions made in the middle of it. */
    history: List<Pair<String, String>> = emptyList(),
    /** Your daily limits, checked before a service is asked; null: none. */
    private val limit: ((String, String) -> AiError?)? = null,
) : ChatSession {
    private var index = 0
    private val sessions = arrayOfNulls<ChatSession>(members.size)
    private var note: String? = null
    private val words = history.toMutableList()

    init {
        require(members.isNotEmpty()) { "no AI service to ask" }
        require(ids.size == members.size) { "an id for every service" }
    }

    override val model: String get() = sessions.getOrNull(index)?.model ?: ""

    /** The service answering now (a backup once one has had to). */
    val activeLabel: String get() = members.getOrNull(index)?.first ?: ""
    val activeId: String get() = ids.getOrNull(index) ?: ""
    val onBackup: Boolean get() = index > 0

    /** Which service answered, and why the ones before it did not; once. */
    fun takeNote(): String? = note.also { note = null }

    override fun seed(turns: List<Pair<String, String>>) {
        words += turns
    }

    override suspend fun ask(text: String, tools: ToolRunner): String {
        val failed = mutableListOf<String>()
        var last: AiException? = null
        // Rested services are skipped -- unless every one left is resting,
        // when the one whose rest ends first is tried anyway (its trial).
        val left = (index until members.size).toList()
        val allowed = left.filter { health == null || health.allow(ids[it]) }
        val order = allowed.ifEmpty {
            listOfNotNull(health?.soonest(left.map { ids[it] })?.let { ids.indexOf(it) })
        }
        for (i in left) if (i !in order) {
            val s = health?.status(ids[i])
            failed += "${members[i].first} skipped (${s?.state?.name?.lowercase() ?: "resting"}" +
                (s?.lastError?.let { ": ${it.category}" } ?: "") + ")"
        }
        for (i in order) {
            index = i
            val (label, make) = members[i]
            var acted = false
            val watched = ToolRunner { call ->
                if (changesState(call.name)) acted = true
                tools.run(call)
            }
            try {
                limit?.invoke(ids[i], label)?.let { throw AiException(it) }
                val session = sessions[i] ?: make().also {
                    // A backup (or a rebuilt session) joining mid-conversation
                    // is told what was said so far.
                    if (words.isNotEmpty()) it.seed(words.toList())
                    sessions[i] = it
                }
                val answer = session.ask(text, watched)
                health?.success(ids[i])
                words += text to answer
                note = if (failed.isEmpty()) null
                else "Answered by $label: ${failed.joinToString("; ")}."
                return answer
            } catch (e: AiException) {
                // (A CancellationException is not an AiException: a stopped
                // turn is never handed to a backup, nor counted against it.)
                e.error?.let { if (it.reason != "local_limit") health?.failure(ids[i], it) }
                if (acted) {
                    val err = e.error ?: AiError(e.category, label, e.message ?: "failed")
                    throw AiException(err.copy(message = "${err.message} -- after it had " +
                        "already acted on that, so the backup was not asked"), e)
                }
                failed += "$label failed (" + (e.error?.let { "${it.category}: " } ?: "") +
                    "${e.message})"
                last = e
            }
        }
        index = members.size - 1
        val err = last?.error
        throw AiException(AiError(err?.category ?: AiErrorCategory.UNKNOWN_ERROR, "AI services",
            "every AI service failed: ${failed.joinToString("; ")}", err?.detail ?: "",
            err?.status, err?.reason ?: ""), last)
    }
}
