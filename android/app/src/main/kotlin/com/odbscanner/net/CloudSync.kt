package com.odbscanner.net

import com.odbscanner.db.MeasurementEntity
import com.odbscanner.db.Repository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** A cloud upload failed. Always retryable; nothing is lost. */
class CloudSyncException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

/** What one upload run achieved. */
data class SyncResult(val uploaded: Int, val batches: Int, val duplicates: Int)

/**
 * Uploads decoded measurements from the local Room store to the cloud ingest
 * API, in batches, oldest first.
 *
 * Nothing here touches the vehicle or the Bluetooth link: it reads rows Room
 * already holds and POSTs them. A failed upload therefore cannot disturb a
 * collection pass, and the local database stays the source of truth.
 *
 * Idempotency comes from the row's [MeasurementEntity.remoteId] -- assigned
 * once, at write time -- which travels as the request's `id`. The server
 * ignores an id it already holds, so re-sending after a dropped response is
 * always safe. A batch is only marked uploaded after the API answered 2xx.
 *
 * Implemented on HttpURLConnection rather than OkHttp to avoid pulling a new
 * networking dependency into the app; the request volume (a batch a minute at
 * most) makes that a non-issue.
 */
class CloudSync(
    private val repo: Repository,
    private val settings: CloudSettings,
    private val batchSize: Int = 500,
    private val connectTimeoutMs: Int = 15_000,
    private val readTimeoutMs: Int = 30_000,
) {
    suspend fun uploadPending(): SyncResult =
        withContext(Dispatchers.IO) {
            check(settings.isUsable) {
                "Cloud sync needs an API URL and token in its settings"
            }
            // Give pre-cloud rows an id so they can be uploaded too.
            repo.backfillRemoteIds()
            // Each row's own vehicle. One VIN used to be stamped on every
            // pending row (vinFor(1), falling back to the first vehicle), so
            // once a phone had seen two cars, car B's readings were filed in
            // the shared archive under car A's VIN. Unknown stays null, not "".
            val vins = mutableMapOf<Long, String?>()

            var uploaded = 0
            var duplicates = 0
            var batches = 0
            while (true) {
                val rows = repo.pendingUploadBatch(batchSize)
                if (rows.isEmpty()) break

                for (id in rows.map { it.vehicleId }.toSet() - vins.keys) {
                    vins[id] = repo.vinOf(id)
                }
                val response = post(buildPayload(rows, vins))
                uploaded += response.optInt("accepted", 0)
                duplicates += response.optInt("duplicates", 0)
                // Only now: the API has durably stored these rows. Marking
                // before the response would drop them on a network failure.
                repo.markUploaded(rows.map { it.id }, System.currentTimeMillis())
                batches++

                if (rows.size < batchSize) break
            }
            SyncResult(uploaded, batches, duplicates)
        }

    private fun buildPayload(
        rows: List<MeasurementEntity>,
        vins: Map<Long, String?>,
    ): JSONObject {
        val source = JSONObject()
            .put("source_id", settings.sourceId)
            .put("kind", "android")
            .put("label", android.os.Build.MODEL)

        val measurements = JSONArray()
        for (m in rows) {
            measurements.put(
                JSONObject()
                    .put("id", m.remoteId.orEmpty())
                    .put("vin", vins[m.vehicleId] ?: JSONObject.NULL)
                    // Instant.toString() ends in 'Z'; the API normalises it.
                    .put("ts", m.ts)
                    .put("ecu", m.ecu)
                    .put("service", m.service)
                    .put("pid", m.pid)
                    .put("key", m.key)
                    .put("value", m.value ?: JSONObject.NULL)
                    .put("text_value", m.textValue ?: JSONObject.NULL)
                    .put("unit", m.unit)
                    .put("provenance", m.provenance)
                    .put("decoding_method", m.decodingMethod)
                    .put("doc_status", m.docStatus ?: JSONObject.NULL)
                    .put("raw_response", m.rawResponse)
                    .put("success", m.success)
                    .put("error", m.error ?: JSONObject.NULL),
            )
        }
        return JSONObject().put("source", source).put("measurements", measurements)
    }

    private fun post(payload: JSONObject): JSONObject {
        val connection = (URL(settings.uploadUrl).openConnection() as HttpURLConnection)
            .apply {
                requestMethod = "POST"
                connectTimeout = connectTimeoutMs
                readTimeout = readTimeoutMs
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty("Authorization", "Bearer ${settings.token}")
            }
        try {
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                .orEmpty()
            if (code !in 200..299) {
                throw CloudSyncException(
                    "Ingest API returned HTTP $code: ${body.take(300)}",
                )
            }
            return JSONObject(body)
        } catch (e: IOException) {
            // Never include the token or the URL's token in the message: it is
            // shown in the UI and could be copied elsewhere.
            throw CloudSyncException("Could not reach the ingest API: ${e.message}", e)
        } finally {
            connection.disconnect()
        }
    }
}
