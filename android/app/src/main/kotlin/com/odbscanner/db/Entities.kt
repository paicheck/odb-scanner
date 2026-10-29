package com.odbscanner.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * One decoded measurement, mirroring `database/models.py`'s `measurements`.
 * Raw bytes are ALWAYS preserved (`rawResponse`), even when `value` is null.
 *
 * [remoteId] and [uploadedAt] exist only for cloud upload. `remoteId` is the
 * measurement's UUID in the shared cloud schema and is assigned once, when the
 * row is written; [uploadedAt] is null until the cloud has acknowledged it, so
 * the uploader's queue is just "rows where uploadedAt is null". Both are
 * nullable because rows written before cloud support existed have neither --
 * [Repository.backfillRemoteIds] fills the first, the uploader fills the second.
 */
@Entity(
    tableName = "measurements",
    // (vehicleId, key): every per-key lookup -- the newest value, the trend
    // history, the latest-per-key walk -- seeks instead of scanning the table.
    // uploadedAt: the upload queue is the rows where it is NULL; without an
    // index each batch walked past every row already uploaded.
    indices = [Index(value = ["vehicleId", "key"]), Index(value = ["uploadedAt"])],
)
data class MeasurementEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val vehicleId: Long = 1,
    val ts: String,
    val ecu: String,
    val service: String,
    val pid: String,
    val key: String,
    val value: Double?,
    val textValue: String?,
    val unit: String,
    val provenance: String,
    val decodingMethod: String,
    val docStatus: String?,
    val rawResponse: String,
    val success: Boolean = true,
    val error: String?,
    val remoteId: String? = null,
    val uploadedAt: Long? = null,
)

/** Result row of MeasurementDao.succeededEcuPids. */
data class EcuPid(val ecu: String, val pid: String)

/** One retired DID, mirroring `database/models.py`'s `unsupported_dids`. */
@Entity(
    tableName = "unsupported_dids",
    primaryKeys = ["vehicleId", "ecuKey", "did"],
)
data class UnsupportedDidEntity(
    val vehicleId: Long,
    val ecuKey: String,
    val did: Int,
    val firstSeen: String,
    val lastSeen: String,
    val count: Int = 1,
)

/** One safety-manifest entry, mirroring `database/models.py`'s `tx_log`. */
@Entity(tableName = "tx_log")
data class TxLogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val ts: String,
    val direction: String,   // "TX" or "RX"
    val ecu: String?,
    val payload: String,
    val purpose: String?,
)

/** A vehicle (keyed by VIN), mirroring `database/models.py`'s `vehicles`. */
@Entity(tableName = "vehicles")
data class VehicleEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val vin: String,
    val make: String = "Volkswagen",
    val model: String = "ID.3",
    val variant: String = "",
    val year: Int = 0,
)

/**
 * One stored DTC, mirroring `database/models.py`'s `dtcs`. The raw code and its
 * ISO 14229 status are kept; the human description and category list live only
 * in the Python analysis layer for now, so `code` + `status` are the source of
 * truth the phone shows.
 */
@Entity(
    tableName = "dtcs",
    indices = [Index(value = ["vehicleId", "ecu", "code"], unique = true)],
)
data class DtcEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val vehicleId: Long,
    val ecu: String,
    val code: String,
    val status: String,          // warning | active | pending | confirmed | stored | unknown
    val firstSeen: String,
    val lastSeen: String,
    val occurrenceCount: Int = 1,
)

/** One charging session, mirroring `charging_sessions` (minus the sample table). */
@Entity(tableName = "charging_sessions")
data class ChargingSessionEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val vehicleId: Long,
    val chargeType: String?,     // AC | DC
    val startedAt: String,
    val endedAt: String? = null,
    val startSoc: Double? = null,
    val endSoc: Double? = null,
    val status: String = "open", // open | completed
    val notes: String? = null,
)

/** One diagnostic event, mirroring `diagnostic_events`. */
@Entity(tableName = "diagnostic_events")
data class DiagnosticEventEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val vehicleId: Long,
    val ts: String,
    val kind: String,            // adapter | charge-start | charge-end | note | dtc-cleared
    val ecu: String?,
    val description: String,
    val data: String? = null,    // JSON
)
