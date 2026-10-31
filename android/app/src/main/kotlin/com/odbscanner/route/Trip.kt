package com.odbscanner.route

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import com.odbscanner.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume
import java.util.Locale

/** Route settings: the API keys, the driver's planning margins, how to navigate. */
data class RouteSettings(
    val orsKey: String,
    val ocmKey: String,
    val reservePct: Double,
    val chargeToPct: Double,
    val consumptionKwhPer100km: Double?,   // null: this car's figure or the default
    val countries: String,                 // place search limit, "GB,FR"; "" = anywhere
    val mode: String = "balanced",         // fastest | balanced | cheapest
    val timeValuePerHour: Double = 15.0,
    val defaultPricePerKwh: Double = 0.79,
    val ownPrices: String = "",            // "Ionity = 0.39" lines
    val mapboxToken: String = "",          // optional: live traffic, lanes, spoken turns
    val avoid: Avoid = Avoid(),
    val navigateInApp: Boolean = true,     // false: hand the trip to Google Maps
    val voiceGuidance: Boolean = true,
    val preferNetworks: String = "",       // "Ionity, Gridserve": favoured, real price shown
    val avoidNetworks: String = "",        // never used
    val publicOnly: Boolean = false,       // leave out members-only chargers
    val weather: Boolean = true,           // the forecast wind, from Open-Meteo (Weather.kt)
) {
    val missingKeys: List<String>
        get() = listOfNotNull(
            "OpenRouteService".takeIf { orsKey.isBlank() },
            "OpenChargeMap".takeIf { ocmKey.isBlank() },
        )
}

object RoutePrefs {
    private const val FILE = "route"

    fun load(context: Context): RouteSettings {
        val p = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return RouteSettings(
            orsKey = p.getString("ors_key", "") ?: "",
            ocmKey = p.getString("ocm_key", "") ?: "",
            reservePct = p.getFloat("reserve_pct", 10f).toDouble(),
            chargeToPct = p.getFloat("charge_to_pct", 80f).toDouble(),
            consumptionKwhPer100km = p.getFloat("consumption", -1f).toDouble()
                .takeIf { it > 0 },
            // Until set, the phone's own region: a UK phone searches the UK.
            countries = p.getString("countries", null)
                ?: RouteServices.countryList(Locale.getDefault().country),
            mode = p.getString("mode", "balanced")?.takeIf { it in MODES } ?: "balanced",
            timeValuePerHour = p.getFloat("time_value", 15f).toDouble(),
            defaultPricePerKwh = p.getFloat("default_price", 0.79f).toDouble(),
            ownPrices = p.getString("own_prices", "") ?: "",
            mapboxToken = p.getString("mapbox_token", "") ?: "",
            avoid = Avoid(p.getBoolean("avoid_motorways", false), p.getBoolean("avoid_tolls", false),
                p.getBoolean("avoid_ferries", false)),
            navigateInApp = p.getBoolean("navigate_in_app", true),
            voiceGuidance = p.getBoolean("voice_guidance", true),
            preferNetworks = p.getString("prefer_networks", "") ?: "",
            avoidNetworks = p.getString("avoid_networks", "") ?: "",
            publicOnly = p.getBoolean("public_only", false),
            weather = p.getBoolean("weather", true),
        )
    }

    fun save(context: Context, s: RouteSettings) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("ors_key", s.orsKey.trim())
            .putString("ocm_key", s.ocmKey.trim())
            .putFloat("reserve_pct", s.reservePct.coerceIn(0.0, 50.0).toFloat())
            .putFloat("charge_to_pct", s.chargeToPct.coerceIn(20.0, 100.0).toFloat())
            .putFloat("consumption", (s.consumptionKwhPer100km ?: -1.0).toFloat())
            .putString("countries", RouteServices.countryList(s.countries))
            .putString("mode", s.mode.takeIf { it in MODES } ?: "balanced")
            .putFloat("time_value", s.timeValuePerHour.coerceIn(0.0, 500.0).toFloat())
            .putFloat("default_price", s.defaultPricePerKwh
                .coerceIn(Prices.MIN_PRICE, Prices.MAX_PRICE).toFloat())
            .putString("own_prices", s.ownPrices.take(2000))
            .putString("mapbox_token", s.mapboxToken.trim())
            .putBoolean("avoid_motorways", s.avoid.motorways)
            .putBoolean("avoid_tolls", s.avoid.tolls)
            .putBoolean("avoid_ferries", s.avoid.ferries)
            .putBoolean("navigate_in_app", s.navigateInApp)
            .putBoolean("voice_guidance", s.voiceGuidance)
            .putString("prefer_networks", Planner.names(s.preferNetworks).joinToString(", "))
            .putString("avoid_networks", Planner.names(s.avoidNetworks).joinToString(", "))
            .putBoolean("public_only", s.publicOnly)
            .putBoolean("weather", s.weather)
            .apply()
    }

    /** Recent destinations, newest first: one tap on the car screen. */
    fun recent(context: Context): List<Place> =
        (context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString("recent", "") ?: "")
            .lines().mapNotNull { line ->
                val f = line.split('\t')
                if (f.size != 3) return@mapNotNull null
                val lat = f[1].toDoubleOrNull() ?: return@mapNotNull null
                val lon = f[2].toDoubleOrNull() ?: return@mapNotNull null
                Place(f[0], lat, lon)
            }

    fun remember(context: Context, place: Place, keep: Int = 6) {
        val clean = place.copy(label = place.label.replace('\t', ' ').replace('\n', ' '))
        val list = (listOf(clean) + recent(context).filter { it.label != clean.label }).take(keep)
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("recent", list.joinToString("\n") { "${it.label}\t${it.lat}\t${it.lon}" })
            .apply()
    }
}

