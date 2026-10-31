package com.odbscanner.ai

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * One voice or typed turn, as it went: when, which service and model, how
 * long each part took, how it ended. Structured, so it can be counted, not
 * a line of log text. No audio, no key; the question's words only if you
 * turned that on, never the answer.
 */
data class Interaction(
    val atMs: Long,
    val source: String,                  // VoiceSource
    val provider: String = "",           // who answered (or failed last)
    val model: String = "",
    val sttMs: Long? = null,             // recognition
    val firstWordMs: Long? = null,       // from the question to the first words of the answer
    val aiMs: Long? = null,              // the whole answer
    val ttsMs: Long? = null,             // speaking it
    val outcome: String = OUTCOME_ANSWERED,
    val category: String? = null,        // AiErrorCategory, when it failed
    val stage: String? = null,           // recognition | AI | speech
    val interrupted: Boolean = false,
    val fallback: Boolean = false,       // a backup answered
    val question: String? = null,
) {
    companion object {
        const val OUTCOME_ANSWERED = "answered"
        const val OUTCOME_FAILED = "failed"
        const val OUTCOME_STOPPED = "stopped"

        fun encode(i: Interaction): String = JSONObject().put("at", i.atMs).put("src", i.source)
            .put("p", i.provider).put("m", i.model).put("stt", i.sttMs ?: JSONObject.NULL)
            .put("first", i.firstWordMs ?: JSONObject.NULL).put("ai", i.aiMs ?: JSONObject.NULL)
            .put("tts", i.ttsMs ?: JSONObject.NULL).put("out", i.outcome)
            .put("cat", i.category ?: JSONObject.NULL).put("stage", i.stage ?: JSONObject.NULL)
            .put("int", i.interrupted).put("fb", i.fallback)
            .put("q", i.question ?: JSONObject.NULL).toString()

        fun decode(line: String): Interaction? = runCatching {
            val o = JSONObject(line)
            fun l(k: String) = if (o.isNull(k)) null else o.getLong(k)
            fun s(k: String) = if (o.isNull(k)) null else o.getString(k)
            Interaction(o.getLong("at"), o.getString("src"), o.optString("p"), o.optString("m"),
                l("stt"), l("first"), l("ai"), l("tts"), o.optString("out", OUTCOME_ANSWERED),
                s("cat"), s("stage"), o.optBoolean("int"), o.optBoolean("fb"), s("q"))
        }.getOrNull()
    }
}

/**
 * One turn as it goes, becoming its [Interaction]: filled in by the voice
 * assistant at each stage, with who answered taken as the answer came back.
 */
class TurnTrace(val source: VoiceSource) {
    val atMs = System.currentTimeMillis()
    var sttMs: Long? = null
    var firstWordMs: Long? = null
    var aiMs: Long? = null
    var ttsMs: Long? = null
    var outcome = Interaction.OUTCOME_ANSWERED
    var category: String? = null
    var stage: String? = null
    var interrupted = false
    var question: String? = null
    // Who answered (or failed), as it happened: never the previous turn's.
    var provider = ""
    var model = ""
    var fallback = false

    /** [active]: Assistant.active as the answer came back ("Groq (backup)"). */
    fun answered(active: String?, model: String) {
        provider = active?.removeSuffix(" (backup)") ?: ""
        this.model = model
        fallback = active?.endsWith("(backup)") == true
    }

    fun failed(e: AiError, at: String) {
        outcome = Interaction.OUTCOME_FAILED
        category = e.category.name
        stage = at
        if (provider.isBlank()) provider = e.provider
    }

    /** Stopped, or talked over: where it was when it was. */
    fun stopped() {
        outcome = Interaction.OUTCOME_STOPPED
        interrupted = true
        if (stage == null) stage = when {
            source != VoiceSource.TYPED && sttMs == null -> "recognition"
            aiMs == null -> "AI"
            else -> "speech"
        }
    }

    fun build(): Interaction = Interaction(atMs, source.name, provider, model, sttMs,
        firstWordMs, aiMs, ttsMs, outcome, category, stage, interrupted, fallback, question)
}

