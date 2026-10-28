package com.odbscanner.route

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Turn-by-turn: reading Mapbox's reply (the same sample test_route_traffic.py
 * reads), making ORS cues, and following a drive along the route -- what is
 * said where, lanes, off-route, arrival. Positions run north along the
 * sample's straight road, so "km along" is just latitude.
 */
class NavTest {

    private val sample: JSONObject by lazy {
        // Unit tests run with the module (android/app) as working directory.
        JSONObject(File("../../tests/data/mapbox_route_sample.json").readText(Charsets.UTF_8))
    }

    private val kmPerDegree = EARTH_RADIUS_KM * Math.PI / 180.0
    private fun lat(km: Double) = 51.75 + km / kmPerDegree
    private val roadLon = -1.26

    private fun route() = MapboxParse.routes(sample)[0]

    // -- Reading Mapbox ----------------------------------------------------------------

    @Test
    fun readsRoutesWithTrafficAndAlternatives() {
        val routes = MapboxParse.routes(sample)
        assertEquals(2, routes.size)
        val r = routes[0]
        assertEquals(9.496, r.distanceKm, 0.005)
        assertEquals(600.0, r.durationS, 0.0)
        assertEquals(480.0, r.typicalDurationS!!, 0.0)
        assertEquals(120.0, r.trafficDelayS!!, 0.0)
        assertEquals("A40, M40", r.summary)
        assertEquals(1, r.legEndsKm.size)
        assertEquals(r.distanceKm, r.legEndsKm[0], 0.001)
        assertEquals("A44, B4027", routes[1].summary)
        assertEquals(20.0, routes[1].trafficDelayS!!, 0.0)
        assertTrue(routes[1].congestion.isEmpty())        // low and moderate only
    }

    @Test
    fun heavyTrafficIsOneRunAtItsWorstLevel() {
        val r = route()
        // Segments 2-4 (heavy, heavy, severe) of 8: from point 2 to point 5.
        assertEquals(1, r.congestion.size)
        val run = r.congestion[0]
        assertEquals("severe", run.level)
        assertEquals(r.cumKm[2], run.fromKm, 1e-9)
        assertEquals(r.cumKm[5], run.toKm, 1e-9)
        assertEquals(run.toKm - run.fromKm, r.heavyKm, 1e-9)
        assertEquals(run, r.trafficAhead(0.5, 3.0))
        assertNull(r.trafficAhead(0.5, 1.0))               // starts 1.9 km on
        assertEquals(run, r.trafficAhead(4.0, 1.0))        // in it
        assertNull(r.trafficAhead(6.5, 5.0))               // passed
    }

    @Test
    fun maneuversTakeTheCuesAndLanesOfTheStepBefore() {
        val m = route().maneuvers
        assertEquals(listOf("off ramp", "fork", "arrive"), m.map { it.type })   // no depart
        val scale = route().distanceKm / 9.5
        assertEquals(2.0 * scale, m[0].atKm, 1e-9)
        assertEquals(8.0 * scale, m[1].atKm, 1e-9)
        assertEquals(9.5 * scale, m[2].atKm, 1e-9)
        assertEquals(listOf(120.0, 540.0, 600.0), m.map { it.atS })

        val ramp = m[0]
        assertEquals("slight left", ramp.modifier)
        assertEquals("M40", ramp.road)                     // ref before name
        assertEquals("8", ramp.exit)
        assertEquals(listOf(true, true, false), ramp.lanes.map { it.active })
        assertEquals(listOf("straight", "slight left"), ramp.lanes[1].directions)
        // Cues: dAG metres before the manoeuvre, on the geometry's scale.
        assertEquals(2, ramp.cues.size)
        assertEquals(0.0, ramp.cues[0].atKm, 1e-6)
        assertEquals((2.0 - 0.4) * scale, ramp.cues[1].atKm, 1e-9)
        assertTrue(ramp.cues[1].text.startsWith("In a quarter of a mile, take the ramp"))

        val fork = m[1]
        assertEquals("Thornhill Way", fork.road)          // no ref: the name
        assertEquals("", fork.exit)
        assertEquals(4, fork.lanes.size)
        assertEquals((8.0 - 1.609) * scale, fork.cues[0].atKm, 1e-9)
        assertTrue(m[2].lanes.isEmpty())
    }