/** This car's battery figures as the phone last read them, with their source. */
data class CarEnergy(
    val usableKwh: Double,
    val usableBasis: String,
    val startPct: Double,
    val startTs: String?,
    val startBasis: String,
) {
    companion object {
        const val DEFAULT_USABLE_KWH = 58.0
        const val DEFAULT_CONSUMPTION = 17.0     // kWh/100 km, as routing/car.py

        /** From the newest successful readings; same keys and order as routing/car.py. */
        suspend fun load(context: Context): CarEnergy = withContext(Dispatchers.IO) {
            val db = AppDatabase.get(context)
            val vid = db.vehicleDao().getFirst()?.id
            val dao = db.measurementDao()
            suspend fun latest(key: String) = vid?.let { dao.latestValue(it, key) }
            val max = latest("hv_energy_max")
            val content = latest("hv_energy_content")
            val soc = latest("soc_normal")
            val usable = max?.value?.div(1000.0) ?: DEFAULT_USABLE_KWH
            val usableBasis = if (max != null) "BMS-rated maximum energy"
            else "ID.3 58 kWh nominal (no BMS reading)"
            when {
                content != null -> CarEnergy(usable, usableBasis,
                    (content.value!! / 1000.0 / usable * 100.0).coerceIn(0.0, 100.0),
                    content.ts, "energy content")
                soc != null -> CarEnergy(usable, usableBasis, soc.value!!, soc.ts, "display SoC")
                else -> CarEnergy(usable, usableBasis, 80.0, null, "no reading; assumed")
            }
        }
    }
}

