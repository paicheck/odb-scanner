package com.odbscanner.ai

import com.anthropic.client.AnthropicClient
import com.anthropic.client.okhttp.AnthropicOkHttpClient
import com.anthropic.core.JsonValue
import com.anthropic.errors.AnthropicIoException
import com.anthropic.errors.AnthropicServiceException
import com.anthropic.models.beta.messages.BetaContentBlockParam
import com.anthropic.models.beta.messages.BetaFallbacksParam
import com.anthropic.models.beta.messages.BetaMessage
import com.anthropic.models.beta.messages.BetaMessageParam
import com.anthropic.models.beta.messages.BetaOutputConfig
import com.anthropic.models.beta.messages.BetaStopReason
import com.anthropic.models.beta.messages.BetaTool
import com.anthropic.models.beta.messages.BetaToolResultBlockParam
import com.anthropic.models.beta.messages.MessageCreateParams
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration

/**
 * Claude through Anthropic's official Java SDK, beta Messages API.
 *
 * - Thinking is left to the model (always on for current Claude models);
 *   [effort] is the control -- "low" by default, for a quick spoken answer.
 * - Refusal fallback opted in (`fallbacks: "default"`): a request the safety
 *   classifiers decline is re-run, in the same call, on the model Anthropic
 *   recommends for that category.
 * - The history is append-only: every reply goes back exactly as returned
 *   ([BetaMessage.toParam], thinking blocks included), every set of tool
 *   results in one user message. Tools are declared once, for the session.
 * - No sampling parameters and no forced tool choice (current models reject
 *   both).
 * - The SDK's call blocks and has no cancel, so it runs in Cancellable: a
 *   cancelled turn returns at once and the late reply is dropped. The SDK's
 *   own retries are off -- a voice turn falls back to the next service
 *   instead of waiting through retries -- and a failed or cancelled turn is
 *   taken out of the history again.
 */
