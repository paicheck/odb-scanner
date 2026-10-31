package com.odbscanner

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.odbscanner.route.Avoid
import com.odbscanner.route.MapMath
import com.odbscanner.route.NavRoute
import com.odbscanner.route.NavSpeech
import com.odbscanner.route.NavText
import com.odbscanner.route.Navigator
import com.odbscanner.route.TripMonitor
import androidx.compose.material3.ButtonDefaults
import androidx.compose.foundation.layout.height
import androidx.compose.ui.geometry.Size
import com.odbscanner.route.NavStatus
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.log2
import kotlin.math.pow

private val BANNER = Color(0xFF1B5E20)
private val ROAD = Color(0xFF1565C0)
private val HEAVY = Color(0xFFE65100)
private val SEVERE = Color(0xFFB71C1C)
private val STOP = Color(0xFF2E7D32)
private val END = Color(0xFFC62828)
private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")

private fun clockIn(seconds: Double): String =
    LocalTime.now().plusSeconds(seconds.toLong()).format(CLOCK)

/**
 * Turn-by-turn on the phone: the next turn with its distance and lanes, a
 * heading-up map of the road ahead (heavy traffic in orange, very heavy in
 * red), arrival time and what traffic adds. Back leaves navigation running
 * -- spoken, and in the notification; End finishes the trip.
 */
@Composable
fun NavigationScreen(onClose: () -> Unit) {
    val nav by Navigator.state.collectAsState()
    Dialog(onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false)) {
        val view = LocalView.current
        DisposableEffect(view) {                         // no screen timeout while navigating
            view.keepScreenOn = true
            onDispose { view.keepScreenOn = false }
        }
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            val s = nav
            if (s == null) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Navigation has ended.", style = MaterialTheme.typography.titleMedium)
                    Button(onClick = onClose) { Text("Close") }
                }
            } else {
                Column(Modifier.fillMaxSize()) {
                    TurnBanner(s)
                    Lanes(s)
                    TripAlerts(s)
                    // Clipped: the turned map is drawn larger than its box.
                    Box(Modifier.weight(1f).fillMaxWidth().clipToBounds()) {
                        NavMap(s)
                        TrafficChip(s, Modifier.align(Alignment.TopCenter).padding(8.dp))
                        SpeedSign(s, Modifier.align(Alignment.BottomStart).padding(12.dp))
                    }
                    BottomBar(s, onClose)
                }
            }
        }
    }
}

/**
 * The posted limit where the car is, as a British sign, beside the speed now
 * -- red when past the limit by the margin the navigator speaks at. No sign
 * where the limit is not known (always, without a Mapbox token).
 */
@Composable
private fun SpeedSign(s: Navigator.State, modifier: Modifier = Modifier) {
    val g = s.guidance ?: return
    if (s.phase != Navigator.Phase.DRIVING || g.offRoute) return
    val limit = s.route?.speedLimitAt(g.kmAlong)
    val kmh = limit?.unit == "km/h"
    val shown = if (kmh) s.speedKmh else s.speedKmh / 1.609344
    val over = limit != null &&
        s.speedKmh > limit.kmh * Navigator.OVER_LIMIT_FACTOR + Navigator.OVER_LIMIT_KMH
    Row(modifier, verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (limit != null) {
            Box(Modifier.size(58.dp).background(Color.White, CircleShape)
                .border(7.dp, Color(0xFFD32F2F), CircleShape),
                contentAlignment = Alignment.Center) {
                Text(limit.value.toString(), color = Color.Black, fontSize = 22.sp,
                    fontWeight = FontWeight.Bold)
            }
        }
        Text(String.format(java.util.Locale.ROOT, "%.0f %s", shown, if (kmh) "km/h" else "mph"),
            color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold,
            modifier = Modifier.background(if (over) SEVERE else Color(0xCC263238),
                RoundedCornerShape(8.dp)).padding(horizontal = 10.dp, vertical = 6.dp))
    }
}

