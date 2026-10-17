package com.odbscanner.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * The single Room database, mirroring the Python side's `odb_scanner.db`.
 * Version 1: destructive migration is allowed during development (the schema
 * is still evolving); production will need proper migrations once it stabilises.
 */
@Database(
    entities = [
        MeasurementEntity::class,
        UnsupportedDidEntity::class,
        TxLogEntity::class,
        VehicleEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun measurementDao(): MeasurementDao
    abstract fun unsupportedDidDao(): UnsupportedDidDao
    abstract fun txLogDao(): TxLogDao
    abstract fun vehicleDao(): VehicleDao

    companion object {
        @Volatile private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "odb_scanner.db",
                ).fallbackToDestructiveMigration().build().also { instance = it }
            }
    }
}
