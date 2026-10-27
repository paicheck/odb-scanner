package com.odbscanner

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.odbscanner.route.CarEnergy
import com.odbscanner.route.MAX_LINK_STOPS
import com.odbscanner.route.MODES
import com.odbscanner.route.PhoneLocation
import com.odbscanner.route.Place
import com.odbscanner.route.RoutePrefs
import com.odbscanner.route.RoutingException
import com.odbscanner.route.TripPlanner
import com.odbscanner.route.TripResult
import com.odbscanner.route.googleMapsTripUrl
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The whole trip to Google Maps in one go: each charging stop, then the
 * destination. Only on the driver's tap -- Google Maps is the one common
 * navigation app that takes stops from a link; without it the link opens in
 * the browser.
 */
fun startTrip(context: Context, r: TripResult) {
    val url = Uri.parse(googleMapsTripUrl(
        r.origin.lat to r.origin.lon,
        r.plan.stops.map { it.charger.lat to it.charger.lon },
        r.destination.lat to r.destination.lon))
    val maps = Intent(Intent.ACTION_VIEW, url).setPackage("com.google.android.apps.maps")
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
        context.startActivity(maps)
    } catch (_: ActivityNotFoundException) {
        context.startActivity(Intent(Intent.ACTION_VIEW, url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

fun fmtDuration(seconds: Double): String {
    val min = Math.round(seconds / 60.0)
    return if (min >= 60) "${min / 60} h %02d".format(min % 60) else "$min min"
}

private fun pct(v: Double) = String.format(Locale.ROOT, "%.0f %%", v)

/** Plain-language one-line summary of a plan, shared with the car screen. */
fun TripResult.headline(): String = when (plan.status) {
    "direct" -> "Arrive with ${pct(plan.arrivalPct!!)} — no charging needed"
    "with_stops" -> "Arrive with ${pct(plan.arrivalPct!!)} after ${plan.stops.size} " +
        "charging stop${if (plan.stops.size == 1) "" else "s"}"
    else -> "Not reachable: below the reserve at about " +
        "${Math.round(plan.unreachableAtKm ?: 0.0)} km and no fast charger found before it"
}

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
 * location. The trip is drawn on an OpenStreetMap map; Start navigation
 * hands it, stops included, to Google Maps.
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

    LaunchedEffect(Unit) {
        val car = CarEnergy.load(context)
        startPct = String.format(Locale.ROOT, "%.0f", car.startPct)
        carNote = "Start charge from ${car.startBasis}" +
            (car.startTs?.let { " ($it)" } ?: "") + "; battery " +
            String.format(Locale.ROOT, "%.1f kWh", car.usableKwh) + " (${car.usableBasis})."
    }

    fun plan() {
        busy = true
        error = null
        result = null
        scope.launch {
            try {
                val origin: Place? = if (from.isBlank()) null else
                    TripPlanner.search(context, from.trim()).firstOrNull()
                        ?: throw RoutingException("No place found for “${from.trim()}”.")
                val dest = TripPlanner.search(context, to.trim(), near = origin).firstOrNull()
                    ?: throw RoutingException("No place found for “${to.trim()}”.")
                result = TripPlanner.plan(context, dest, origin,
                    startPct.toDoubleOrNull()?.takeIf { it in 1.0..100.0 }, mode)
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
                result?.let { TripResultView(it) }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun TripResultView(r: TripResult) {
    val context = LocalContext.current
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
        Column(modifier = Modifier.padding(vertical = 6.dp)) { TripMap(r) }
        if (p.status != "unreachable") {
            Button(onClick = { startTrip(context, r) }, modifier = Modifier.fillMaxWidth()) {
                Text(when (p.stops.size) {
                    0 -> "Start navigation"
                    1 -> "Start navigation · 1 charging stop"
                    else -> "Start navigation · ${p.stops.size} charging stops"
                })
            }
            if (p.stops.size > MAX_LINK_STOPS) {
                Text("Google Maps takes $MAX_LINK_STOPS stops from a link; add the rest there.",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        p.stops.forEachIndexed { i, s ->
            Column(modifier = Modifier.padding(top = 6.dp)) {
                Text("${i + 1}. ${s.charger.name}" +
                    (if (s.charger.operator.isNotBlank()) " · ${s.charger.operator}" else ""),
                    fontWeight = FontWeight.Medium)
                Text(String.format(Locale.ROOT,
                    "at %.0f km · %.0f kW · %.0f %% → %.0f %% · %d min",
                    s.km, s.charger.powerKw, s.arrivePct, s.departPct, s.chargeMin),
                    style = MaterialTheme.typography.bodySmall)
                Text(String.format(Locale.ROOT, "%.1f kWh at £%.2f/kWh%s = £%.2f · %s",
                    s.kwh, s.pricePerKwh,
                    if (s.charger.fee > 0) String.format(Locale.ROOT, " + £%.2f", s.charger.fee) else "",
                    s.cost, s.priceSource), style = MaterialTheme.typography.bodySmall)
                if (s.charger.address.isNotBlank()) {
                    Text(s.charger.address, style = MaterialTheme.typography.bodySmall)
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
                ))
                onDone()
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } },
    )
}
