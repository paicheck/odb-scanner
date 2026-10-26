package com.odbscanner.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * The single Room database, mirroring the Python side's `odb_scanner.db`.
 *
 * Version 2 adds the two cloud-upload columns to `measurements`. It ships a
 * real migration rather than leaning on the destructive fallback, because the
 * whole point of the persisted store is history that survives an app upgrade --
 * dropping it on the way to cloud sync would throw away the readings you were
 * about to upload. The destructive fallback is kept for every OTHER unexpected
 * version, which during development is still the pragmatic choice; the version
 * bumped here so the normal path migrates cleanly is 1 -> 2.
 */
@Database(
    entities = [
        MeasurementEntity::class,
        UnsupportedDidEntity::class,
        TxLogEntity::class,
        VehicleEntity::class,
        DtcEntity::class,
        ChargingSessionEntity::class,
        DiagnosticEventEntity::class,
    ],
    version = 3,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun measurementDao(): MeasurementDao
    abstract fun unsupportedDidDao(): UnsupportedDidDao
    abstract fun txLogDao(): TxLogDao
    abstract fun vehicleDao(): VehicleDao
    abstract fun dtcDao(): DtcDao
    abstract fun chargingSessionDao(): ChargingSessionDao
    abstract fun diagnosticEventDao(): DiagnosticEventDao

    companion object {
        /**
         * Adds the cloud-upload columns.
         *
         * Both are nullable and added WITHOUT a DEFAULT on purpose: Room
         * compares a column's declared default against the entity and fails
         * validation if they differ, and the entity declares none. A nullable
         * column needs no default, so the schema Room expects and the schema
         * SQLite ends up with are identical.
         */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE measurements ADD COLUMN remoteId TEXT")
                db.execSQL("ALTER TABLE measurements ADD COLUMN uploadedAt INTEGER")
            }
        }

        /** Adds the DTC, charging-session and diagnostic-event tables. */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS dtcs (" +
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                        "vehicleId INTEGER NOT NULL, ecu TEXT NOT NULL, code TEXT NOT NULL, " +
                        "status TEXT NOT NULL, firstSeen TEXT NOT NULL, lastSeen TEXT NOT NULL, " +
                        "occurrenceCount INTEGER NOT NULL)"
                )
                db.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS index_dtcs_vehicleId_ecu_code " +
                        "ON dtcs (vehicleId, ecu, code)"
                )
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS charging_sessions (" +
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                        "vehicleId INTEGER NOT NULL, chargeType TEXT, startedAt TEXT NOT NULL, " +
                        "endedAt TEXT, startSoc REAL, endSoc REAL, status TEXT NOT NULL, notes TEXT)"
                )
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS diagnostic_events (" +
                        "id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                        "vehicleId INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL, " +
                        "ecu TEXT, description TEXT NOT NULL, data TEXT)"
                )
            }
        }

        @Volatile private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "odb_scanner.db",
                ).addMigrations(MIGRATION_1_2, MIGRATION_2_3)
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}
