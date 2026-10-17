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

    @Query("SELECT COUNT(*) FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun count(vehicleId: Long): Int

    @Query("DELETE FROM measurements WHERE vehicleId = :vehicleId")
    suspend fun deleteAll(vehicleId: Long)
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

    @Query("SELECT * FROM vehicles LIMIT 1")
    suspend fun getFirst(): VehicleEntity?
}