@Composable
private fun TurnBanner(s: Navigator.State) {
    val g = s.guidance
    val next = g?.next
    // A tap on it says it again.
    Column(Modifier.fillMaxWidth().background(BANNER).clickable { Navigator.repeat() }
        .padding(horizontal = 16.dp, vertical = 12.dp)) {
        val white = Color.White
        when {
            s.phase == Navigator.Phase.ARRIVED ->
                Text(s.note, color = white, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            s.phase == Navigator.Phase.AT_STOP -> {
                Text(s.note, color = white, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                s.stopHere?.stop?.let { st ->
                    Text(String.format(java.util.Locale.ROOT, "Charge to %.0f %% · about %d min",
                        st.departPct, st.chargeMin), color = white, fontSize = 16.sp)
                }
                Text("Drive off when ready: the route on starts by itself.", color = white,
                    fontSize = 14.sp)
            }
            next == null || g.offRoute -> Text(s.note.ifBlank { "Finding the route…" }, color = white,
                fontSize = 22.sp, fontWeight = FontWeight.Bold)
            else -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(NavText.arrow(next), color = white, fontSize = 52.sp,
                        modifier = Modifier.padding(end = 14.dp))
                    Column {
                        Text(NavText.distance(g.toNextKm), color = white, fontSize = 30.sp,
                            fontWeight = FontWeight.Bold)
                        Text(next.instruction, color = white, fontSize = 18.sp, maxLines = 3,
                            overflow = TextOverflow.Ellipsis)
                        val road = listOfNotNull(next.exit.takeIf { it.isNotBlank() }?.let { "Exit $it" },
                            next.road.takeIf { it.isNotBlank() }).joinToString(" · ")
                        if (road.isNotBlank()) Text(road, color = white.copy(alpha = 0.8f), fontSize = 14.sp)
                    }
                }
                g.then?.let { then ->
                    Text("Then ${NavText.arrow(then)}  ${then.instruction}", color = white,
                        fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(top = 4.dp))
                }
            }
        }
    }
}

