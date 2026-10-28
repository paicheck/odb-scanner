package com.odbscanner

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Paint
import android.graphics.Typeface
import android.util.LruCache
import androidx.compose.foundation.Canvas
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

/**
 * OpenStreetMap tiles, fetched one at a time (the tile policy asks for few
 * connections and an identifying User-Agent) and kept in memory. Fetching a
 * tile tells OpenStreetMap roughly which area the trip covers.
 */
internal object OsmTiles {
    private val cache = LruCache<String, ImageBitmap>(96)   // a navigation view needs ~30

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

/** The road, the start, numbered charging stops and the destination on one map. */
@Composable
fun TripMap(r: TripResult) {
    val context = LocalContext.current
    val density = LocalDensity.current
    val road = MaterialTheme.colorScheme.primary
    val stopColour = Color(0xFF2E7D32)
    val endColour = Color(0xFFC62828)
    val points = remember(r) {
        r.line + r.plan.stops.map { it.charger.lat to it.charger.lon } +
            listOf(r.origin.lat to r.origin.lon, r.destination.lat to r.destination.lon)
    }
    Column {
        BoxWithConstraints(
            Modifier.fillMaxWidth().height(MAP_HEIGHT).clip(RoundedCornerShape(8.dp)),
        ) {
            val w = constraints.maxWidth.toFloat()
            val h = constraints.maxHeight.toFloat()
            val tilePx = with(density) { TILE_SIZE.toPx() }
            val dot = with(density) { 11.dp.toPx() }
            val view = remember(points, w, h, tilePx) { MapMath.fit(points, w, h, tilePx, dot * 2) }
            val tiles = remember(view) { mutableStateMapOf<MapMath.Tile, ImageBitmap>() }
            LaunchedEffect(view) {
                for (t in view.tiles()) OsmTiles.get(context, t)?.let { tiles[t] = it }
            }
            val label = remember(density) {
                Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = android.graphics.Color.WHITE
                    textAlign = Paint.Align.CENTER
                    typeface = Typeface.DEFAULT_BOLD
                    textSize = with(density) { 12.dp.toPx() }
                }
            }
            Canvas(Modifier.fillMaxSize()) {
                drawRect(Color(0xFFE8E6E1))
                val size = ceil(tilePx).toInt()
                tiles.forEach { (t, img) ->
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
            }
        }
        Text("Map © OpenStreetMap contributors · green: charging stops · red: destination",
            style = MaterialTheme.typography.bodySmall)
    }
}
