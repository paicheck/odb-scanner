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
            // Room doesn't easily query "distinct (ecu, pid) where success=1"
            // without a custom query; keep it simple and load all then filter.
            measurements.getAll(vehicleId)
                .filter { it.success && it.service == "UDS-0x22" && it.pid.startsWith("0x") }
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
            measurements.getAll(vehicleId)
                .filter { it.success }
                .distinctBy { it.key }
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

    /** The VIN to stamp on uploads, or null when the vehicle is unknown yet. */
    suspend fun vinFor(vehicleId: Long): String? = withContext(Dispatchers.IO) {
        (vehicles.getById(vehicleId) ?: vehicles.getFirst())?.vin
    }

    /** The most recently used vehicle row, for the startup history view. */
    suspend fun firstVehicleId(): Long? = withContext(Dispatchers.IO) {
        vehicles.getFirst()?.id
    }
}
