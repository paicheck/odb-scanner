package com.odbscanner.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface MeasurementDao {
    @Insert
    suspend fun insert(m: MeasurementEntity): Long

    @Query("SELECT * FROM measurements WHERE vehicleId = :vehicleId ORDER BY id DESC")
    suspend fun getAll(vehicleId: Long): List<MeasurementEntity>

    @Query("SELECT * FROM measurements WHERE vehicleId = :vehicleId AND key = :key ORDER BY id DESC LIMIT 1")
    suspend fun latestByKey(vehicleId: Long, key: String): MeasurementEntity?

    /**
     * The newest [limit] successful numeric values for one key, oldest first.
     * Feeds the trend charts; `value IS NOT NULL` keeps out the text-only rows
     * (VIN, gear) that have no y-axis to plot.
     */
    @Query(
        "SELECT * FROM measurements WHERE vehicleId = :vehicleId AND key = :key " +
            "AND success = 1 AND value IS NOT NULL ORDER BY id ASC LIMIT :limit"
    )
    suspend fun valueHistory(vehicleId: Long, key: String, limit: Int): List<MeasurementEntity>

    @Query("SELECT COUNT(*) FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun count(vehicleId: Long): Int

    @Query("DELETE FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun deleteAll(vehicleId: Long)

    // -- cloud upload queue --------------------------------------------------
    /**
     * The next rows to upload. An index on uploadedAt is deliberately absent:
     * the table is small (thousands of rows) and the query already scans for
     * NULLs, so an index would only add write cost.
     *
     * remoteId IS NOT NULL because a row with no id cannot be uploaded
     * idempotently; those are filled first by Repository.backfillRemoteIds().
     */
    @Query(
        "SELECT * FROM measurements " +
            "WHERE uploadedAt IS NULL AND remoteId IS NOT NULL " +
            "ORDER BY id ASC LIMIT :limit"
    )
    suspend fun pendingUpload(limit: Int): List<MeasurementEntity>

    @Query("UPDATE measurements SET uploadedAt = :at WHERE id IN (:ids)")
    suspend fun markUploaded(ids: List<Long>, at: Long): Int

    @Query("UPDATE measurements SET remoteId = :remoteId WHERE id = :id AND remoteId IS NULL")
    suspend fun assignRemoteId(id: Long, remoteId: String): Int

    @Query("SELECT id FROM measurements WHERE remoteId IS NULL")
    suspend fun idsMissingRemoteId(): List<Long>

    @Query("SELECT COUNT(*) FROM measurements WHERE uploadedAt IS NULL AND remoteId IS NOT NULL")
    suspend fun pendingUploadCount(): Int
}

@Dao
interface UnsupportedDidDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(d: UnsupportedDidEntity)

    @Query("SELECT * FROM unsupported_dids WHERE vehicleId = :vehicleId")
    suspend fun getAll(vehicleId: Long): List<UnsupportedDidEntity>

    @Query("SELECT COUNT(*) FROM unsupported_dids WHERE vehicleId = :vehicleId")
    suspend fun count(vehicleId: Long): Int
}

@Dao
interface TxLogDao {
    @Insert
    suspend fun insert(t: TxLogEntity): Long

    @Query("SELECT * FROM tx_log ORDER BY id DESC LIMIT :limit")
    suspend fun recent(limit: Int = 100): List<TxLogEntity>
}

@Dao
interface VehicleDao {
    @Insert
    suspend fun insert(v: VehicleEntity): Long

    @Query("SELECT * FROM vehicles WHERE vin = :vin LIMIT 1")
    suspend fun getByVin(vin: String): VehicleEntity?

    @Query("SELECT * FROM vehicles WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): VehicleEntity?

    @Query("SELECT * FROM vehicles LIMIT 1")
    suspend fun getFirst(): VehicleEntity?
}

@Dao
interface DtcDao {
    @Query("SELECT * FROM dtcs WHERE vehicleId = :vehicleId AND ecu = :ecu AND code = :code LIMIT 1")
    suspend fun find(vehicleId: Long, ecu: String, code: String): DtcEntity?

    @Insert
    suspend fun insert(d: DtcEntity): Long

    @Query(
        "UPDATE dtcs SET lastSeen = :lastSeen, occurrenceCount = :occurrenceCount, " +
            "status = :status WHERE id = :id"
    )
    suspend fun update(id: Long, lastSeen: String, occurrenceCount: Int, status: String)

    @Query("SELECT * FROM dtcs WHERE vehicleId = :vehicleId ORDER BY lastSeen DESC")
    suspend fun getAll(vehicleId: Long): List<DtcEntity>
}

@Dao
interface ChargingSessionDao {
    @Insert
    suspend fun insert(s: ChargingSessionEntity): Long

    @Query("SELECT * FROM charging_sessions WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): ChargingSessionEntity?

    @Query(
        "SELECT * FROM charging_sessions WHERE vehicleId = :vehicleId " +
            "AND status = 'open' ORDER BY startedAt DESC LIMIT 1"
    )
    suspend fun openSession(vehicleId: Long): ChargingSessionEntity?

    @Query(
        "UPDATE charging_sessions SET endedAt = :endedAt, endSoc = :endSoc, " +
            "status = 'completed' WHERE id = :id"
    )
    suspend fun close(id: Long, endedAt: String, endSoc: Double?)

    @Query("SELECT * FROM charging_sessions WHERE vehicleId = :vehicleId ORDER BY startedAt DESC")
    suspend fun getAll(vehicleId: Long): List<ChargingSessionEntity>
}

@Dao
interface DiagnosticEventDao {
    @Insert
    suspend fun insert(e: DiagnosticEventEntity): Long

    @Query(
        "SELECT * FROM diagnostic_events WHERE vehicleId = :vehicleId " +
            "ORDER BY id DESC LIMIT :limit"
    )
    suspend fun recent(vehicleId: Long, limit: Int): List<DiagnosticEventEntity>
}
