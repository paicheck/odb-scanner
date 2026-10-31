package com.odbscanner.ai

import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ThreadFactory
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Blocking work (a network call that has no cancel of its own) that the
 * caller can walk away from. Cancelling the coroutine returns at once:
 * [abort] runs (closing a socket makes the blocked read fail), the worker is
 * interrupted, and whatever the work produces afterwards is dropped -- a
 * cancelled request can never deliver an answer.
 */
object Cancellable {
    private val pool = Executors.newCachedThreadPool(ThreadFactory { r ->
        Thread(r, "ai-io").apply { isDaemon = true }
    })

    suspend fun <T> blocking(abort: () -> Unit = {}, work: () -> T): T =
        suspendCancellableCoroutine { cont ->
            val task = pool.submit {
                val result = runCatching(work)
                if (cont.isActive) {
                    result.fold({ cont.resume(it) }, { cont.resumeWithException(it) })
                }
            }
            cont.invokeOnCancellation {
                runCatching(abort)
                task.cancel(true)
            }
        }
}

data class HttpRequest(
    val method: String,
    val url: String,
    val headers: Map<String, String> = emptyMap(),
    val body: String? = null,
    val connectTimeoutMs: Int = 15_000,
    val readTimeoutMs: Int = 60_000,
)

data class HttpResponse(val status: Int, val body: String, val ms: Long,
                        val headers: Map<String, String> = emptyMap())   // names in lower case

/**
 * A request that failed before a reply: [sent] says whether it had gone out
 * (a timeout after that is the service, before it the network).
 */
class HttpFailure(val sent: Boolean, cause: Throwable) : IOException(cause.message, cause)

/** Sends one request. Cancelling the caller aborts it. Never logs headers. */
fun interface HttpTransport {
    suspend fun send(req: HttpRequest): HttpResponse

    /**
     * Send [req] and hand each line of a successful reply to [onLine] as it
     * arrives (server-sent events); an error reply comes back whole, its
     * body unread by [onLine]. The default reads the whole reply first --
     * enough for fakes and for a service that does not stream.
     */
    suspend fun stream(req: HttpRequest, onLine: (String) -> Unit): HttpResponse {
        val r = send(req)
        if (r.status < 400) r.body.lineSequence().forEach(onLine)
        return r
    }
}

/** HttpURLConnection, aborted by disconnect() when the caller is cancelled. */
object UrlTransport : HttpTransport {
    override suspend fun send(req: HttpRequest): HttpResponse = exchange(req, null)

    override suspend fun stream(req: HttpRequest, onLine: (String) -> Unit): HttpResponse =
        exchange(req, onLine)

    private suspend fun exchange(req: HttpRequest, onLine: ((String) -> Unit)?): HttpResponse {
        val conn = URL(req.url).openConnection() as HttpURLConnection
        return Cancellable.blocking(abort = { conn.disconnect() }) {
            val start = System.nanoTime()
            var sent = false
            try {
                conn.requestMethod = req.method
                conn.connectTimeout = req.connectTimeoutMs
                conn.readTimeout = req.readTimeoutMs
                req.headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
                if (req.body != null) {
                    val bytes = req.body.toByteArray(Charsets.UTF_8)
                    conn.doOutput = true
                    // A known length makes the connection open here, not at the
                    // reply, so `sent` really marks the request as on its way.
                    conn.setFixedLengthStreamingMode(bytes.size)
                    conn.outputStream.use { it.write(bytes) }
                } else {
                    conn.connect()
                }
                sent = true
                val code = conn.responseCode
                val text = if (onLine != null && code < 400) {
                    conn.inputStream.bufferedReader(Charsets.UTF_8).use { r ->
                        while (true) onLine(r.readLine() ?: break)
                    }
                    ""
                } else {
                    (if (code < 400) conn.inputStream else conn.errorStream)
                        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
                }
                val headers = conn.headerFields.orEmpty().mapNotNull { (k, v) ->
                    k?.lowercase()?.let { it to v.joinToString(",") }
                }.toMap()
                HttpResponse(code, text, (System.nanoTime() - start) / 1_000_000, headers)
            } catch (e: IOException) {
                throw HttpFailure(sent, e)
            } finally {
                conn.disconnect()
            }
        }
    }
}
