package com.odbscanner.route

import android.Manifest
import android.content.Context
import android.graphics.Bitmap
import android.location.Location
import android.location.LocationManager
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.odbscanner.MainActivity
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * In-app navigation on a device, without a car or a network: the shared
 * Mapbox sample (tests/data, as NavTest) is the planned route, and the GPS is
 * a test provider fed points along it. Checks what is said where, lanes,
 * the heavy-traffic warning, off-route, arrival -- through the real location
 * pipeline, Navigator and text-to-speech -- and saves a screenshot of the
 * navigation screen (nav_screen.png in the app's files) to look at.
 *
 * Run on an emulator: ./gradlew :app:connectedDebugAndroidTest
 */
@RunWith(AndroidJUnit4::class)
class NavigatorDeviceTest {
    private val inst = InstrumentationRegistry.getInstrumentation()
    private val ctx: Context = inst.targetContext
    private val lm = ctx.getSystemService(LocationManager::class.java)
    private lateinit var route: NavRoute
    private lateinit var trip: TripResult
    private val kmPerDegree = EARTH_RADIUS_KM * Math.PI / 180.0

    private fun shell(cmd: String) {
        ParcelFileDescriptor.AutoCloseInputStream(inst.uiAutomation.executeShellCommand(cmd))
            .use { it.readBytes() }
    }

    @Before
    fun setUp() {
        for (p in listOf(Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION)) {
            inst.uiAutomation.grantRuntimePermission(ctx.packageName, p)
        }
        shell("appops set ${ctx.packageName} android:mock_location allow")
        runCatching { lm.removeTestProvider(LocationManager.GPS_PROVIDER) }
        @Suppress("DEPRECATION")
        lm.addTestProvider(LocationManager.GPS_PROVIDER, false, false, false, false, true, true,
            true, android.location.Criteria.POWER_LOW, android.location.Criteria.ACCURACY_FINE)
        lm.setTestProviderEnabled(LocationManager.GPS_PROVIDER, true)

        val sample = inst.context.assets.open("mapbox_route_sample.json").use {
            it.readBytes().toString(Charsets.UTF_8)
        }
        route = MapboxParse.routes(JSONObject(sample))[0]
        val plan = Plan("direct", route.distanceKm, route.durationS, 80.0, 78.0, emptyList(),
            listOf(0.0 to 80.0, route.distanceKm to 78.0), null, 0.0, 0.0, "balanced", emptyList())
        trip = TripResult(Place("Banbury Road", 51.75, -1.26), Place("Thornhill", 51.8354, -1.26),
            plan, CarEnergy(58.0, "test", 80.0, null, "test"), 17.0, MapMath.thinLine(route.points),
            route.steps(), listOf(route), 0)
        // No keys on purpose: a re-route must fail politely, and nothing here
        // may reach a real service. (An emulator only: the run uninstalls the app.)
        RoutePrefs.save(ctx, RoutePrefs.load(ctx).copy(voiceGuidance = true, navigateInApp = true,
            mapboxToken = "", orsKey = "", ocmKey = ""))
    }

    @After
    fun tearDown() {
        inst.runOnMainSync { TripMonitor.stop() }
        runCatching { lm.removeTestProvider(LocationManager.GPS_PROVIDER) }
    }

    private fun lat(km: Double) = 51.75 + km / kmPerDegree

    /** One GPS fix; then wait until the Navigator has placed it. */
    private fun drive(km: Double, lonOffset: Double = 0.0): Navigator.State {
        val l = Location(LocationManager.GPS_PROVIDER).apply {
            latitude = lat(km)
            longitude = -1.26 + lonOffset
            accuracy = 4f
            altitude = 70.0
            speed = 25f
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
        }
        lm.setTestProviderLocation(LocationManager.GPS_PROVIDER, l)
        val until = SystemClock.elapsedRealtime() + 8_000
        while (SystemClock.elapsedRealtime() < until) {
            val s = Navigator.state.value
            val f = s?.fix
            if (f != null && Math.abs(f.first - l.latitude) < 1e-7 &&
                Math.abs(f.second - l.longitude) < 1e-7) {
                // All this fix set off has run by now (it is one main-thread
                // pass), and the next fix keeps the update interval.
                SystemClock.sleep(1_100)
                return Navigator.state.value!!
            }
            SystemClock.sleep(50)
        }
        throw AssertionError("the Navigator never saw the fix at km $km")
    }

    @Test
    fun drivesTheRouteSpeakingTurnsLanesAndTraffic() {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            inst.runOnMainSync {
                TripMonitor.drive(ctx, trip)
                Navigator.showOnPhone.value = true
            }
            val start = drive(0.0)
            assertEquals(Navigator.Phase.DRIVING, start.phase)
            assertEquals("off ramp", start.guidance?.next?.type)
            val said = { Navigator.said.value }
            assertTrue(said().toString(), said().first().startsWith("Starting navigation to Thornhill"))
            assertTrue(said().toString(), said().any { it.startsWith("Drive north on Banbury Road") })
            // The jam 2.4 km on is under 3 km away: warned once.
            assertTrue(said().toString(), said().any { it.contains("Very heavy traffic in") })
            assertTrue(said().any { it.startsWith("Traffic adds about 2 minutes") }.not())   // < 5 min

            val ramp = drive(1.62)
            assertEquals(3, ramp.guidance?.lanes?.size)
            assertEquals("In a quarter of a mile, take the ramp on the left towards the M40. " +
                "Use the left two lanes.", said().last())

            drive(3.0)
            // A look at the screen at speed on the motorway, the jam drawn ahead.
            SystemClock.sleep(2_500)                        // map tiles, if the network allows
            inst.uiAutomation.takeScreenshot()?.let { shot ->
                File(ctx.filesDir, "nav_screen.png").outputStream().use {
                    shot.compress(Bitmap.CompressFormat.PNG, 100, it)
                }
            }

            drive(6.4)
            assertEquals("In 1 mile, keep left at the fork towards Thornhill.", said().last())

            // Off the road: three fixes, then a new route is asked for -- with no
            // key here it cannot come, and the note says so instead of crashing.
            for (k in listOf(6.5, 6.55, 6.6)) drive(k, lonOffset = 0.003)
            val off = Navigator.state.value!!
            assertTrue(off.guidance!!.offRoute)
            SystemClock.sleep(1_500)
            val note = Navigator.state.value!!.note
            assertTrue(note, note.startsWith("No route yet") || note == "Re-routing")

            // Back on it and on to the end.
            drive(7.8)
            assertEquals("Keep left at the fork towards Thornhill. Use the left two lanes.",
                said().last())
            val end = drive(route.distanceKm - 0.02)
            assertEquals(Navigator.Phase.ARRIVED, end.phase)
            assertEquals("You have arrived at your destination.", said().last())
            assertNotNull(TripMonitor.state.value)          // the trip ends itself after a moment
        } finally {
            scenario.close()
        }
        inst.runOnMainSync { TripMonitor.stop() }
        SystemClock.sleep(300)
        assertNull(Navigator.state.value)
    }
}