class ClaudeChatSession(
    apiKey: String,
    override val model: String,
    private val effort: String,
    private val system: String,
    tools: List<ToolSpec>,
    baseUrl: String = "",
) : ChatSession {
    private val key = apiKey
    private val client: AnthropicClient = AnthropicOkHttpClient.builder()
        .apiKey(apiKey)
        .timeout(Duration.ofSeconds(TIMEOUT_S))
        .maxRetries(0)
        .apply { if (baseUrl.isNotBlank()) baseUrl(baseUrl) }
        .build()
    private val betaTools = tools.map(::betaTool)
    private val history = mutableListOf<BetaMessageParam>()

    override suspend fun ask(text: String, tools: ToolRunner): String = withContext(Dispatchers.IO) {
        val mark = history.size
        try {
            turn(text, tools)
        } catch (t: Throwable) {
            while (history.size > mark) history.removeAt(history.size - 1)
            throw t
        }
    }

    private suspend fun turn(text: String, tools: ToolRunner): String {
        history.add(BetaMessageParam.ofUser(text))
        repeat(MAX_TOOL_ROUNDS) {
            val reply = create()
            when (reply.stopReason().orElse(null)) {
                BetaStopReason.REFUSAL -> {
                    // Declined even after the fallback; nothing to echo (no
                    // output). The next question simply follows this one.
                    val why = reply.stopDetails().flatMap { it.category() }.map { it.toString() }
                        .orElse(null)
                    return "I can't help with that one" + (if (why != null) " ($why)." else ".")
                }
                else -> Unit
            }
            history.add(reply.toParam())
            val calls = reply.content().mapNotNull { it.toolUse().orElse(null) }
            if (reply.stopReason().orElse(null) != BetaStopReason.TOOL_USE || calls.isEmpty()) {
                val answer = textOf(reply)
                if (answer.isBlank()) {
                    throw AiException(AiError(AiErrorCategory.INVALID_RESPONSE, LABEL,
                        "Claude gave no answer"))
                }
                return if (reply.stopReason().orElse(null) == BetaStopReason.MAX_TOKENS)
                    "$answer (cut short)" else answer
            }
            // Every result for this turn in ONE user message, matched by id.
            val results = calls.map { call ->
                val args = runCatching { JSONObject(call.input(Map::class.java)) }
                    .getOrDefault(JSONObject())
                val out = tools.run(ToolCall(call.id(), call.name(), args))
                BetaContentBlockParam.ofToolResult(BetaToolResultBlockParam.builder()
                    .toolUseId(call.id()).content(out).build())
            }
            history.add(BetaMessageParam.ofUserOfContentBlockParams(results))
        }
        throw AiException(AiError(AiErrorCategory.INVALID_RESPONSE, LABEL,
            "Claude kept calling tools without answering"))
    }

    private suspend fun create(): BetaMessage {
        val params = MessageCreateParams.builder()
            .model(model)
            .maxTokens(16000L)
            .system(system)
            .messages(history.toList())
            .outputConfig(BetaOutputConfig.builder().effort(BetaOutputConfig.Effort.of(effort)).build())
            .addBeta(FALLBACK_BETA)
            .fallbacks(BetaFallbacksParam.ofDefault())
            .apply { betaTools.forEach { addTool(it) } }
            .build()
        return guarded(model, key) { client.beta().messages().create(params) }
    }

    companion object {
        const val LABEL = "Claude (Anthropic)"
        const val HOST = "api.anthropic.com"
        const val TIMEOUT_S = 90L
        const val FALLBACK_BETA = "server-side-fallback-2026-07-01"
        val SUGGESTED = listOf("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
            "claude-fable-5-1")

        /** Text blocks only: thinking and fallback-marker blocks carry none. */
        fun textOf(reply: BetaMessage): String =
            reply.content().mapNotNull { it.text().orElse(null)?.text() }.joinToString("").trim()

        fun betaTool(spec: ToolSpec): BetaTool {
            val props = BetaTool.InputSchema.Properties.builder()
            val p = spec.parameters.optJSONObject("properties") ?: JSONObject()
            p.keys().forEach { name ->
                props.putAdditionalProperty(name, JsonValue.from(plain(p.getJSONObject(name))))
            }
            val required = spec.parameters.optJSONArray("required")
                ?.let { r -> (0 until r.length()).map { r.getString(it) } } ?: emptyList()
            return BetaTool.builder()
                .name(spec.name)
                .description(spec.description)
                .inputSchema(BetaTool.InputSchema.builder()
                    .properties(props.build())
                    .required(required)
                    .build())
                .build()
        }

        /** org.json -> plain maps and lists, which JsonValue.from takes. */
        fun plain(v: Any?): Any? = when (v) {
            is JSONObject -> v.keys().asSequence().associateWith { plain(v.get(it)) }
            is JSONArray -> (0 until v.length()).map { plain(v.get(it)) }
            JSONObject.NULL -> null
            else -> v
        }

        /** An SDK failure in its category, the key taken out of any message. */
        fun classify(e: Throwable, model: String, key: String): AiError = when (e) {
            is AiException -> e.error ?: AiErrors.fromException(LABEL, HOST, e)
            is AnthropicServiceException ->
                AiErrors.fromHttp(LABEL, e.statusCode(), e.message ?: "", model, listOf(key))
            is AnthropicIoException ->
                AiErrors.fromException(LABEL, HOST, e.cause ?: e, sent = true,
                    timeoutS = TIMEOUT_S.toInt())
            else -> AiErrors.fromException(LABEL, HOST, e)
        }

        /** [work] (a blocking SDK call) cancellable, its failures classified. */
        suspend fun <T> guarded(model: String, key: String, work: () -> T): T = try {
            Cancellable.blocking(work = work)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            throw AiException(classify(e, model, key), e)
        }

        private fun client(apiKey: String, baseUrl: String, timeoutS: Long) =
            AnthropicOkHttpClient.builder().apiKey(apiKey).maxRetries(0)
                .timeout(Duration.ofSeconds(timeoutS))
                .apply { if (baseUrl.isNotBlank()) baseUrl(baseUrl) }.build()

        /** Model ids the key can use, newest first (GET /v1/models): a refused key fails here. */
        suspend fun listModels(apiKey: String, baseUrl: String = ""): List<String> =
            guarded("", apiKey) { client(apiKey, baseUrl, 20).models().list().data().map { it.id() } }

        /** One short question, no tools, no history: the setup screen's AI test. */
        suspend fun probe(apiKey: String, model: String, prompt: String,
                          baseUrl: String = ""): Probe = guarded(model, apiKey) {
            val reply = client(apiKey, baseUrl, 30).messages().create(
                com.anthropic.models.messages.MessageCreateParams.builder()
                    .model(model).maxTokens(200L).addUserMessage(prompt).build())
            val text = reply.content().mapNotNull { it.text().orElse(null)?.text() }
                .joinToString("").trim()
            Probe(text, reply.model().asString(), reply.usage().inputTokens().toInt(),
                reply.usage().outputTokens().toInt())
        }
    }
}