/** The lanes at the coming junction: the ones to be in lit up. */
@Composable
private fun Lanes(s: Navigator.State) {
    val g = s.guidance ?: return
    val next = g.next ?: return
    if (g.lanes.isEmpty() || g.offRoute) return
    Column(Modifier.fillMaxWidth().background(Color(0xFF14461A)).padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally) {
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            g.lanes.forEach { lane ->
                Box(Modifier.size(width = 40.dp, height = 44.dp)
                    .background(if (lane.active) Color.White else Color(0xFF2E5E33),
                        RoundedCornerShape(6.dp)),
                    contentAlignment = Alignment.Center) {
                    Text(NavText.lane(lane, next.modifier), fontSize = 26.sp,
                        color = if (lane.active) BANNER else Color(0xFF9DB89F))
                }
            }
        }
        NavSpeech.laneAdvice(g.lanes)?.let {
            Text(it, color = Color.White, fontSize = 14.sp, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@Composable
private fun TrafficChip(s: Navigator.State, modifier: Modifier) {
    val g = s.guidance ?: return
    val route = s.route ?: return
    val run = route.trafficAhead(g.kmAlong, 8.0) ?: return
    val text = NavSpeech.traffic(run, g.kmAlong).removeSuffix(".")
    Text(text, color = Color.White, fontSize = 14.sp, modifier = modifier
        .background(if (run.level == "severe") SEVERE else HEAVY, RoundedCornerShape(16.dp))
        .padding(horizontal = 12.dp, vertical = 6.dp))
}

@Composable
private fun BottomBar(s: Navigator.State, onClose: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var menu by remember { mutableStateOf(false) }
    var confirmEnd by remember { mutableStateOf(false) }
    var said by remember { mutableStateOf<String?>(null) }
    val g = s.guidance
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        if (g != null) {
            val toEnd = s.toEndS ?: g.remainingS
            Row(verticalAlignment = Alignment.Bottom) {
                Text(clockIn(toEnd), fontSize = 28.sp, fontWeight = FontWeight.Bold)
                Text("  " + fmtDuration(toEnd) + " · " + NavText.distance(
                    g.remainingKm + (s.target?.stop?.let { s.trip.plan.distanceKm - it.km } ?: 0.0)),
                    fontSize = 16.sp)
            }
            val delay = s.route?.trafficDelayS
            Text(when {
                !s.traffic -> "No live traffic (add a Mapbox token in Route settings)"
                delay != null && delay >= 60 -> "Traffic adds ${Navigator.minutes(delay)}"
                else -> "Traffic is light"
            }, style = MaterialTheme.typography.bodySmall,
                color = if ((delay ?: 0.0) >= 300) SEVERE else MaterialTheme.colorScheme.onSurface)
            val toGo = g.remainingKm + (s.target?.stop?.let { s.trip.plan.distanceKm - it.km } ?: 0.0)
            val (driven, _) = NavStatus.progress(s.trip.plan.distanceKm, toGo)
            Text("${NavText.distance(driven)} driven · ${NavText.distance(toGo)} to go",
                style = MaterialTheme.typography.bodySmall)
            // Estimates from the latest plan (re-planned from the car's battery as
            // it drives), with the range the car's own record supports.
            s.target?.stop?.let { st ->
                val range = if (st.arriveLowPct != null && st.arriveHighPct != null)
                    " (likely ${st.arriveLowPct.toInt()}–${st.arriveHighPct.toInt()} %)" else ""
                Text("Charging stop ${s.trip.plan.stops.indexOf(st) + 1}: ${st.charger.name} in " +
                    "${NavText.distance(g.remainingKm)} · arrive ${clockIn(g.remainingS)} " +
                    "with about ${st.arrivePct.toInt()} %$range · charge to " +
                    "${st.departPct.toInt()} %, about ${st.chargeMin} min",
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            } ?: s.trip.plan.arrivalPct?.let { pct ->
                val band = s.trip.plan.arrivalBand
                Text("Arrive with about ${pct.toInt()} % charge" +
                    (band?.let { " (likely ${it.lowPct.toInt()}–${it.highPct.toInt()} %)" } ?: ""),
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                    color = if (band?.belowReserve == true) SEVERE else MaterialTheme.colorScheme.onSurface)
            }
        }
        // Measured against planned: the car's latest battery reading and the
        // consumption so far (EnRoute's ratio), or that there is no fresh reading.
        val judged = TripMonitor.state.collectAsState().value?.assessment
        val measured = when {
            judged == null -> null
            judged.staleReading -> "No fresh battery reading: the figures are the plan's"
            judged.deviationPct != null && judged.actualPct != null -> String.format(
                java.util.Locale.ROOT, "Battery %.0f %% · plan %.0f %% here", judged.actualPct,
                judged.expectedPct ?: 0.0) + (judged.ratio?.takeIf { kotlin.math.abs(it - 1.0) >= 0.02 }
                ?.let { String.format(java.util.Locale.ROOT, " · using %.2f× the plan", it) } ?: "")
            else -> null
        }
        measured?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        if (s.note.isNotBlank() && s.phase == Navigator.Phase.DRIVING && g?.offRoute != true) {
            Text(s.note, style = MaterialTheme.typography.bodySmall)
        }
        said?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
            OutlinedButton(onClick = { Navigator.setMuted(!s.muted) }) {
                Text(if (s.muted) "Voice on" else "Mute")
            }
            Box {
                OutlinedButton(onClick = { menu = true }) { Text("Route") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("Check for a faster way") }, onClick = {
                        menu = false
                        said = "Checking the traffic…"
                        scope.launch { said = runCatching { Navigator.checkTraffic(announce = true) }
                            .getOrElse { "Could not check: ${it.message}" } }
                    })
                    @Composable
                    fun avoid(label: String, on: Boolean, change: (Avoid) -> Avoid) =
                        DropdownMenuItem(text = { Text((if (on) "✓ " else "") + label) }, onClick = {
                            menu = false
                            Navigator.reroute(change(s.avoid))
                        })
                    avoid("Avoid motorways", s.avoid.motorways) { it.copy(motorways = !it.motorways) }
                    avoid("Avoid tolls", s.avoid.tolls) { it.copy(tolls = !it.tolls) }
                    avoid("Avoid ferries", s.avoid.ferries) { it.copy(ferries = !it.ferries) }
                    DropdownMenuItem(text = { Text("Re-plan charging from here") }, onClick = {
                        menu = false
                        TripMonitor.replanNow(context)
                    })
                    // Google Maps speaks its own directions: these go quiet.
                    DropdownMenuItem(text = { Text("Open in Google Maps (mutes these)") }, onClick = {
                        menu = false
                        Navigator.setMuted(true)
                        startTrip(context, s.trip)
                    })
                }
            }
            Button(onClick = { confirmEnd = true }) { Text("End") }
            TextButton(onClick = onClose) { Text("Back") }
        }
    }
    if (confirmEnd) {
        AlertDialog(onDismissRequest = { confirmEnd = false },
            title = { Text("End navigation?") },
            text = { Text("This ends the trip: no more directions or re-planning.") },
            confirmButton = { TextButton(onClick = {
                confirmEnd = false
                TripMonitor.stop()
                onClose()
            }) { Text("End") } },
            dismissButton = { TextButton(onClick = { confirmEnd = false }) { Text("Keep going") } })
    }
}

