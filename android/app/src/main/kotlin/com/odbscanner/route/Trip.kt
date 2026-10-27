package com.odbscanner.route

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import androidx.core.content.ContextCompat
import com.odbscanner.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Locale

/** Route settings: the two free API keys and the driver's planning margins. */
data class RouteSettings(
    val orsKey: String,
    val ocmKey: String,
    val reservePct: Double,
    val chargeToPct: Double,
    val consumptionKwhPer100km: Double?,   // null: this car's figure or the default
    val countries: String,                 // place search limit, "GB,FR"; "" = anywhere
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
    fun permitted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context,
            android.Manifest.permission.ACCESS_COARSE_LOCATION) ==
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
}

data class TripResult(
    val origin: Place,
    val destination: Place,
    val plan: Plan,
    val car: CarEnergy,
    val consumptionKwhPer100km: Double,
    val line: List<Pair<Double, Double>>,    // the road, thinned, for the map
)

/**
 * Geocode, route, plan: the same steps as the dashboard's Route page
 * (web/routes/route.py). Blocking network -- runs on Dispatchers.IO.
 */
object TripPlanner {
    suspend fun plan(
        context: Context,
        to: Place,
        from: Place?,
        startPct: Double? = null,
    ): TripResult = withContext(Dispatchers.IO) {
        val s = RoutePrefs.load(context)
        val car = CarEnergy.load(context)
        val origin = from ?: PhoneLocation.lastKnown(context)
            ?.let { Place("Your location", it.latitude, it.longitude) }
            ?: throw RoutingException(
                "No location for the start: allow location access, or enter a start.")
        val services = RouteServices(s.orsKey, s.ocmKey, countries = s.countries)
        val route = services.route(origin.lat to origin.lon, to.lat to to.lon)
        val consumption = s.consumptionKwhPer100km ?: CarEnergy.DEFAULT_CONSUMPTION
        val plan = Planner.planTrip(
            route.points, route.durationS, startPct ?: car.startPct,
            CarModel(usableKwh = car.usableKwh, consumptionKwhPerKm = consumption / 100.0),
            PlanSettings(reservePct = s.reservePct, chargeToPct = s.chargeToPct),
            ChargerLookup { lat, lon, r -> services.chargersNear(lat, lon, r) },
        )
        TripResult(origin, to, plan, car, consumption, MapMath.thinLine(route.points))
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
