package com.odbscanner.db

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

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
}