    @Test
    fun timeLeftFollowsEachStretchsSpeed() {
        val r = route()
        assertEquals(0.0, r.timeAt(0.0), 1e-9)
        assertEquals(120.0, r.timeAt(r.maneuvers[0].atKm), 1e-9)
        // Halfway along the slow motorway stretch: halfway between 120 s and 540 s.
        val mid = (r.maneuvers[0].atKm + r.maneuvers[1].atKm) / 2
        assertEquals(330.0, r.timeAt(mid), 1e-6)
        assertEquals(600.0, r.timeAt(r.distanceKm + 1.0), 1e-9)
    }

    @Test
    fun errorCodeIsARoutingException() {
        val e = runCatching {
            MapboxParse.routes(JSONObject("""{"code":"NoRoute","message":"No route found"}"""))
        }.exceptionOrNull()
        assertTrue(e is RoutingException)
        assertTrue(e!!.message!!.contains("No route found"))
    }

    @Test
    fun requestAsksForTrafficLanesAndVoiceInBritishUnits() {
        val two = listOf(51.75 to -1.26, 51.8354 to -1.26)
        val url = MapboxRouting.url(two, Avoid(motorways = true, tolls = true), true, "tok en")
        assertTrue(url.startsWith(
            "https://api.mapbox.com/directions/v5/mapbox/driving-traffic/" +
                "-1.260000,51.750000;-1.260000,51.835400?"))
        for (part in listOf("overview=full", "steps=true", "banner_instructions=true",
                "voice_instructions=true", "voice_units=british_imperial",
                "annotations=congestion", "alternatives=true", "exclude=motorway,toll",
                "access_token=tok+en")) {
            assertTrue(part, url.contains(part))
        }
        // Via a charging stop: Mapbox gives alternatives only between two points.
        val via = MapboxRouting.url(listOf(two[0], 51.8 to -1.27, two[1]), Avoid(), true, "t")
        assertFalse(via.contains("alternatives"))
        assertFalse(via.contains("exclude"))
    }

    @Test
    fun avoidNamesForBothServices() {
        val a = Avoid(motorways = true, tolls = false, ferries = true)
        assertEquals(listOf("highways", "ferries"), a.ors)
        assertEquals(listOf("motorway", "ferry"), a.mapbox)
        assertTrue(Avoid().ors.isEmpty())
    }

    // -- Speech ---------------------------------------------------------------------------

    @Test
    fun britishDistances() {
        assertEquals("100 yards", NavSpeech.distance(0.1))
        assertEquals("50 yards", NavSpeech.distance(0.01))
        assertEquals("a quarter of a mile", NavSpeech.distance(0.4))
        assertEquals("half a mile", NavSpeech.distance(0.8))
        assertEquals("three quarters of a mile", NavSpeech.distance(1.2))
        assertEquals("1 mile", NavSpeech.distance(1.609))
        assertEquals("1 and a half miles", NavSpeech.distance(2.4))
        assertEquals("3 miles", NavSpeech.distance(4.83))
        assertEquals("12 miles", NavSpeech.distance(19.3))
        assertEquals("In a quarter of a mile, turn left onto Station Road.",
            NavSpeech.lead(0.4, "Turn left onto Station Road."))
    }

    @Test
    fun laneAdvice() {
        fun lanes(vararg on: Boolean) = on.map { Lane(listOf("straight"), it) }
        assertEquals("Use the left two lanes.", NavSpeech.laneAdvice(lanes(true, true, false)))
        assertEquals("Use the right lane.", NavSpeech.laneAdvice(lanes(false, false, true)))
        assertEquals("Use the left lane.", NavSpeech.laneAdvice(lanes(true, false)))
        assertEquals("Use the right three lanes.",
            NavSpeech.laneAdvice(lanes(false, true, true, true)))
        assertEquals("Use the second lane from the left.",
            NavSpeech.laneAdvice(lanes(false, true, false)))
        assertEquals("Use the middle lanes.", NavSpeech.laneAdvice(lanes(false, true, true, false)))
        assertNull(NavSpeech.laneAdvice(lanes(true, true)))           // any lane will do
        assertNull(NavSpeech.laneAdvice(lanes(false, false)))         // no advice given
        assertNull(NavSpeech.laneAdvice(emptyList()))
    }

