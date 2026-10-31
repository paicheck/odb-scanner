package com.odbscanner

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Paint
import android.graphics.Typeface
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import com.odbscanner.route.MapMath
import com.odbscanner.route.TripResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.ceil

private val TILE_SIZE = 160.dp       // one 256 px OSM tile, drawn large enough to read
private val MAP_HEIGHT = 260.dp
private const val TILES_KEPT = 120

/**
 * OpenStreetMap tiles, fetched one at a time (the tile policy asks for few
 * connections and an identifying User-Agent) and kept in memory. Fetching a
 * tile tells OpenStreetMap roughly which area the trip covers.
 */
internal object OsmTiles {
    private val cache = LruCache<String, ImageBitmap>(160)  // a full-screen view needs ~40

    suspend fun get(context: Context, t: MapMath.Tile): ImageBitmap? {
        cache.get(t.path)?.let { return it }
        return withContext(Dispatchers.IO) {
            runCatching {
                val conn = URL("https://tile.openstreetmap.org/${t.path}.png")
                    .openConnection() as HttpURLConnection
                try {
                    conn.connectTimeout = 10_000
                    conn.readTimeout = 10_000
                    conn.setRequestProperty("User-Agent",
                        "${context.packageName} route planner (Android)")
                    if (conn.responseCode != 200) null
                    else conn.inputStream.use { BitmapFactory.decodeStream(it) }?.asImageBitmap()
                } finally {
                    conn.disconnect()
                }
            }.getOrNull()?.also { cache.put(t.path, it) }
        }
    }
}

