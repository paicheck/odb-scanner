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
 * Every version step ships a real migration (1 -> 2 adds the cloud-upload
 * columns, 2 -> 3 the DTC, charging-session and event tables), and there is NO
 * destructive fallback. The fallback used to cover every other version change
 * -- including a downgrade when an older APK is installed -- by dropping every
 * table, so the un-uploaded measurements and the tx_log safety manifest were
 * silently wiped. A missing migration now fails loudly at open instead: the
 * data stays on disk and the next build can migrate it.
 *
 * The schema is exported to app/schemas/ (committed), so each version's shape
 * is on record and a future migration can be tested against the real one.
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
    exportSchema = true,
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
                    .build()
                    .also { instance = it }
            }
    }
}
