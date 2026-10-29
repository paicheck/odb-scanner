package com.odbscanner.db

import androidx.room.Room
import androidx.room.testing.MigrationTestHelper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The measurement queries the screens, the trend chart, the assistant and the
 * uploader rely on, against real SQLite; and the 3 -> 4 migration that indexes
 * them, against the committed schema -- a broken migration would otherwise
 * stop the app opening its own database at the user's first start.
 */
@RunWith(AndroidJUnit4::class)
class MeasurementDaoDeviceTest {

    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(), AppDatabase::class.java,
    )

    private lateinit var db: AppDatabase
    private val dao get() = db.measurementDao()

    @Before
    fun open() {
        db = Room.inMemoryDatabaseBuilder(
            InstrumentationRegistry.getInstrumentation().targetContext, AppDatabase::class.java,
        ).build()
    }

    @After
    fun close() = db.close()

    private suspend fun add(
        key: String, value: Double?, ok: Boolean = true, vehicle: Long = 1,
        uploadedAt: Long? = null, remoteId: String? = "r",
    ) = dao.insert(
        MeasurementEntity(
            vehicleId = vehicle, ts = "2026-10-28T10:00:00Z", ecu = "bat_mgmt",
            service = "UDS-0x22", pid = "0x1E3B", key = key, value = value,
            textValue = null, unit = "V", provenance = "reported",
            decodingMethod = "builtin", docStatus = null, rawResponse = "",
            success = ok, error = if (ok) null else "NO DATA",
            remoteId = remoteId?.let { "$it-$key-${System.nanoTime()}" },
            uploadedAt = uploadedAt,
        ),
    )

    @Test
    fun valueHistoryIsTheNewestValuesOldestFirst() = runBlocking {
        for (i in 0 until 10) add("soc", i.toDouble())
        add("soc", null, ok = false)
        add("other", 99.0)
        assertEquals(listOf(7.0, 8.0, 9.0), dao.valueHistory(1, "soc", 3).map { it.value })
    }

    @Test
    fun latestPerKeyIsEachKeysNewestSuccessForThisVehicle() = runBlocking {
        add("a", 1.0)
        add("b", 2.0)
        add("a", 3.0)
        add("a", null, ok = false)                 // newest a failed
        add("never", null, ok = false)
        add("a", 50.0, vehicle = 2)
        add("only_other_car", 1.0, vehicle = 2)
        val latest = dao.latestPerKey(1).associate { it.key to it.value }
        assertEquals(mapOf("a" to 3.0, "b" to 2.0), latest)
        assertEquals(listOf("a", "b"), dao.latestPerKey(1).map { it.key })  // newest first
    }

    @Test
    fun theUploadQueueIsTheUnuploadedRowsInOrder() = runBlocking {
        add("a", 1.0, uploadedAt = 5)
        val second = add("a", 2.0)
        add("a", 3.0, remoteId = null)             // no id yet: not uploadable
        val fourth = add("b", 4.0)
        assertEquals(listOf(second, fourth), dao.pendingUpload(10).map { it.id })
        assertEquals(2, dao.pendingUploadCount())
    }

    @Test
    fun migrationThreeToFourKeepsTheRowsAndAddsTheIndexes() {
        helper.createDatabase(TEST_DB, 3).apply {
            execSQL(
                "INSERT INTO measurements (vehicleId, ts, ecu, service, pid, key, value, " +
                    "textValue, unit, provenance, decodingMethod, docStatus, rawResponse, " +
                    "success, error, remoteId, uploadedAt) VALUES (1, '2026-10-01T00:00:00Z', " +
                    "'bat_mgmt', 'UDS-0x22', '0x1E3B', 'pack_voltage', 401.5, NULL, 'V', " +
                    "'reported', 'builtin', NULL, '0FAF', 1, NULL, 'id-1', NULL)",
            )
            close()
        }
        val migrated = helper.runMigrationsAndValidate(
            TEST_DB, 4, true, AppDatabase.MIGRATION_3_4,
        )
        migrated.query("SELECT key, value FROM measurements").use {
            assertTrue(it.moveToFirst())
            assertEquals("pack_voltage", it.getString(0))
            assertEquals(401.5, it.getDouble(1), 1e-9)
        }
        val indexes = mutableSetOf<String>()
        migrated.query("PRAGMA index_list(measurements)").use {
            while (it.moveToNext()) indexes.add(it.getString(it.getColumnIndexOrThrow("name")))
        }
        assertTrue(indexes.toString(), indexes.containsAll(
            setOf("index_measurements_vehicleId_key", "index_measurements_uploadedAt")))
        migrated.close()
    }

    private companion object {
        const val TEST_DB = "migration-test.db"
    }
}
