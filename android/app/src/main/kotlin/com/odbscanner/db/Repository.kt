package com.odbscanner.db

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.UUID

/**
 * Thin wrapper around the Room DAOs, mirroring `database/repository.py`'s
 * Repository. All methods are `suspend` and run on [Dispatchers.IO].
 */
class Repository(private val db: AppDatabase) {

    val measurements: MeasurementDao get() = db.measurementDao()
    val unsupported: UnsupportedDidDao get() = db.unsupportedDidDao()
    val txLog: TxLogDao get() = db.txLogDao()
    val vehicles: VehicleDao get() = db.vehicleDao()
    val dtcs: DtcDao get() = db.dtcDao()
    val chargingSessions: ChargingSessionDao get() = db.chargingSessionDao()
    val diagnosticEvents: DiagnosticEventDao get() = db.diagnosticEventDao()

    suspend fun ensureVehicle(
        vin: String,
        make: String = "Volkswagen",
        model: String = "ID.3",
        variant: String = "",
        year: Int = 0,
    ): Long = withContext(Dispatchers.IO) {
        val existing = vehicles.getByVin(vin)
        if (existing != null) existing.id
        else vehicles.insert(VehicleEntity(vin = vin, make = make, model = model, variant = variant, year = year))
    }

    suspend fun recordMeasurement(
        vehicleId: Long,
        ts: String,
        key: String,
        ecu: String,
        service: String,
        pid: String,
        unit: String,
        provenance: String,
        decodingMethod: String,
        docStatus: String?,
        rawResponse: String,
        value: Double?,
        textValue: String?,
        success: Boolean,
        error: String?,
    ): Long = withContext(Dispatchers.IO) {
        measurements.insert(MeasurementEntity(
            vehicleId = vehicleId, ts = ts, ecu = ecu, service = service, pid = pid,
            key = key, value = value, textValue = textValue, unit = unit,
            provenance = provenance, decodingMethod = decodingMethod, docStatus = docStatus,
            rawResponse = rawResponse, success = success, error = error,
            // The cloud id is assigned here, once, at write time. It must be
            // stable across upload attempts and app restarts, because the
            // shared schema deduplicates on it -- a fresh UUID per attempt
            // would turn every retry into a duplicate row.
            remoteId = UUID.randomUUID().toString(),
        ))
    }

    suspend fun markUnsupported(vehicleId: Long, ecuKey: String, did: Int, ts: String) =
        withContext(Dispatchers.IO) {
            val existing = unsupported.getAll(vehicleId).firstOrNull {
                it.ecuKey == ecuKey && it.did == did
            }
            unsupported.upsert(UnsupportedDidEntity(
                vehicleId = vehicleId, ecuKey = ecuKey, did = did,
                firstSeen = existing?.firstSeen ?: ts,
                lastSeen = ts,
                count = (existing?.count ?: 0) + 1,
            ))
        }

    suspend fun unsupportedKeys(vehicleId: Long): Set<Pair<String, Int>> =
        withContext(Dispatchers.IO) {
            unsupported.getAll(vehicleId).map { it.ecuKey to it.did }.toSet()
        }

    suspend fun everSucceededKeys(vehicleId: Long): Set<Pair<String, Int>> =
        withContext(Dispatchers.IO) {
            // DISTINCT in SQL: this runs on every connect, and loading every
            // measurement row to find the distinct pairs grew with the table.
            measurements.succeededEcuPids(vehicleId)
                .mapNotNull { m ->
                    try { m.ecu to m.pid.removePrefix("0x").toInt(16) }
                    catch (_: NumberFormatException) { null }
                }.toSet()
        }

    suspend fun recordTx(
        ts: String, direction: String, ecu: String?, payload: String, purpose: String?,
    ): Long = withContext(Dispatchers.IO) {
        txLog.insert(TxLogEntity(ts = ts, direction = direction, ecu = ecu,
            payload = payload, purpose = purpose))
    }

    /** Latest successful value per key (for the measurements screen). */
    suspend fun latestMeasurements(vehicleId: Long): List<MeasurementEntity> =
        withContext(Dispatchers.IO) {
            measurements.latestPerKey(vehicleId)
        }

