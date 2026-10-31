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

    override suspend fun ask(text: String, tools: ToolRunner): String = ask(text, tools, null)

    override suspend fun ask(text: String, tools: ToolRunner, onText: ((String) -> Unit)?): String {
        val failed = mutableListOf<String>()
        var last: AiException? = null

        /** Ask member [i]: its answer, or null when it failed (recorded) and the next may try. */
        suspend fun attempt(i: Int): String? {
            index = i
            val (label, make) = members[i]
            var acted = false
            var spoke = false
            val watched = ToolRunner { call ->
                if (changesState(call.name)) acted = true
                tools.run(call)
            }
            val heard = onText?.let { f -> { words: String -> spoke = true; f(words) } }
            try {
                limit?.invoke(ids[i], label)?.let { throw AiException(it) }
                val session = sessions[i] ?: make().also {
                    // A backup (or a rebuilt session) joining mid-conversation
                    // is told what was said so far.
                    if (words.isNotEmpty()) it.seed(words.toList())
                    sessions[i] = it
                }
                val answer = session.ask(text, watched, heard)
                health?.success(ids[i])
                words += text to answer
                note = if (failed.isEmpty()) null
                else "Answered by $label: ${failed.joinToString("; ")}."
                return answer
            } catch (e: kotlinx.coroutines.CancellationException) {
                // Stopped: neither a success nor a failure, and never handed
                // to a backup -- but a trial it was on must be let go.
                health?.release(ids[i])
                throw e
            } catch (e: AiException) {
                val err = e.error
                if (err != null && err.reason != "local_limit") health?.failure(ids[i], err)
                else health?.release(ids[i])
                if (acted) {
                    val a = err ?: AiError(e.category, label, e.message ?: "failed")
                    throw AiException(a.copy(message = "${a.message} -- after it had " +
                        "already acted on that, so the backup was not asked"), e)
                }
                // Part of its answer was already spoken: a second answer from a
                // backup would follow it, so the failure is reported instead.
                if (spoke) throw e
                failed += "$label failed (" + (err?.let { "${it.category}: " } ?: "") +
                    "${e.message})"
                last = e
                return null
            } catch (e: Throwable) {
                health?.release(ids[i])
                throw e
            }
        }

        // Each service in turn, asking the breaker only just before trying it
        // (asking reserves a trial): a resting one is skipped.
        val left = (index until members.size).toList()
        var tried = false
        for (i in left) {
            if (health != null && !health.allow(ids[i])) {
                val st = health.status(ids[i])
                failed += "${members[i].first} skipped (${st.state.name.lowercase()}" +
                    (st.lastError?.let { ": ${it.category}" } ?: "") + ")"
                continue
            }
            tried = true
            attempt(i)?.let { return it }
        }
        // Every one left is resting: the one whose rest ends first is tried
        // anyway. One that needs attention (a refused key, a spent quota) is
        // not: asking again would be refused again -- say so instead.
        if (!tried && health != null) {
            val resting = left.filter { health.status(ids[it]).state == Health.RESTING }
            val pick = health.soonest(resting.map { ids[it] })?.let { ids.indexOf(it) }
            val stuck = left.map { health.status(ids[it]) }
                .firstOrNull { it.state == Health.NEEDS_ATTENTION }?.lastError
            if (pick != null && pick >= 0) {
                attempt(pick)?.let { return it }
            } else if (stuck != null) {
                index = members.size - 1
                throw AiException(stuck.copy(message = "${stuck.message} -- not asked again " +
                    "for now: check its key, model or quota in AI & Voice Setup"))
            }
        }
        index = members.size - 1
        val err = last?.error
        throw AiException(AiError(err?.category ?: AiErrorCategory.UNKNOWN_ERROR, "AI services",
            "every AI service failed: ${failed.joinToString("; ")}", err?.detail ?: "",
            err?.status, err?.reason ?: ""), last)
    }
}