    @Test
    fun trafficSpeech() {
        val run = route().congestion[0]                  // 2.38 to 5.95 km, severe
        assertEquals("Very heavy traffic in 1 and a half miles, for about 2 miles.",
            NavSpeech.traffic(run, 0.0))
        assertEquals("Very heavy traffic ahead for about 2 miles.", NavSpeech.traffic(run, 3.0))
        assertEquals("Heavy traffic ahead for about 2 miles.",
            NavSpeech.traffic(run.copy(level = "heavy"), 3.0))
    }

    // -- Following the drive ---------------------------------------------------------------

    @Test
    fun drivingTheRouteSaysEachCueOnceWithLanesNearTheJunction() {
        val g = Guidance(route())
        fun at(km: Double) = g.update(lat(km), roadLon)

        val start = at(0.0)
        assertEquals("Drive north on Banbury Road, then take the ramp on the left towards the M40.",
            start.say)                                   // > 1 km out: no lane advice yet
        assertEquals("off ramp", start.next?.type)
        assertEquals(3, start.lanes.size)                // within 2 km: lanes on screen
        assertNull(at(1.0).say)

        assertEquals("In a quarter of a mile, take the ramp on the left towards the M40. " +
            "Use the left two lanes.", at(1.62).say)
        assertNull(at(1.65).say)                         // once only

        val motorway = at(3.0)
        assertEquals("fork", motorway.next?.type)
        assertTrue(motorway.lanes.isEmpty())             // 5 km out
        assertEquals(0, motorway.leg)

        assertEquals("In 1 mile, keep left at the fork towards Thornhill.", at(6.4).say)
        val fork = at(7.8)
        assertEquals("Keep left at the fork towards Thornhill. Use the left two lanes.", fork.say)
        assertEquals(600.0 - route().timeAt(7.8), fork.remainingS, 1e-6)
        assertEquals(route().distanceKm - 7.8, fork.remainingKm, 0.002)
        assertFalse(fork.arrived)

        // Straight to 25 m from the end: the 300-yard cue is long passed and stays
        // quiet; the arrival cue is due.
        val end = at(route().distanceKm - 0.025)
        assertEquals("You have arrived at your destination.", end.say)
        assertTrue(end.arrived)
        assertNull(end.next?.takeIf { it.type != "arrive" })
    }

    @Test
    fun offRouteAfterThreeFixesAndQuietMeanwhile() {
        val g = Guidance(route())
        g.update(lat(3.0), roadLon)
        val off = roadLon + 0.002                        // ~140 m east of the road
        val first = g.update(lat(3.1), off)
        assertTrue(first.offRouteKm > Guidance.OFF_ROUTE_KM)
        assertFalse(first.offRoute)
        assertFalse(g.update(lat(3.2), off).offRoute)
        val third = g.update(lat(6.45), off)             // the 1-mile cue is due, but off route
        assertTrue(third.offRoute)
        assertNull(third.say)
        val back = g.update(lat(6.5), roadLon)
        assertFalse(back.offRoute)
        assertEquals("In 1 mile, keep left at the fork towards Thornhill.", back.say)
    }

    @Test
    fun snapFindsTheRoadAgainAfterAJump() {
        val g = Guidance(route())
        g.update(lat(0.5), roadLon)
        val (km, off) = g.snap(lat(9.0), roadLon)
        assertEquals(9.0, km, 0.002)
        assertEquals(0.0, off, 0.001)
    }

    @Test
    fun aRouteSwappedInOnTheSameRoadsStartsQuiet() {
        val g = Guidance(route(), quietUntilKm = 0.02)
        assertNull(g.update(lat(0.0), roadLon).say)        // the opening words: already heard
        assertTrue(g.update(lat(1.62), roadLon).say!!.startsWith("In a quarter of a mile"))
    }