    // -- cloud upload --------------------------------------------------------
    /** Rows not yet acknowledged by the cloud, oldest first. */
    suspend fun pendingUploadBatch(limit: Int): List<MeasurementEntity> =
        withContext(Dispatchers.IO) { measurements.pendingUpload(limit) }

    suspend fun pendingUploadCount(): Int =
        withContext(Dispatchers.IO) { measurements.pendingUploadCount() }

    /**
     * Give every row written before cloud support existed a stable cloud id.
     *
     * Cheap after the first run (the query returns nothing), and it is what
     * makes those older readings uploadable rather than stranded forever.
     */
    suspend fun backfillRemoteIds(): Int = withContext(Dispatchers.IO) {
        var filled = 0
        for (rowId in measurements.idsMissingRemoteId()) {
            filled += measurements.assignRemoteId(rowId, UUID.randomUUID().toString())
        }
        filled
    }

    /** Mark rows as uploaded. Only called after the API has accepted them. */
    suspend fun markUploaded(ids: List<Long>, at: Long): Int = withContext(Dispatchers.IO) {
        if (ids.isEmpty()) 0 else measurements.markUploaded(ids, at)
    }

    /**
     * The VIN of exactly this vehicle row, or null. No fallback to another
     * vehicle: a reading attributed to the wrong VIN is worse than one with
     * none, because the cloud archive is keyed by it.
     */
    suspend fun vinOf(vehicleId: Long): String? = withContext(Dispatchers.IO) {
        vehicles.getById(vehicleId)?.vin
    }

    /** The most recently used vehicle row, for the startup history view. */
    suspend fun firstVehicleId(): Long? = withContext(Dispatchers.IO) {
        vehicles.getFirst()?.id
    }

    /** Newest [limit] numeric values for one key, oldest first (for charts). */
    suspend fun valueHistory(vehicleId: Long, key: String, limit: Int = 600): List<Double> =
        withContext(Dispatchers.IO) {
            measurements.valueHistory(vehicleId, key, limit).mapNotNull { it.value }
        }

    // -- DTCs ----------------------------------------------------------------

    /**
     * Record one sighting of a DTC. First sighting inserts the row; later
     * sightings refresh `lastSeen`/`status` and bump the occurrence count. Read
     * once per slow phase (not per poll), so the count stays a count of
     * sightings, not of polls.
     */
    suspend fun upsertDtc(
        vehicleId: Long, ts: String, ecu: String, code: String, status: String,
    ) = withContext(Dispatchers.IO) {
        val existing = dtcs.find(vehicleId, ecu, code)
        if (existing == null) {
            dtcs.insert(
                DtcEntity(
                    vehicleId = vehicleId, ecu = ecu, code = code, status = status,
                    firstSeen = ts, lastSeen = ts, occurrenceCount = 1,
                ),
            )
        } else {
            dtcs.update(existing.id, ts, existing.occurrenceCount + 1, status)
        }
    }

    suspend fun dtcList(vehicleId: Long): List<DtcEntity> =
        withContext(Dispatchers.IO) { dtcs.getAll(vehicleId) }

    // -- charging sessions ---------------------------------------------------

    suspend fun openSession(
        vehicleId: Long, ts: String, chargeType: String, startSoc: Double?,
    ): Long = withContext(Dispatchers.IO) {
        chargingSessions.insert(
            ChargingSessionEntity(
                vehicleId = vehicleId, chargeType = chargeType,
                startedAt = ts, startSoc = startSoc, status = "open",
            ),
        )
    }

    suspend fun closeSession(sessionId: Long, ts: String, endSoc: Double?) =
        withContext(Dispatchers.IO) {
            chargingSessions.close(sessionId, ts, endSoc)
        }

    /** A session a previous run left open, so a restart can adopt it. */
    suspend fun openSessionId(vehicleId: Long): Long? =
        withContext(Dispatchers.IO) { chargingSessions.openSession(vehicleId)?.id }

    // -- diagnostic events ---------------------------------------------------

    suspend fun addEvent(
        vehicleId: Long, ts: String, kind: String, description: String,
        ecu: String? = null, data: String? = null,
    ) = withContext(Dispatchers.IO) {
        diagnosticEvents.insert(
            DiagnosticEventEntity(
                vehicleId = vehicleId, ts = ts, kind = kind, ecu = ecu,
                description = description, data = data,
            ),
        )
    }
}
