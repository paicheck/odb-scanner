package com.odbscanner.db

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * One decoded measurement, mirroring `database/models.py`'s `measurements`.
 * Raw bytes are ALWAYS preserved (`rawResponse`), even when `value` is null.
 */
@Entity(tableName = "measurements")
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
)

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