/**
 * What needs the driver's attention, above the map: the GPS when the
 * position cannot be trusted, the en-route level when it is not normal, and a
 * suggested plan with two large buttons. Nothing here changes the route by
 * itself.
 */
@Composable
private fun TripAlerts(s: Navigator.State) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val trip by TripMonitor.state.collectAsState()
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000)
            now = System.currentTimeMillis()
        }
    }
    @Composable
    fun banner(text: String, colour: Color) = Text(text, color = Color.White, fontSize = 15.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.fillMaxWidth().background(colour).padding(horizontal = 12.dp, vertical = 6.dp))
    NavStatus.gps(s.fixAtMs, s.accuracyM, now)?.let { banner(it, Color(0xFF616161)) }
    val proposal = trip?.route?.proposal
    trip?.assessment?.takeIf { it.level != "normal" && proposal == null }?.let { a ->
        banner("${a.level.uppercase(java.util.Locale.ROOT)}: ${a.action}", when (a.level) {
            "critical" -> SEVERE
            "adjust" -> HEAVY
            else -> Color(0xFF9E7700)
        })
    }
    proposal?.let { p ->
        Column(Modifier.fillMaxWidth().background(if (p.urgent) SEVERE else ROAD)
            .padding(horizontal = 12.dp, vertical = 10.dp)) {
            Text(if (p.urgent) "Your plan no longer works" else "New plan suggested",
                color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Text(p.change, color = Color.White, fontSize = 15.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.padding(top = 8.dp)) {
                Button(onClick = { scope.launch { TripMonitor.accept(context) } },
                    colors = ButtonDefaults.buttonColors(containerColor = Color.White,
                        contentColor = Color.Black),
                    modifier = Modifier.weight(1f).height(56.dp)) { Text("Switch", fontSize = 18.sp) }
                OutlinedButton(onClick = { TripMonitor.decline() },
                    modifier = Modifier.weight(1f).height(56.dp)) {
                    Text("Keep my plan", color = Color.White, fontSize = 18.sp)
                }
            }
        }
    }
}