    @Test
    fun sameRoadsOrAnotherWay() {
        val r = route()
        val alt = MapboxParse.routes(sample)[1]           // west through (-1.28, 51.79)
        assertTrue(r.follows(r, 0.0))
        assertFalse(r.follows(alt, 0.0))
        // From halfway, the second half of the same road still follows.
        val rest = NavRoute(r.points.drop(4), emptyList(), 300.0, null, emptyList(),
            emptyList(), "", "test")
        assertTrue(r.follows(rest, 4.5))
    }

    @Test
    fun pointAndHeadingAlongTheRoad() {
        val r = route()
        val (la, lo) = r.pointAt(3.0)
        assertEquals(lat(3.0), la, 1e-9)
        assertEquals(roadLon, lo, 1e-12)
        assertEquals(0.0, r.bearingAt(3.0), 0.01)          // due north
        val alt = MapboxParse.routes(sample)[1]
        assertTrue(alt.bearingAt(1.0) in 270.0..360.0)     // north-west at first
        assertTrue(alt.bearingAt(alt.distanceKm - 1.0) in 0.0..90.0)   // then north-east
        assertEquals(r.points.last().lat, r.pointAt(99.0).first, 1e-12)
    }

    @Test
    fun shownDistancesAndArrows() {
        assertEquals("150 yd", NavText.distance(0.137))
        assertEquals("0.3 mi", NavText.distance(0.5))
        assertEquals("12 mi", NavText.distance(19.3))
        val m = route().maneuvers
        assertEquals("↖", NavText.arrow(m[0]))             // off ramp, slight left
        assertEquals("⚑", NavText.arrow(m[2]))
        assertEquals("↖", NavText.lane(m[0].lanes[1], m[0].modifier))   // straight or slight left
        assertEquals("↑", NavText.lane(m[0].lanes[2], m[0].modifier))   // not this one
        assertEquals("turn" to "right", OrsNav.kind(1))
        assertEquals("fork" to "slight left", OrsNav.kind(12))
        assertEquals("roundabout" to "", OrsNav.kind(7))
    }

    @Test
    fun assistantStepsComeFromTheManoeuvres() {
        val steps = route().steps()
        assertEquals(3, steps.size)
        assertEquals("Take exit 8 on the left towards the M40.", steps[0].instruction)
        assertEquals(8, steps[0].exitNumber)
        assertEquals(route().maneuvers[1].atKm - route().maneuvers[0].atKm, steps[0].lengthKm, 1e-9)
    }

    // -- OpenRouteService steps -----------------------------------------------------------

    @Test
    fun orsStepsGetCuesMadeHere() {
        val pts = (0..50).map { RoutePoint(lat(it * 0.1), roadLon) }   // 5 km north
        val steps = listOf(
            RouteStep(0.0, 3.0, "Head north on Banbury Road", "Banbury Road", null, type = 11),
            RouteStep(3.0, 2.0, "Turn left onto Kidlington Road", "Kidlington Road", null, type = 0),
            RouteStep(5.0, 0.0, "Arrive at Kidlington Road, on the right", "Kidlington Road",
                null, type = 10),
        )
        val nav = OrsNav.route(Route(pts, 5.0, 400.0, steps), listOf(5.0))
        assertEquals(listOf("depart", "turn", "arrive"), nav.maneuvers.map { it.type })
        assertEquals("left", nav.maneuvers[1].modifier)
        assertEquals("Head north on Banbury Road.", nav.maneuvers[0].cues.single().text)
        val turn = nav.maneuvers[1].cues
        assertEquals(listOf(1.4, 2.6, 2.94), turn.map { Math.round(it.atKm * 100) / 100.0 })
        assertEquals("In 1 mile, turn left onto Kidlington Road.", turn[0].text)
        assertEquals("Turn left onto Kidlington Road.", turn[2].text)
        assertEquals("Arrive at Kidlington Road, on the right.", nav.maneuvers[2].cues.last().text)
        assertNull(nav.trafficDelayS)                     // no traffic from ORS

        val g = Guidance(nav)
        assertEquals("Head north on Banbury Road.", g.update(lat(0.0), roadLon).say)
        assertEquals("In 1 mile, turn left onto Kidlington Road.", g.update(lat(1.45), roadLon).say)
        assertNotNull(g.update(lat(2.95), roadLon).say)
    }
}