/** What the history says, for a provider and/or capability, or for all. */
data class HistoryStats(
    val count: Int,
    val answered: Int,
    val failed: Int,
    val stopped: Int,
    val fallbacks: Int,
    val successRate: Double?,            // answered / (answered + failed)
    val avgSttMs: Long?,
    val avgFirstWordMs: Long?,
    val avgAiMs: Long?,
    val avgTtsMs: Long?,
    val slow: Int,                       // answers that took longer than SLOW_MS
    val byCategory: Map<String, Int>,
    val byProvider: Map<String, Pair<Int, Int>>,   // provider -> (answered, failed)
)

/**
 * The interaction history: kept on the phone (a file in its private
 * storage), bounded to MAX records and to the days you chose, deleted when
 * you say. Never uploaded. Pure parts ([stats], [prune]) are unit-tested.
 */
object History {
    const val MAX = 500
    const val SLOW_MS = 10_000L
    const val CAP_STT = "STT"
    const val CAP_LLM = "LLM"
    const val CAP_TTS = "TTS"
    private const val FILE = "ai_history.jsonl"

    fun stats(all: List<Interaction>, provider: String? = null, capability: String? = null):
        HistoryStats {
        val list = all.filter { i ->
            (provider == null || i.provider == provider) && when (capability) {
                CAP_STT -> i.sttMs != null || i.stage == "recognition"
                CAP_LLM -> i.aiMs != null || i.stage == "AI"
                CAP_TTS -> i.ttsMs != null || i.stage == "speech"
                else -> true
            }
        }
        fun avg(f: (Interaction) -> Long?) = list.mapNotNull(f).takeIf { it.isNotEmpty() }
            ?.let { it.sum() / it.size }
        val answered = list.count { it.outcome == Interaction.OUTCOME_ANSWERED }
        val failed = list.count { it.outcome == Interaction.OUTCOME_FAILED }
        return HistoryStats(
            list.size, answered, failed, list.count { it.outcome == Interaction.OUTCOME_STOPPED },
            list.count { it.fallback },
            if (answered + failed > 0) answered.toDouble() / (answered + failed) else null,
            avg { it.sttMs }, avg { it.firstWordMs }, avg { it.aiMs }, avg { it.ttsMs },
            list.count { (it.aiMs ?: 0) > SLOW_MS },
            list.mapNotNull { it.category }.groupingBy { it }.eachCount(),
            list.filter { it.provider.isNotBlank() }.groupBy { it.provider }.mapValues { (_, v) ->
                v.count { it.outcome == Interaction.OUTCOME_ANSWERED } to
                    v.count { it.outcome == Interaction.OUTCOME_FAILED }
            })
    }

    /** The records kept: no older than [days] (0: none kept), the newest MAX. */
    fun prune(all: List<Interaction>, nowMs: Long, days: Int): List<Interaction> {
        if (days <= 0) return emptyList()
        val since = nowMs - days * 86_400_000L
        return all.filter { it.atMs >= since }.sortedBy { it.atMs }.takeLast(MAX)
    }

    /**
     * What may be kept of [all] under your settings: [prune]d, and the
     * questions' words only while you keep them ([text]).
     */
    fun kept(all: List<Interaction>, nowMs: Long, days: Int, text: Boolean): List<Interaction> =
        prune(all, nowMs, days).map { if (text || it.question == null) it else it.copy(question = null) }

    private fun file(context: Context) = File(context.filesDir, FILE)

    private fun read(context: Context): List<Interaction> = runCatching {
        file(context).takeIf { it.exists() }?.readLines()?.mapNotNull(Interaction::decode)
    }.getOrNull() ?: emptyList()

    private fun write(context: Context, all: List<Interaction>) {
        runCatching {
            if (all.isEmpty()) file(context).delete()
            else file(context).writeText(all.joinToString("\n", postfix = "\n") { Interaction.encode(it) })
        }
    }

    /**
     * The history, as your settings now allow: a shorter keep, "off", or the
     * questions' words turned off take effect on what is already stored.
     */
    @Synchronized
    fun load(context: Context): List<Interaction> {
        val raw = read(context)
        val s = AiPrefs.load(context)
        val kept = kept(raw, System.currentTimeMillis(), s.historyDays, s.historyText)
        if (kept != raw) write(context, kept)
        return kept
    }

    /** Add [i], keeping only what the retention setting allows. */
    @Synchronized
    fun add(context: Context, i: Interaction) {
        val s = AiPrefs.load(context)
        write(context, kept(read(context) + i, System.currentTimeMillis(), s.historyDays,
            s.historyText))
    }

    @Synchronized
    fun clear(context: Context) {
        runCatching { file(context).delete() }
    }
}