/**
 * The road ahead, heading up: OpenStreetMap tiles turned so the way the road
 * goes points up, the car a little below the middle. Zooms out at speed; the
 * driver can pinch or use + and − to zoom from there, and Overview shows the
 * whole trip (north up, to explore) until Re-centre.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NavMap(s: Navigator.State) {
    var userZoom by remember { mutableStateOf(0.0) }      // added to the zoom for the speed
    var overview by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val density = LocalDensity.current
    val route = s.route
    val g = s.guidance
    val onRoute = route != null && g != null && !g.offRoute
    val here = when {
        onRoute -> route!!.pointAt(g!!.kmAlong)
        s.fix != null -> s.fix
        else -> route?.points?.firstOrNull()?.let { it.lat to it.lon }
    } ?: return
    if (overview) {
        Box(Modifier.fillMaxSize()) {
            // TripMap's own column shares the height between map and place row.
            Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)
                .padding(6.dp)) {
                TripMap(s.trip, Modifier.fillMaxWidth().weight(1f), here = here)
            }
            Surface(onClick = { overview = false }, shape = RoundedCornerShape(20.dp),
                color = ROAD, modifier = Modifier.align(Alignment.TopStart).padding(12.dp)) {
                Text("Re-centre", color = Color.White, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp))
            }
        }
        return
    }
    val bearing = if (onRoute) route!!.bearingAt(g!!.kmAlong).toFloat() else 0f
    val night = NavStatus.isNight(here.first, here.second, System.currentTimeMillis())
    // Closer as a turn comes up; further out the faster the road.
    val autoZoom = when {
        g != null && !g.offRoute && g.toNextKm < 0.3 -> 16.5
        s.speedKmh >= 100 -> 14.5
        s.speedKmh >= 60 -> 15.0
        else -> 16.0
    }
    val zoomF = (autoZoom + userZoom).coerceIn(NAV_MIN_ZOOM, MapMath.MAX_VIEW_ZOOM)
    val zoom = floor(zoomF).toInt().coerceAtMost(MapMath.MAX_ZOOM)
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val w = constraints.maxWidth.toFloat()
        val h = constraints.maxHeight.toFloat()
        // Between whole zooms the tiles are drawn larger: smooth pinching.
        val tilePx = with(density) { 160.dp.toPx() } * 2.0.pow(zoomF - zoom).toFloat()
        // A square around the car big enough to fill the screen at any heading.
        val side = 2f * hypot(w / 2f, h * 0.72f)
        val v = MapMath.View(zoom, MapMath.worldX(here.second, zoom), MapMath.worldY(here.first, zoom),
            side, side, tilePx)
        // Keyed on the set, not the list: the list's order (nearest first)
        // changes with every fix, the set only when the car crosses a tile.
        val needed = v.tiles()
        val wanted = needed.toSet()
        val tiles = remember { mutableStateMapOf<MapMath.Tile, ImageBitmap>() }
        LaunchedEffect(wanted) {
            tiles.keys.filter { it !in wanted }.forEach { tiles.remove(it) }
            for (t in needed) if (t !in tiles) OsmTiles.get(context, t)?.let { tiles[t] = it }
        }
        val world = remember(route, zoom) { route?.let { worldLine(it, zoom) } }
        val line = with(density) { 7.dp.toPx() }
        val dot = with(density) { 9.dp.toPx() }
        Canvas(Modifier.fillMaxSize().pointerInput(Unit) {
            detectTransformGestures { _, _, pinch, _ ->
                userZoom = (userZoom + log2(pinch.toDouble())).coerceIn(-6.0, 3.0)
            }
        }) {
            drawRect(Color(0xFFE8E6E1))
            withTransform({
                translate(w / 2f - side / 2f, h * 0.72f - side / 2f)
                rotate(-bearing, Offset(side / 2f, side / 2f))
            }) {
                val size = ceil(tilePx).toInt()
                tiles.forEach { (t, img) ->
                    drawImage(img, dstOffset = IntOffset(v.left(t).toInt(), v.top(t).toInt()),
                        dstSize = IntSize(size, size), filterQuality = FilterQuality.Medium)
                }
                // Night: the map dims, the route stays bright.
                if (night) drawRect(Color.Black.copy(alpha = 0.45f), size = Size(side, side))
                if (route != null && world != null) {
                    fun px(i: Int) = Offset(((world[2 * i] - v.cx) * tilePx + side / 2).toFloat(),
                        ((world[2 * i + 1] - v.cy) * tilePx + side / 2).toFloat())
                    fun path(fromKm: Double, toKm: Double): Path {
                        val p = Path()
                        val a = indexAt(route, fromKm)
                        val b = indexAt(route, toKm) + 1
                        for (i in a..minOf(b, route.points.lastIndex)) {
                            val o = px(i)
                            if (i == a) p.moveTo(o.x, o.y) else p.lineTo(o.x, o.y)
                        }
                        return p
                    }
                    val from = (g?.kmAlong ?: 0.0) - 0.3
                    val ahead = path(from, route.distanceKm)
                    val stroke = { width: Float -> Stroke(width, cap = StrokeCap.Round, join = StrokeJoin.Round) }
                    drawPath(ahead, Color.White, style = stroke(line * 1.6f))
                    drawPath(ahead, ROAD, style = stroke(line))
                    route.congestion.filter { it.toKm > from }.forEach { run ->
                        drawPath(path(maxOf(run.fromKm, from), run.toKm),
                            if (run.level == "severe") SEVERE else HEAVY, style = stroke(line))
                    }
                    val end = route.points.last()
                    val e = Offset(((MapMath.worldX(end.lon, zoom) - v.cx) * tilePx + side / 2).toFloat(),
                        ((MapMath.worldY(end.lat, zoom) - v.cy) * tilePx + side / 2).toFloat())
                    drawCircle(Color.White, dot * 1.25f, e)
                    drawCircle(if (s.lastLeg) END else STOP, dot, e)
                }
            }
            // The car: an arrow pointing the way the road goes (up).
            val c = Offset(w / 2f, h * 0.72f)
            val r = dot * 1.6f
            val arrow = Path().apply {
                moveTo(c.x, c.y - r)
                lineTo(c.x + r * 0.8f, c.y + r * 0.8f)
                lineTo(c.x, c.y + r * 0.35f)
                lineTo(c.x - r * 0.8f, c.y + r * 0.8f)
                close()
            }
            drawPath(arrow, Color.White, style = Stroke(dot * 0.5f, join = StrokeJoin.Round))
            drawPath(arrow, if (onRoute) ROAD else Color.Gray)
        }
        Text("© OpenStreetMap contributors", fontSize = 10.sp,
            modifier = Modifier.align(Alignment.BottomEnd).padding(4.dp)
                .background(Color.White.copy(alpha = 0.7f)).padding(horizontal = 4.dp))
        Column(Modifier.align(Alignment.CenterEnd).padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            MapButton("+", "Zoom in") { userZoom = (userZoom + 1).coerceAtMost(3.0) }
            MapButton("−", "Zoom out") { userZoom = (userZoom - 1).coerceAtLeast(-6.0) }
            MapButton("⤢", "Overview: the whole trip") { overview = true }
        }
    }
}

private const val NAV_MIN_ZOOM = 9.0

/** The route's points in tile space at [zoom], x and y interleaved. */
private fun worldLine(route: NavRoute, zoom: Int): DoubleArray {
    val out = DoubleArray(route.points.size * 2)
    route.points.forEachIndexed { i, p ->
        out[2 * i] = MapMath.worldX(p.lon, zoom)
        out[2 * i + 1] = MapMath.worldY(p.lat, zoom)
    }
    return out
}

/** The index of the point at or just before [km]. */
private fun indexAt(route: NavRoute, km: Double): Int {
    var i = java.util.Arrays.binarySearch(route.cumKm, km.coerceIn(0.0, route.distanceKm))
    if (i < 0) i = -i - 2
    return i.coerceIn(0, route.points.lastIndex)
}

/** On the main screen while navigation runs behind it: back to the directions. */
@Composable
fun NavigationReturnBar(onOpen: () -> Unit) {
    val nav by Navigator.state.collectAsState()
    val s = nav ?: return
    val g = s.guidance
    val next = g?.next
    Button(onClick = onOpen, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(if (next != null && !g.offRoute && s.phase == Navigator.Phase.DRIVING)
            "${NavText.arrow(next)} ${NavText.distance(g.toNextKm)} · ${next.instruction}"
        else "Navigation: ${s.note.ifBlank { s.trip.destination.label }}",
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
