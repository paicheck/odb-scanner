package com.odbscanner

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.PopupProperties
import androidx.core.content.ContextCompat
import com.odbscanner.route.ActiveTrip
import com.odbscanner.route.CarEnergy
import com.odbscanner.route.GoogleMaps
import com.odbscanner.route.MODES
import com.odbscanner.route.MapsHandoff
import com.odbscanner.route.NavRoute
import com.odbscanner.route.NavText
import com.odbscanner.route.NetworkList
import com.odbscanner.route.Networks
import com.odbscanner.route.Navigator
import com.odbscanner.route.PhoneLocation
import com.odbscanner.route.Place
import com.odbscanner.route.Planner
import com.odbscanner.route.RoutePrefs
import com.odbscanner.route.Picks
import com.odbscanner.route.RouteRevisions
import com.odbscanner.route.RoutingException
import com.odbscanner.route.TripMonitor
import com.odbscanner.route.TripPlanner
import com.odbscanner.route.TripResult
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The whole trip to Google Maps in one go: each charging stop, then the
 * destination. Only on the driver's tap -- Google Maps is the one common
 * navigation app that takes stops from a link; without it the link opens in
 * the browser.
 */
fun startTrip(context: Context, r: TripResult) {
    MapsHandoff.open(context, r)          // from here; records the revision handed over
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
    // Chargers avoided or chosen for the trip being planned; once it is
    // driven they live in its RouteState.
    var picks by remember { mutableStateOf(Picks()) }
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
                    avoid = ways?.avoid, ways = ways?.routes, choice = choice, picks = picks)
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
                    val driving = active?.takeIf { it.result === r }
                    TripResultView(r, busy = busy,
                        onChoose = { i -> plan(ways = r, choice = i) },
                        onNavigate = onDismiss,          // the directions take the screen
                        picks = driving?.route?.picks ?: picks,
                        onPicks = { chosen, why ->
                            if (driving != null) scope.launch {
                                busy = true
                                try { TripMonitor.changePicks(context, chosen, why) }
                                finally { busy = false }
                            } else {
                                picks = chosen
                                plan(ways = r, choice = r.chosen)
                            }
                        })
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

/**
 * The trip being driven: what the monitor last saw, a suggested plan waiting
 * for the driver, whether Google Maps has the current plan, Re-plan now, End.
 */
@Composable
private fun ActiveTripBanner(t: ActiveTrip) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var said by remember { mutableStateOf<String?>(null) }
    Column(modifier = Modifier.padding(bottom = 8.dp)) {
        Text("Trip to ${t.destination.label}", fontWeight = FontWeight.SemiBold)
        Text(t.note + (if (t.replans > 0) " · re-planned ${t.replans}×" else ""),
            style = MaterialTheme.typography.bodySmall)
        Text("Plan revision ${t.route.revision}: ${t.route.current.reason}",
            style = MaterialTheme.typography.bodySmall)
        t.route.proposal?.let { p ->
            Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)
                .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(8.dp))
                .padding(10.dp)) {
                Text(if (p.urgent) "Your plan no longer works" else "New plan suggested",
                    fontWeight = FontWeight.SemiBold)
                Text("${p.change} (${p.reason})", style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { scope.launch { said = TripMonitor.accept(context) } }) {
                        Text("Switch")
                    }
                    OutlinedButton(onClick = { said = TripMonitor.decline() }) { Text("Keep my plan") }
                }
            }
        }
        if (t.route.sync.provider == RouteRevisions.GOOGLE_MAPS) {
            val sent = t.route.sync.sentRevision
            Text(when {
                !t.route.needsHandoff -> "Google Maps has this plan (revision $sent)."
                sent == null -> "Not handed to Google Maps yet."
                else -> "Google Maps has an older plan (revision $sent); it cannot be updated " +
                    "from here, only handed this one."
            }, style = MaterialTheme.typography.bodySmall)
            if (t.route.needsHandoff) {
                TextButton(onClick = { startTrip(context, t.result) }) { Text("Send to Google Maps") }
            }
        }
        said?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { TripMonitor.replanNow(context) }) { Text("Re-plan now") }
            TextButton(onClick = { TripMonitor.stop() }) { Text("End trip") }
        }
        HorizontalDivider()
    }
}