/**
 * The road, the start, numbered charging stops and the destination on one
 * map the driver can explore: pinch to zoom, drag to move, double-tap to zoom
 * in, + and − buttons, "Whole trip" to fit it again, and a row to jump to the
 * start, each stop or the destination. [here], when given, is drawn as the car.
 * [onExpand] adds a button that opens the map full screen.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TripMap(
    r: TripResult,
    modifier: Modifier = Modifier.fillMaxWidth().height(MAP_HEIGHT),
    here: Pair<Double, Double>? = null,
    onExpand: (() -> Unit)? = null,
) {
    val context = LocalContext.current
    val density = LocalDensity.current
    val road = MaterialTheme.colorScheme.primary
    val stopColour = Color(0xFF2E7D32)
    val endColour = Color(0xFFC62828)
    val points = remember(r) {
        r.line + r.plan.stops.map { it.charger.lat to it.charger.lon } +
            listOf(r.origin.lat to r.origin.lon, r.destination.lat to r.destination.lon)
    }
    // Where the row of places or "whole trip" sends the camera (null: the
    // whole trip), and a count so the same choice twice still moves it.
    var target by remember(r) { mutableStateOf<Pair<Double, Double>?>(null) }
    var moves by remember(r) { mutableStateOf(0) }
    fun go(to: Pair<Double, Double>?) { target = to; moves++ }
    Column {
        BoxWithConstraints(modifier.clip(RoundedCornerShape(8.dp))) {
            val w = constraints.maxWidth.toFloat()
            val h = constraints.maxHeight.toFloat()
            val tilePx = with(density) { TILE_SIZE.toPx() }
            val dot = with(density) { 11.dp.toPx() }
            val fitted = remember(points, w, h, tilePx) {
                MapMath.fitCamera(points, w, h, tilePx, dot * 2)
            }
            var camera by remember(fitted) { mutableStateOf(fitted) }
            LaunchedEffect(moves) {
                if (moves > 0) camera = target?.let { (lat, lon) ->
                    MapMath.Camera.at(lat, lon, 14.5) } ?: fitted
            }
            val view = camera.view(w, h, tilePx)
            // One store across pans and zooms, so moving the map never blanks it.
            val tiles = remember { mutableStateMapOf<MapMath.Tile, ImageBitmap>() }
            val wanted = view.tiles()
            LaunchedEffect(wanted.toSet()) {
                // Bounded: what is on screen now, plus what was, up to TILES_KEPT.
                if (tiles.size > TILES_KEPT) {
                    val keep = wanted.toSet()
                    tiles.keys.filter { it !in keep }.take(tiles.size - TILES_KEPT / 2)
                        .forEach { tiles.remove(it) }
                }
                for (t in wanted) if (t !in tiles) OsmTiles.get(context, t)?.let { tiles[t] = it }
            }
            val label = remember(density) {
                Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = android.graphics.Color.WHITE
                    textAlign = Paint.Align.CENTER
                    typeface = Typeface.DEFAULT_BOLD
                    textSize = with(density) { 12.dp.toPx() }
                }
            }
            Canvas(Modifier.fillMaxSize()
                .pointerInput(w, h, tilePx) {
                    detectTransformGestures { centroid, pan, zoom, _ ->
                        camera = camera.zoomAbout(zoom, centroid.x, centroid.y, w, h, tilePx)
                            .pan(pan.x, pan.y, tilePx)
                    }
                }
                .pointerInput(w, h, tilePx) {
                    detectTapGestures(onDoubleTap = { o ->
                        camera = camera.zoomAbout(2f, o.x, o.y, w, h, tilePx)
                    })
                }) {
                drawRect(Color(0xFFE8E6E1))
                val size = ceil(view.tilePx).toInt() + 1
                // Tiles of the zoom being shown; others stay stored for when it returns.
                tiles.forEach { (t, img) ->
                    if (t.z != view.zoom) return@forEach
                    drawImage(img, dstOffset = IntOffset(view.left(t).toInt(), view.top(t).toInt()),
                        dstSize = IntSize(size, size), filterQuality = FilterQuality.Medium)
                }
                if (r.line.size > 1) {
                    val path = Path()
                    r.line.forEachIndexed { i, (lat, lon) ->
                        if (i == 0) path.moveTo(view.x(lon), view.y(lat))
                        else path.lineTo(view.x(lon), view.y(lat))
                    }
                    drawPath(path, Color.White, style = Stroke(dot * 0.75f,
                        cap = StrokeCap.Round, join = StrokeJoin.Round))
                    drawPath(path, road, style = Stroke(dot * 0.45f,
                        cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
                fun marker(lat: Double, lon: Double, colour: Color, text: String) {
                    val c = Offset(view.x(lon), view.y(lat))
                    drawCircle(Color.White, dot + dot * 0.2f, c)
                    drawCircle(colour, dot, c)
                    if (text.isNotEmpty()) {
                        drawContext.canvas.nativeCanvas.drawText(text, c.x,
                            c.y - (label.ascent() + label.descent()) / 2, label)
                    }
                }
                marker(r.origin.lat, r.origin.lon, road, "")
                r.plan.stops.forEachIndexed { i, s ->
                    marker(s.charger.lat, s.charger.lon, stopColour, "${i + 1}")
                }
                marker(r.destination.lat, r.destination.lon, endColour, "")
                here?.let { (lat, lon) ->
                    val c = Offset(view.x(lon), view.y(lat))
                    drawCircle(Color.White, dot * 0.95f, c)
                    drawCircle(Color(0xFF1565C0), dot * 0.7f, c)
                }
            }
            Column(Modifier.align(Alignment.TopEnd).padding(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                MapButton("+") { camera = camera.zoomAbout(2f, w / 2, h / 2, w, h, tilePx) }
                MapButton("−") { camera = camera.zoomAbout(0.5f, w / 2, h / 2, w, h, tilePx) }
                MapButton("⤢", "Whole trip") { go(null) }
                if (onExpand != null) MapButton("⛶", "Full screen") { onExpand() }
            }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            AssistChip(onClick = { go(null) }, label = { Text("Whole trip") })
            AssistChip(onClick = { go(r.origin.lat to r.origin.lon) }, label = { Text("Start") })
            r.plan.stops.forEachIndexed { i, s ->
                AssistChip(onClick = { go(s.charger.lat to s.charger.lon) },
                    label = { Text("${i + 1} · ${s.charger.name}".take(28)) })
            }
            AssistChip(onClick = { go(r.destination.lat to r.destination.lon) },
                label = { Text("Destination") })
        }
        Text("Pinch or double-tap to zoom, drag to move · Map © OpenStreetMap contributors · " +
            "green: charging stops · red: destination", style = MaterialTheme.typography.bodySmall)
    }
}

/** The trip's map on the whole screen: the route preview. [here] is the car, when known. */
@Composable
fun RoutePreview(r: TripResult, here: Pair<Double, Double>? = null, onClose: () -> Unit) {
    Dialog(onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().padding(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Route to ${r.destination.label}", style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.weight(1f))
                    TextButton(onClick = onClose) { Text("Close") }
                }
                // The weight is the map's share of TripMap's own column.
                TripMap(r, Modifier.fillMaxWidth().weight(1f), here = here)
            }
        }
    }
}

/** A round map button: a symbol, read out as [description] by TalkBack. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun MapButton(symbol: String, description: String = symbol, onClick: () -> Unit) {
    Surface(onClick = onClick, shape = CircleShape, shadowElevation = 2.dp,
        color = Color.White.copy(alpha = 0.92f),
        modifier = Modifier.size(40.dp).semantics { contentDescription = description }) {
        Box(contentAlignment = Alignment.Center) {
            Text(symbol, fontSize = 20.sp, color = Color(0xFF202124))
        }
    }
}
