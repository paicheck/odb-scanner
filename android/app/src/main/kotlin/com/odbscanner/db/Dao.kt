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

    /** The newest reading of [key] that decoded to a number (route planner, car screen). */
    @Query(
        "SELECT * FROM measurements WHERE vehicleId = :vehicleId AND key = :key " +
            "AND success = 1 AND value IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    suspend fun latestValue(vehicleId: Long, key: String): MeasurementEntity?

    /**
     * The newest successful row per key, newest first. The screen used to load
     * the WHOLE table every 3 seconds and de-duplicate in Kotlin; an always-on
     * car phone adds hundreds of rows a pass, so that grew without bound. A
     * GROUP BY over the table still read every row each time, so this walks
     * the distinct keys through the (vehicleId, key) index -- one seek per key
     * -- and seeks each key's newest successful row.
     */
    @Query(
        "WITH RECURSIVE k(key) AS (" +
            "SELECT (SELECT MIN(key) FROM measurements WHERE vehicleId = :vehicleId) " +
            "UNION ALL SELECT (SELECT MIN(key) FROM measurements " +
            "WHERE vehicleId = :vehicleId AND key > k.key) FROM k WHERE k.key IS NOT NULL) " +
            "SELECT m.* FROM k JOIN measurements m ON m.id = (" +
            "SELECT x.id FROM measurements x WHERE x.vehicleId = :vehicleId " +
            "AND x.key = k.key AND x.success = 1 ORDER BY x.id DESC LIMIT 1) " +
            "ORDER BY m.id DESC"
    )
    suspend fun latestPerKey(vehicleId: Long): List<MeasurementEntity>

    /** (ecu, pid) of every UDS 0x22 read that ever succeeded, without loading rows. */
    @Query(
        "SELECT DISTINCT ecu, pid FROM measurements WHERE vehicleId = :vehicleId " +
            "AND success = 1 AND service = 'UDS-0x22' AND pid LIKE '0x%'"
    )
    suspend fun succeededEcuPids(vehicleId: Long): List<EcuPid>

    /**
     * The newest [limit] successful numeric values for one key, oldest first.
     * Feeds the trend charts; `value IS NOT NULL` keeps out the text-only rows
     * (VIN, gear) that have no y-axis to plot. (It once ordered ascending before
     * the LIMIT, which returned the OLDEST [limit] values: the trend chart
     * froze on the first 50 minutes of a key and never moved again.)
     */
    @Query(
        "SELECT * FROM (SELECT * FROM measurements WHERE vehicleId = :vehicleId " +
            "AND key = :key AND success = 1 AND value IS NOT NULL " +
            "ORDER BY id DESC LIMIT :limit) ORDER BY id ASC"
    )
    suspend fun valueHistory(vehicleId: Long, key: String, limit: Int): List<MeasurementEntity>

    @Query("SELECT COUNT(*) FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun count(vehicleId: Long): Int

    @Query("DELETE FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun deleteAll(vehicleId: Long)

    // -- cloud upload queue --------------------------------------------------
    /**
     * The next rows to upload: the uploadedAt index holds the NULLs (the
     * queue) together, in id order. Without it each batch walked past every
     * row already uploaded, which on an always-on car phone is millions.
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