/** Watch the trip and navigate it here -- or, by setting, in Google Maps. */
private fun beginTrip(context: Context, r: TripResult, picks: Picks, onNavigate: () -> Unit) {
    if (TripMonitor.drive(context, r, picks)) {
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
                           onNavigate: () -> Unit, picks: Picks = Picks(),
                           onPicks: (Picks, String) -> Unit = { _, _ -> }) {
    val context = LocalContext.current
    val inApp = remember { RoutePrefs.load(context).navigateInApp }
    // Asked once, on Start navigation; the trip starts whatever the answer.
    val askTrip = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { beginTrip(context, r, picks, onNavigate) }
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
        var preview by remember(r) { mutableStateOf(false) }
        Column(modifier = Modifier.padding(vertical = 6.dp)) {
            TripMap(r, onExpand = { preview = true })
        }
        if (preview) RoutePreview(r) { preview = false }
        if (p.status != "unreachable") {
            Button(onClick = {
                // Watch this trip against the battery while it is driven.
                val missing = tripPermissionsMissing(context)
                if (missing.isEmpty()) beginTrip(context, r, picks, onNavigate)
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
                    if (TripMonitor.state.value?.result !== r) TripMonitor.start(context, r, picks)
                    startTrip(context, r)
                }) { Text("Open in Google Maps instead") }
            }
            if (p.stops.size > GoogleMaps.MAX_WAYPOINTS) {
                Text(MapsHandoff.link(r).note,
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
                // The leg after it: to the next stop, or to the destination.
                val next = p.stops.getOrNull(i + 1)
                val onKm = next?.let { it.legKm } ?: p.lastLegKm
                val onKwh = next?.let { it.legKwh } ?: p.lastLegKwh
                Text(String.format(Locale.ROOT, "Leg here: %.0f km, %.1f kWh", s.legKm, s.legKwh) +
                    (if (onKm != null && onKwh != null) String.format(Locale.ROOT,
                        " · then %.0f km, %.1f kWh to %s%s", onKm, onKwh,
                        if (next != null) "stop ${i + 2}" else "the destination",
                        next?.let { String.format(Locale.ROOT, " (arrive %.0f %%)", it.arrivePct) } ?: "")
                    else ""), style = MaterialTheme.typography.bodySmall)
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
                if (s.charger.id.isNotBlank()) {
                    TextButton(enabled = !busy, onClick = {
                        onPicks(picks.avoid(s.charger.id), "you chose to avoid ${s.charger.name}")
                    }) { Text("Avoid this charger") }
                }
                if (s.alternatives.isNotEmpty()) {
                    Text("Also near:", style = MaterialTheme.typography.bodySmall)
                    s.alternatives.forEach { a ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(String.format(Locale.ROOT, "%s%s, %.0f kW, %+.0f km, £%.2f/kWh",
                                a.charger.name,
                                if (a.charger.operator.isNotBlank()) " (${a.charger.operator})" else "",
                                a.charger.powerKw, a.km - s.km, a.pricePerKwh),
                                style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                            if (a.charger.id.isNotBlank()) {
                                TextButton(enabled = !busy, onClick = {
                                    // Instead of this stop: use the other, keep off this one.
                                    onPicks(picks.insteadOf(s.charger.id, a.charger.id),
                                        "you chose ${a.charger.name} instead of ${s.charger.name}")
                                }) { Text("Use") }
                            }
                        }
                    }
                }
            }
        }
        if (!picks.isEmpty) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Your charger choices: avoiding ${picks.avoidChargers.size}, " +
                    "using ${picks.useChargers.size}" + (if (picks.avoidNetworks.isEmpty()) ""
                    else "; never " + picks.avoidNetworks.joinToString(", ")),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f))
                TextButton(enabled = !busy, onClick = {
                    onPicks(Picks(), "you cleared your charger choices")
                }) { Text("Clear") }
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
    val scope = rememberCoroutineScope()
    var networks by remember { mutableStateOf(Networks.cached(context)) }
    var counting by remember { mutableStateOf(false) }
    var networkNote by remember { mutableStateOf<String?>(null) }
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
                fun countNetworks(refresh: Boolean) {
                    if (counting) return
                    counting = true
                    networkNote = null
                    scope.launch {
                        networks = try {
                            Networks.load(context, s.copy(ocmKey = ocm.trim(),
                                countries = countries), refresh)
                        } catch (e: RoutingException) {
                            networkNote = e.message
                            networks
                        }
                        counting = false
                    }
                }
                val list = networks
                Text(when {
                    counting -> "Counting the networks' charging locations on Open Charge Map…"
                    list == null -> "Pick from the networks with the most fast charging locations: " +
                        "list them from Open Charge Map (one larger download, kept for a month)."
                    else -> "${list.networks.size} networks with fast CCS chargers in " +
                        "${list.countries}, most locations first (Open Charge Map, " +
                        "${ageText(list.fetchedAtMs)})."
                }, style = MaterialTheme.typography.bodySmall)
                networkNote?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                TextButton(enabled = !counting && ocm.isNotBlank(),
                    onClick = { countNetworks(refresh = list != null) }) {
                    Text(if (list == null) "List the networks" else "Count again")
                }
                NetworkPicker("Prefer", prefer, list,
                    onAdd = { n -> Networks.choose(n, prefer, avoidNets).let { (a, b) -> prefer = a; avoidNets = b } },
                    onRemove = { n -> prefer = Networks.remove(n, prefer) })
                NetworkPicker("Never use", avoidNets, list,
                    onAdd = { n -> Networks.choose(n, avoidNets, prefer).let { (a, b) -> avoidNets = a; prefer = b } },
                    onRemove = { n -> avoidNets = Networks.remove(n, avoidNets) })
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

