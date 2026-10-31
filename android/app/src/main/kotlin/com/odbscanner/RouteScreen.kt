package com.odbscanner

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.odbscanner.route.ActiveTrip
import com.odbscanner.route.CarEnergy
import com.odbscanner.route.MAX_LINK_STOPS
import com.odbscanner.route.MODES
import com.odbscanner.route.NavRoute
import com.odbscanner.route.NavText
import com.odbscanner.route.Navigator
import com.odbscanner.route.PhoneLocation
import com.odbscanner.route.Place
import com.odbscanner.route.RoutePrefs
import com.odbscanner.route.RoutingException
import com.odbscanner.route.TripMonitor
import com.odbscanner.route.TripPlanner
import com.odbscanner.route.TripResult
import com.odbscanner.route.MapsHandoff
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The whole trip to Google Maps in one go: each charging stop, then the
 * destination. Only on the driver's tap -- Google Maps is the one common
 * navigation app that takes stops from a link; without it the link opens in
 * the browser.
 */
fun startTrip(context: Context, r: TripResult) {
    val maps = MapsHandoff.intent(r)
    try {
        context.startActivity(maps)
    } catch (_: ActivityNotFoundException) {
        context.startActivity(Intent(Intent.ACTION_VIEW, maps.data).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

fun fmtDuration(seconds: Double): String {
    val min = Math.round(seconds / 60.0)
    return if (min >= 60) "${min / 60} h %02d".format(min % 60) else "$min min"
}

private fun pct(v: Double) = String.format(Locale.ROOT, "%.0f %%", v)

/** Plain-language one-line summary of a plan, shared with the car screen. */
fun TripResult.headline(): String = when (plan.status) {
    "direct" -> "Arrive with ${pct(plan.arrivalPct!!)}${range()} — no charging needed"
    "with_stops" -> "Arrive with ${pct(plan.arrivalPct!!)}${range()} after ${plan.stops.size} " +
        "charging stop${if (plan.stops.size == 1) "" else "s"}"
    else -> "Not reachable: below the reserve at about " +
        "${Math.round(plan.unreachableAtKm ?: 0.0)} km and no fast charger found before it"
}

/** " (likely 12–19 %)", with a warning when the low end is below the reserve. */
private fun TripResult.range(): String = plan.arrivalBand?.let { b ->
    String.format(Locale.ROOT, " (likely %.0f–%.0f %%%s)", b.lowPct, b.highPct,
        if (b.belowReserve) ", could be below your reserve" else "")
} ?: ""

val MODE_LABELS = mapOf("fastest" to "Fastest", "balanced" to "Balanced", "cheapest" to "Cheapest")

/** "Charging £23.40 · Balanced", shared with the car screen. */
fun TripResult.costLine(): String =
    String.format(Locale.ROOT, "Charging £%.2f · %s", plan.cost, MODE_LABELS[plan.mode] ?: plan.mode)

/** Fastest / Balanced / Cheapest, as three buttons; the chosen one filled. */
@Composable
fun ModeChooser(mode: String, onChange: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
        for (m in MODES) {
            val pad = PaddingValues(horizontal = 4.dp, vertical = 6.dp)
            val label: @Composable () -> Unit = { Text(MODE_LABELS[m] ?: m, maxLines = 1) }
            if (m == mode) {
                Button(onClick = { onChange(m) }, contentPadding = pad,
                    modifier = Modifier.weight(1f)) { label() }
            } else {
                OutlinedButton(onClick = { onChange(m) }, contentPadding = pad,
                    modifier = Modifier.weight(1f)) { label() }
            }
        }
    }
}

/**
 * The phone's route planner: the same plan as the dashboard's Route page
 * (routing/planner.py, ported in route/Planner.kt), started from the phone's
 * location. With a Mapbox token the ways there come in today's traffic, to
 * choose from. The trip is drawn on an OpenStreetMap map; Start navigation
 * navigates it here, turn by turn (or hands it to Google Maps, by setting).
 */
@Composable
fun RouteDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var settings by remember { mutableStateOf(RoutePrefs.load(context)) }
    var to by remember { mutableStateOf("") }
    var from by remember { mutableStateOf("") }
    var startPct by remember { mutableStateOf("") }
    var carNote by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<TripResult?>(null) }
    var showSettings by remember { mutableStateOf(settings.missingKeys.isNotEmpty()) }
    var mode by remember { mutableStateOf(settings.mode) }
    val active by TripMonitor.state.collectAsState()

    LaunchedEffect(Unit) {
        val car = CarEnergy.load(context)
        startPct = String.format(Locale.ROOT, "%.0f", car.startPct)
        carNote = "Start charge from ${car.startBasis}" +
            (car.startTs?.let { " ($it)" } ?: "") + "; battery " +
            String.format(Locale.ROOT, "%.1f kWh", car.usableKwh) + " (${car.usableBasis})."
    }

    /** Plan; with [ways] (the last plan) and [choice], the same trip another way. */
    fun plan(ways: TripResult? = null, choice: Int = 0) {
        busy = true
        error = null
        if (ways == null) result = null
        scope.launch {
            try {
                val origin: Place? = ways?.origin ?: if (from.isBlank()) null else
                    TripPlanner.search(context, from.trim()).firstOrNull()
                        ?: throw RoutingException("No place found for “${from.trim()}”.")
                val dest = ways?.destination
                    ?: TripPlanner.search(context, to.trim(), near = origin).firstOrNull()
                    ?: throw RoutingException("No place found for “${to.trim()}”.")
                result = TripPlanner.plan(context, dest, origin,
                    startPct.toDoubleOrNull()?.takeIf { it in 1.0..100.0 }, mode,
                    avoid = ways?.avoid, ways = ways?.routes, choice = choice)
                RoutePrefs.remember(context, dest)        // offered on the car screen
            } catch (e: RoutingException) {
                error = e.message
            } catch (e: Exception) {
                error = "Planning failed (${e.javaClass.simpleName}: ${e.message})"
            } finally {
                busy = false
            }
        }
    }

    val askLocation = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { plan() }   // planned either way; without it the start must be typed

    // One window at a time: shown together, the planner's dialog window lands
    // on top of Settings and hides it, so Settings seemed not to open.
    if (showSettings) {
        RouteSettingsDialog(onDone = {
            settings = RoutePrefs.load(context)
            showSettings = false
        })
        return
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Route planner") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                active?.let { ActiveTripBanner(it) }
                if (settings.missingKeys.isNotEmpty()) {
                    Text("Needs free API keys for ${settings.missingKeys.joinToString(" and ")} " +
                        "(Settings below).", color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall)
                }
                OutlinedTextField(value = to, onValueChange = { to = it.take(200) },
                    label = { Text("To") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = from, onValueChange = { from = it.take(200) },
                    label = { Text("From (blank: your location)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = startPct, onValueChange = { startPct = it.take(5) },
                    label = { Text("Start charge %") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth())
                Text(carNote, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 4.dp))
                ModeChooser(mode) {
                    mode = it
                    settings = settings.copy(mode = it)
                    RoutePrefs.save(context, settings)             // the next trip's default
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        enabled = !busy && to.isNotBlank() && settings.missingKeys.isEmpty(),
                        onClick = {
                            if (from.isBlank() && !PhoneLocation.permitted(context)) {
                                askLocation.launch(Manifest.permission.ACCESS_COARSE_LOCATION)
                            } else {
                                plan()
                            }
                        },
                    ) { Text(if (busy) "Planning…" else "Plan") }
                    TextButton(onClick = { showSettings = true }) { Text("Settings") }
                }
                error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 6.dp))
                }
                // For the trip being driven, the newer of its latest (re-planned)
                // plan and one made here -- another way chosen, say; a plan for
                // somewhere else shows instead.
                val mine = result
                val driven = active?.result
                val shown = when {
                    mine == null -> driven
                    driven == null || driven.destination != mine.destination -> mine
                    else -> if (driven.madeAtMs > mine.madeAtMs) driven else mine   // the newer
                }
                shown?.let { r ->
                    TripResultView(r, busy = busy,
                        onChoose = { i -> plan(ways = r, choice = i) },
                        onNavigate = onDismiss)          // the directions take the screen
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

/** The trip being driven: what the monitor last saw, Re-plan now, End trip. */
@Composable
private fun ActiveTripBanner(t: ActiveTrip) {
    val context = LocalContext.current
    Column(modifier = Modifier.padding(bottom = 8.dp)) {
        Text("Trip to ${t.destination.label}", fontWeight = FontWeight.SemiBold)
        Text(t.note + (if (t.replans > 0) " · re-planned ${t.replans}×" else ""),
            style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { TripMonitor.replanNow(context) }) { Text("Re-plan now") }
            TextButton(onClick = { TripMonitor.stop() }) { Text("End trip") }
        }
        HorizontalDivider()
    }
}

/** Watch the trip and navigate it here -- or, by setting, in Google Maps. */
private fun beginTrip(context: Context, r: TripResult, onNavigate: () -> Unit) {
    if (TripMonitor.drive(context, r)) {
        Navigator.showOnPhone.value = true
        onNavigate()
    } else {
        startTrip(context, r)
    }
}

/** "M40, A34 · 1 h 05 · 98 km · traffic +12 minutes · 1.9 mi heavy" */
private fun NavRoute.optionLine(): String = buildString {
    if (summary.isNotBlank()) append(summary).append(" · ")
    append(fmtDuration(durationS)).append(" · ")
    append(String.format(Locale.ROOT, "%.0f km", distanceKm))
    trafficDelayS?.let { d ->
        append(if (d >= 60) " · traffic +${Navigator.minutes(d)}" else " · light traffic")
    }
    if (heavyKm >= 0.5) append(" · ").append(NavText.distance(heavyKm)).append(" heavy")
}

/** What starting a trip asks for and does not have yet: precise location
 *  (to follow the trip) and notifications (to say when the stops change). */
private fun tripPermissionsMissing(context: Context): List<String> = buildList {
    if (!PhoneLocation.precise(context)) {
        add(Manifest.permission.ACCESS_FINE_LOCATION)
        add(Manifest.permission.ACCESS_COARSE_LOCATION)
    }
    if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context,
            Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
        add(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@Composable
private fun TripResultView(r: TripResult, busy: Boolean, onChoose: (Int) -> Unit,
                           onNavigate: () -> Unit) {
    val context = LocalContext.current
    val inApp = remember { RoutePrefs.load(context).navigateInApp }
    // Asked once, on Start navigation; the trip starts whatever the answer.
    val askTrip = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { beginTrip(context, r, onNavigate) }
    val p = r.plan
    Column(modifier = Modifier.padding(top = 8.dp)) {
        HorizontalDivider()
        Text(r.headline(), fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(top = 6.dp))
        Text("${r.origin.label} → ${r.destination.label}",
            style = MaterialTheme.typography.bodySmall)
        Text(String.format(Locale.ROOT, "%.0f km · ", p.distanceKm) +
            fmtDuration(p.driveS) + " driving" +
            (if (p.chargeS > 0) " + " + fmtDuration(p.chargeS) + " charging" else ""),
            style = MaterialTheme.typography.bodySmall)
        if (p.stops.isNotEmpty()) Text(r.costLine(), style = MaterialTheme.typography.bodySmall)
        r.nav?.trafficDelayS?.let { d ->
            Text(if (d >= 60) "In today's traffic: ${Navigator.minutes(d)} slower than usual"
                else "In today's traffic: as usual", style = MaterialTheme.typography.bodySmall)
        }
        r.notes.forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
        if (r.routes.size > 1) {
            Text("Ways there", fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 6.dp))
            r.routes.forEachIndexed { i, way ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${i + 1}. " + way.optionLine(), style = MaterialTheme.typography.bodySmall,
                        fontWeight = if (i == r.chosen) FontWeight.SemiBold else FontWeight.Normal,
                        modifier = Modifier.weight(1f))
                    if (i == r.chosen) Text("planned", style = MaterialTheme.typography.bodySmall)
                    else TextButton(enabled = !busy, onClick = { onChoose(i) }) { Text("Use") }
                }
            }
        }
        Column(modifier = Modifier.padding(vertical = 6.dp)) { TripMap(r) }
        if (p.status != "unreachable") {
            Button(onClick = {
                // Watch this trip against the battery while it is driven.
                val missing = tripPermissionsMissing(context)
                if (missing.isEmpty()) beginTrip(context, r, onNavigate)
                else askTrip.launch(missing.toTypedArray())
            }, modifier = Modifier.fillMaxWidth()) {
                Text(when (p.stops.size) {
                    0 -> "Start navigation"
                    1 -> "Start navigation · 1 charging stop"
                    else -> "Start navigation · ${p.stops.size} charging stops"
                } + if (inApp) "" else " (Google Maps)")
            }
            if (inApp) {
                TextButton(onClick = {
                    if (TripMonitor.state.value?.result !== r) TripMonitor.start(context, r)
                    startTrip(context, r)
                }) { Text("Open in Google Maps instead") }
            }
            if (p.stops.size > MAX_LINK_STOPS) {
                Text("Google Maps takes $MAX_LINK_STOPS stops from a link; add the rest there.",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        p.stops.forEachIndexed { i, s ->
            Column(modifier = Modifier.padding(top = 6.dp)) {
                Text("${i + 1}. ${s.charger.name}" +
                    (if (s.charger.operator.isNotBlank()) " · ${s.charger.operator}" else "") +
                    (if (s.preferred) " · preferred" else ""),
                    fontWeight = FontWeight.Medium)
                if (s.access.isNotBlank()) {
                    Text(s.access, style = MaterialTheme.typography.bodySmall)
                }
                Text(String.format(Locale.ROOT,
                    "at %.0f km · %.0f kW · %.0f %% → %.0f %% · %d min",
                    s.km, s.charger.powerKw, s.arrivePct, s.departPct, s.chargeMin),
                    style = MaterialTheme.typography.bodySmall)
                if (s.arriveLowPct != null && s.arriveHighPct != null) {
                    Text(String.format(Locale.ROOT, "arrive likely %.0f–%.0f %%%s",
                        s.arriveLowPct, s.arriveHighPct,
                        if (s.arriveLowBelowReserve) " — could be below your reserve" else ""),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (s.arriveLowBelowReserve) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(String.format(Locale.ROOT, "%.1f kWh at £%.2f/kWh%s = £%.2f · %s",
                    s.kwh, s.pricePerKwh,
                    if (s.charger.fee > 0) String.format(Locale.ROOT, " + £%.2f", s.charger.fee) else "",
                    s.cost, s.priceSource), style = MaterialTheme.typography.bodySmall)
                if (s.charger.address.isNotBlank()) {
                    Text(s.charger.address, style = MaterialTheme.typography.bodySmall)
                }
                if (s.alternatives.isNotEmpty()) {
                    Text("Also near: " + s.alternatives.joinToString("; ") { a ->
                        String.format(Locale.ROOT, "%s%s, %.0f kW, %+.0f km, £%.2f/kWh",
                            a.charger.name,
                            if (a.charger.operator.isNotBlank()) " (${a.charger.operator})" else "",
                            a.charger.powerKw, a.km - s.km, a.pricePerKwh)
                    }, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        Text("Assumptions", fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 4.dp))
        p.assumptions.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
        Text("Charger data and listed prices: Open Charge Map (community-maintained; check " +
            "before relying on them). Set what you pay per network in Route settings.",
            style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun RouteSettingsDialog(onDone: () -> Unit) {
    val context = LocalContext.current
    val s = remember { RoutePrefs.load(context) }
    var ors by remember { mutableStateOf(s.orsKey) }
    var ocm by remember { mutableStateOf(s.ocmKey) }
    var reserve by remember { mutableStateOf(s.reservePct.toInt().toString()) }
    var chargeTo by remember { mutableStateOf(s.chargeToPct.toInt().toString()) }
    var consumption by remember {
        mutableStateOf(s.consumptionKwhPer100km?.let { String.format(Locale.ROOT, "%.1f", it) } ?: "")
    }
    var countries by remember { mutableStateOf(s.countries) }
    var timeValue by remember { mutableStateOf(String.format(Locale.ROOT, "%.0f", s.timeValuePerHour)) }
    var defaultPrice by remember { mutableStateOf(String.format(Locale.ROOT, "%.2f", s.defaultPricePerKwh)) }
    var ownPrices by remember { mutableStateOf(s.ownPrices) }
    var mapbox by remember { mutableStateOf(s.mapboxToken) }
    var avoid by remember { mutableStateOf(s.avoid) }
    var inApp by remember { mutableStateOf(s.navigateInApp) }
    var voice by remember { mutableStateOf(s.voiceGuidance) }
    var prefer by remember { mutableStateOf(s.preferNetworks) }
    var avoidNets by remember { mutableStateOf(s.avoidNetworks) }
    var publicOnly by remember { mutableStateOf(s.publicOnly) }
    var weather by remember { mutableStateOf(s.weather) }
    AlertDialog(
        onDismissRequest = onDone,
        title = { Text("Route settings") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("Free keys: openrouteservice.org (roads, place search) and " +
                    "openchargemap.org (chargers). Planning sends the start, destination " +
                    "and route to those two services; the map loads OpenStreetMap tiles " +
                    "of the trip area.", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(ors, { ors = it }, label = { Text("OpenRouteService key") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(ocm, { ocm = it }, label = { Text("OpenChargeMap key") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(reserve, { reserve = it.take(3) },
                    label = { Text("Arrive with at least %") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(chargeTo, { chargeTo = it.take(3) },
                    label = { Text("Charge to %") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(consumption, { consumption = it.take(5) },
                    label = { Text("Consumption kWh/100 km (blank: 17)") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(countries, { countries = it.take(40) },
                    label = { Text("Countries to search (e.g. GB,FR)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("Traffic and navigation", fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(top = 8.dp))
                OutlinedTextField(mapbox, { mapbox = it }, label = { Text("Mapbox token (optional)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("With a free mapbox.com token (100,000 routes a month free) routes come " +
                    "in today's traffic, with other ways to choose from, lanes and spoken " +
                    "turns; the start, stops and destination then go to Mapbox too.",
                    style = MaterialTheme.typography.bodySmall)
                @Composable
                fun toggle(label: String, on: Boolean, change: (Boolean) -> Unit) =
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = on, onCheckedChange = change)
                        Text(label)
                    }
                toggle("Avoid motorways", avoid.motorways) { avoid = avoid.copy(motorways = it) }
                toggle("Avoid tolls", avoid.tolls) { avoid = avoid.copy(tolls = it) }
                toggle("Avoid ferries", avoid.ferries) { avoid = avoid.copy(ferries = it) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Navigate in this app (off: Google Maps)", modifier = Modifier.weight(1f))
                    Switch(checked = inApp, onCheckedChange = { inApp = it })
                }
                if (!inApp) {
                    val allowed = android.provider.Settings.canDrawOverlays(context)
                    Text(if (allowed) "After a re-plan, say yes and the new route goes to " +
                        "Google Maps." else "After a re-plan the phone asks whether to send the " +
                        "new route to Google Maps. Android lets it open Google Maps by voice " +
                        "only with Display over other apps allowed; otherwise tap the notification.",
                        style = MaterialTheme.typography.bodySmall)
                    if (!allowed) {
                        TextButton(onClick = {
                            context.startActivity(MapsHandoff.permissionIntent(context))
                        }) { Text("Allow Display over other apps") }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Spoken directions", modifier = Modifier.weight(1f))
                    Switch(checked = voice, onCheckedChange = { voice = it })
                }
                Text("Prices", fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 8.dp))
                OutlinedTextField(timeValue, { timeValue = it.take(4) },
                    label = { Text("Balanced: my time is worth £/hour") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(defaultPrice, { defaultPrice = it.take(5) },
                    label = { Text("Price when unknown, £/kWh") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(ownPrices, { ownPrices = it.take(2000) },
                    label = { Text("What I pay, one network per line") },
                    placeholder = { Text("Ionity = 0.39\nTesla = 0.55") },
                    minLines = 2, modifier = Modifier.fillMaxWidth())
                Text("Your prices beat the price Open Charge Map lists; matched on the " +
                    "network's name.", style = MaterialTheme.typography.bodySmall)
                Text("Charging networks", fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(top = 8.dp))
                OutlinedTextField(prefer, { prefer = it.take(400) },
                    label = { Text("Prefer (comma-separated)") },
                    placeholder = { Text("Ionity, Gridserve") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(avoidNets, { avoidNets = it.take(400) },
                    label = { Text("Never use (comma-separated)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                toggle("Public chargers only (no members-only sites)", publicOnly) { publicOnly = it }
                Text("A preferred network wins a close choice; the price shown is still its " +
                    "real price, and it never adds a stop the trip does not need.",
                    style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Count the forecast wind", modifier = Modifier.weight(1f))
                    Switch(checked = weather, onCheckedChange = { weather = it })
                }
                Text("From Open-Meteo (free, no key): points along the route, rounded to " +
                    "about 11 km, are sent to it.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                RoutePrefs.save(context, s.copy(
                    orsKey = ors, ocmKey = ocm,
                    reservePct = reserve.toDoubleOrNull() ?: s.reservePct,
                    chargeToPct = chargeTo.toDoubleOrNull() ?: s.chargeToPct,
                    consumptionKwhPer100km = consumption.toDoubleOrNull()
                        ?.takeIf { it in 8.0..40.0 },
                    countries = countries,
                    timeValuePerHour = timeValue.toDoubleOrNull() ?: s.timeValuePerHour,
                    defaultPricePerKwh = defaultPrice.toDoubleOrNull() ?: s.defaultPricePerKwh,
                    ownPrices = ownPrices,
                    mapboxToken = mapbox,
                    avoid = avoid,
                    navigateInApp = inApp,
                    voiceGuidance = voice,
                    preferNetworks = prefer,
                    avoidNetworks = avoidNets,
                    publicOnly = publicOnly,
                    weather = weather,
                ))
                onDone()
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } },
    )
}