/** Where the trip starts: the phone's own last known position. */
object PhoneLocation {
    fun permitted(context: Context): Boolean = precise(context) ||
        ContextCompat.checkSelfPermission(context,
            android.Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    /** Precise location granted: what following a trip along the road needs. */
    fun precise(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context,
            android.Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    /** Newest last-known fix from any provider; null without permission or fix. */
    @SuppressLint("MissingPermission")
    fun lastKnown(context: Context): Location? {
        if (!permitted(context)) return null
        val lm = context.getSystemService(LocationManager::class.java) ?: return null
        return lm.getProviders(true)
            .mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .maxByOrNull { it.time }
    }

    /**
     * A position no older than [maxAgeMs]: the last-known fix when recent,
     * else a fresh one asked of the fused (or network) provider, waiting up to
     * [timeoutMs]. Null when neither is available -- a stale fix is worse than
     * none for a trip in progress (it reads as "off the route").
     */
    @SuppressLint("MissingPermission")
    suspend fun current(context: Context, maxAgeMs: Long = 3 * 60_000L,
                        timeoutMs: Long = 15_000L): Location? {
        if (!permitted(context)) return null
        fun recent(l: Location?) = l?.takeIf { System.currentTimeMillis() - it.time <= maxAgeMs }
        recent(lastKnown(context))?.let { return it }
        val lm = context.getSystemService(LocationManager::class.java) ?: return null
        val enabled = lm.getProviders(true)
        val provider = listOf("fused", LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
            .firstOrNull { it in enabled } ?: return null
        val fix = withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { cont ->
                val cancel = androidx.core.os.CancellationSignal()
                cont.invokeOnCancellation { cancel.cancel() }
                try {
                    LocationManagerCompat.getCurrentLocation(lm, provider, cancel,
                        ContextCompat.getMainExecutor(context)) { cont.resume(it) }
                } catch (e: SecurityException) {
                    cont.resume(null)
                }
            }
        }
        return recent(fix)
    }
}

data class TripResult(
    val origin: Place,
    val destination: Place,
    val plan: Plan,
    val car: CarEnergy,
    val consumptionKwhPer100km: Double,
    val line: List<Pair<Double, Double>>,    // the road, thinned, for the map
    val steps: List<RouteStep> = emptyList(), // turn-by-turn, for the assistant
    val routes: List<NavRoute> = emptyList(), // the ways there; with Mapbox, in today's traffic
    val chosen: Int = 0,                      // the one planned on
    val avoid: Avoid = Avoid(),
    val notes: List<String> = emptyList(),    // e.g. why there is no live traffic
    val madeAtMs: Long = System.currentTimeMillis(),
) {
    val nav: NavRoute? get() = routes.getOrNull(chosen)
}

/**
 * Geocode, route, plan: the same steps as the dashboard's Route page
 * (web/routes/route.py). Blocking network -- runs on Dispatchers.IO.
 *
 * With a Mapbox token the ways there come in today's traffic (up to three,
 * the fastest first) and the plan uses the [choice]; ORS adds the hills,
 * which Mapbox does not give. Without one, ORS's single route, no traffic.
 */
object TripPlanner {
    suspend fun plan(
        context: Context,
        to: Place,
        from: Place?,
        startPct: Double? = null,
        mode: String? = null,
        consumptionKwhPer100km: Double? = null,
        avoid: Avoid? = null,
        ways: List<NavRoute>? = null,          // the ways a previous plan found: pick another
        choice: Int = 0,
        picks: Picks = Picks(),                // chargers the driver avoided or chose, for this trip
    ): TripResult = withContext(Dispatchers.IO) {
        val s = RoutePrefs.load(context)
        val car = CarEnergy.load(context)
        // Approximate location can be ~10 min old by design; fine for a start.
        val origin = from ?: PhoneLocation.current(context, maxAgeMs = 15 * 60_000L)
            ?.let { Place("Your location", it.latitude, it.longitude) }
            ?: throw RoutingException(
                "No recent location for the start: allow location access, or enter a start.")
        val services = RouteServices(s.orsKey, s.ocmKey, countries = s.countries)
        val keepOff = avoid ?: s.avoid
        val notes = mutableListOf<String>()
        val routes = ways?.takeIf { it.isNotEmpty() } ?: waysThere(s, services, origin, to, keepOff, notes)
        val pick = choice.coerceIn(0, routes.lastIndex)
        val nav = routes[pick]
        // Mapbox gives no heights: ORS adds them to a thinned copy (flat if that fails).
        val points = if (nav.points.any { it.ele != null }) nav.points else services.elevate(nav.points)
        val consumption = consumptionKwhPer100km ?: s.consumptionKwhPer100km
            ?: CarEnergy.DEFAULT_CONSUMPTION
        val settings = PlanSettings(
            reservePct = s.reservePct, chargeToPct = s.chargeToPct,
            mode = mode?.takeIf { it in MODES } ?: s.mode,
            timeValuePerHour = s.timeValuePerHour, defaultPricePerKwh = s.defaultPricePerKwh,
            preferNetworks = Planner.names(s.preferNetworks),
            avoidNetworks = Planner.names(s.avoidNetworks),
            publicOnly = s.publicOnly,
            avoidChargers = picks.avoidChargers,
            useChargers = picks.useChargers,
        )
        val own = Prices.ownPrices(s.ownPrices)
        val chargers = services.chargersAlong(points, settings.searchRadiusKm).map { c ->
            val (price, fee, source) = Prices.priceFor(c.operator, c.usageCost, own,
                settings.defaultPricePerKwh)
            c.copy(pricePerKwh = price, fee = fee, priceSource = source)
        }
        val forecast = if (s.weather) {
            val (conditions, note) = Weather.along(points, nav.durationS)
            notes += note
            conditions ?: emptyList()
        } else emptyList()
        val plan = Planner.planTrip(
            points, nav.durationS, startPct ?: car.startPct,
            CarModel(usableKwh = car.usableKwh, consumptionKwhPerKm = consumption / 100.0),
            settings, chargers, weather = forecast,
        )
        TripResult(origin, to, plan, car, consumption, MapMath.thinLine(nav.points), nav.steps(),
            routes, pick, keepOff, notes)
    }

    /** Mapbox's ways in today's traffic when there is a token (ORS if it fails), else ORS's. */
    private fun waysThere(s: RouteSettings, services: RouteServices, from: Place, to: Place,
                          avoid: Avoid, notes: MutableList<String>): List<NavRoute> {
        val ends = listOf(from.lat to from.lon, to.lat to to.lon)
        if (s.mapboxToken.isNotBlank()) {
            try {
                return MapboxRouting(s.mapboxToken).routes(ends, avoid)
            } catch (e: RoutingException) {
                notes += "No live traffic: ${e.message} This route is from OpenRouteService."
            }
        }
        val r = services.routeVia(ends, avoid)
        return listOf(OrsNav.route(r, r.legEndsKm))
    }

    /** Place search near [near], else near the phone. */
    suspend fun search(context: Context, text: String, near: Place? = null): List<Place> =
        withContext(Dispatchers.IO) {
            val s = RoutePrefs.load(context)
            val focus = near?.let { it.lat to it.lon }
                ?: PhoneLocation.lastKnown(context)?.let { it.latitude to it.longitude }
            RouteServices(s.orsKey, s.ocmKey, countries = s.countries).geocode(text, focus)
        }
}