/**
 * One list of networks (Prefer, or Never use): the ones chosen, each a chip
 * that takes it off when tapped, and a box that lists the networks with the
 * most charging locations first, narrowing as you type. A network not in the
 * list can still be added as typed (the planner matches on part of the name).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NetworkPicker(label: String, chosen: String, list: NetworkList?,
                          onAdd: (String) -> Unit, onRemove: (String) -> Unit) {
    val names = Planner.names(chosen)
    var query by remember { mutableStateOf("") }
    var open by remember { mutableStateOf(false) }
    Text(label, modifier = Modifier.padding(top = 6.dp))
    if (names.isNotEmpty()) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            names.forEach { n ->
                AssistChip(onClick = { onRemove(n) }, label = { Text("$n  ✕") })
            }
        }
    }
    val options = (list?.networks ?: emptyList())
        .filter { o -> names.none { it.equals(o.name, ignoreCase = true) } }
        .filter { query.isBlank() || query.trim().lowercase() in it.name.lowercase() }
        .take(40)
    Box {
        OutlinedTextField(query, { query = it.take(60); open = true },
            label = { Text(if (list == null) "Type a network to add" else "Add a network") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().onFocusChanged { if (it.isFocused) open = true })
        DropdownMenu(expanded = open && (options.isNotEmpty() || query.isNotBlank()),
            onDismissRequest = { open = false },
            properties = PopupProperties(focusable = false)) {
            if (query.isNotBlank() && options.none { it.name.equals(query.trim(), ignoreCase = true) }) {
                DropdownMenuItem(text = { Text("Add “${query.trim()}” as typed") }, onClick = {
                    onAdd(query.trim()); query = ""; open = false
                })
            }
            options.forEach { o ->
                DropdownMenuItem(text = { Text("${o.name} (${o.locations})") }, onClick = {
                    onAdd(o.name); query = ""; open = false
                })
            }
        }
    }
}

private fun ageText(atMs: Long): String {
    val days = (System.currentTimeMillis() - atMs) / 86_400_000L
    return when {
        days < 1 -> "today"
        days == 1L -> "yesterday"
        else -> "$days days ago"
    }
}
