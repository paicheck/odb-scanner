package com.odbscanner

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.odbscanner.collect.Reading
import com.odbscanner.db.Repository
import com.odbscanner.spec.MeasurementSpec
import com.odbscanner.spec.Spec
import java.util.Locale

/**
 * The human-facing readout of the collector's data.
 *
 * Four tabs: an Overview of the numbers you ask about, a Battery view with the
 * per-cell heat strips, a Modules view of the full DID map grouped into
 * like-with-like categories, and a Trends view that charts any metric over the
 * stored history. Everything is derived from the shared spec, so a DID added or
 * renamed in Python appears here with its real name and unit the next build.
 */

private enum class Category(val title: String) {
    Battery("Battery"),
    Cells("Cell voltages"),
    CellTemps("Cell temperatures"),
    Lv("12V system"),
    Charging("Charging"),
    Range("Range & efficiency"),
    Climate("Climate"),
    Drivetrain("Drivetrain"),
    Vehicle("Vehicle & GPS"),
}

/** Categories shown in the Modules tab, in display order. */
private val MODULE_CATEGORIES = listOf(
    Category.Battery, Category.Lv, Category.Charging, Category.Range,
    Category.Climate, Category.Drivetrain, Category.Vehicle,
)

/**
 * Group a DID into a human-facing category from its key and module. The spec
 * groups by physical ECU; this regroups by what the value *means*, so the 12 V
 * battery figures (spread across `energy` and `dcdc`) sit together, and the
 * motor/torque values that live on `veh_info` join the electric-drive module.
 */
private fun categoryOf(key: String, ecu: String): Category = when {
    key.startsWith("cell_v_") -> Category.Cells
    key.startsWith("cell_t_") -> Category.CellTemps
    key.startsWith("aux_12v") || key.startsWith("dcdc_") ||
        key.startsWith("dc_dc_converter") -> Category.Lv
    ecu == "chg" -> Category.Charging
    key.startsWith("range_") || key.startsWith("road_avg_consumption") ||
        key == "nv_energy_requirement" ||
        key.startsWith("average_battery_consumption") ||
        key.startsWith("hv_em_") -> Category.Range
    ecu == "climate" || key.startsWith("radiator_fan") -> Category.Climate
    ecu == "eldrive" -> Category.Drivetrain
    key.startsWith("motor_rpm") || key.startsWith("torque_") ||
        key.startsWith("actual_mechanical_torque") || key.startsWith("actual_possible") ||
        key.startsWith("inverter_") || key.startsWith("dc_link") ||
        key.startsWith("desired_link") || key.startsWith("dmcm_") ||
        key.startsWith("stator_") || key.startsWith("rotor_") ||
        key.startsWith("ecu_pcb_temperature") || key.startsWith("eldrive_odometer") ||
        key.startsWith("electric_drive_") || key.startsWith("power_electronics_") ||
        key == "driver_requested_torque" || key.startsWith("front_drive_actual") ||
        key.startsWith("rear_drive_actual") || key == "front_rear_torque_difference" ||
        key.startsWith("rated_torque_on_the_front") || key == "acceleration" ||
        key == "additional_high_voltage_consumers_total_current" -> Category.Drivetrain
    key.startsWith("odometer") || key == "gear" || key == "vin" ||
        key == "speed_kmh" || key.startsWith("steering_wheel") ||
        key.startsWith("number_of_") || key == "brake_booster_pressure" ||
        key.startsWith("coolant_circulation_pump") ||
        key.startsWith("total_distance_travelled") -> Category.Vehicle
    else -> Category.Battery
}

/** Keys offered up front in the Trends tab, in order. */
private val FAVORITE_TRENDS = listOf(
    "battery_soc_normalized", "pack_voltage", "pack_current",
    "bat_temp_min", "bat_temp_max", "range_displayed",
    "aux_12v_voltage", "aux_12v_soc", "dcdc_voltage", "dcdc_current",
    "motor_rpm_2", "outside_temp", "odometer_km",
)

private data class CellStats(
    val count: Int,
    val min: Double?,
    val max: Double?,
    val avg: Double?,
)

private data class GaugeDef(val label: String, val key: String)

private val SUMMARY_GAUGES = listOf(
    GaugeDef("State of charge", "battery_soc_normalized"),
    GaugeDef("Pack voltage", "pack_voltage"),
    GaugeDef("Pack current", "pack_current"),
    GaugeDef("Range", "range_displayed"),
    GaugeDef("Odometer", "odometer_km"),
    GaugeDef("Gear", "gear"),
    GaugeDef("12 V battery", "aux_12v_voltage"),
    GaugeDef("12 V SoC", "aux_12v_soc"),
    GaugeDef("Battery min", "bat_temp_min"),
    GaugeDef("Battery max", "bat_temp_max"),
    GaugeDef("Outside", "outside_temp"),
    GaugeDef("Motor RPM", "motor_rpm_2"),
)

private fun formatNumber(v: Double): String {
    if (!v.isFinite()) return v.toString()
    val abs = kotlin.math.abs(v)
    val decimals = when {
        abs == 0.0 -> 0
        abs >= 1000.0 -> 0
        abs >= 100.0 -> 1
        abs >= 10.0 -> 1
        abs >= 1.0 -> 2
        else -> 3
    }
    return String.format(Locale.US, "%.${decimals}f", v).trimEnd('0').trimEnd('.')
}

private fun prettyUnit(unit: String): String = when (unit) {
    "degC" -> "°C"
    else -> unit
}

private fun unitSuffix(unit: String): String {
    val u = prettyUnit(unit)
    return if (u.isEmpty()) "" else " $u"
}

private fun formatValue(value: Any?, unit: String): String = when (value) {
    null -> "—"
    is Double -> formatNumber(value) + unitSuffix(unit)
    is Float -> formatNumber(value.toDouble()) + unitSuffix(unit)
    is Long -> value.toString() + unitSuffix(unit)
    is Int -> value.toString() + unitSuffix(unit)
    is Number -> formatNumber(value.toDouble()) + unitSuffix(unit)
    is String -> value
    is Map<*, *> -> value.entries.joinToString(", ") { (k, v) -> "$k=${formatValue(v, "")}" }
    else -> value.toString()
}

private fun cellStats(values: List<Double?>): CellStats {
    val present = values.filterNotNull()
    if (present.isEmpty()) return CellStats(0, null, null, null)
    return CellStats(present.size, present.minOrNull(), present.maxOrNull(), present.average())
}

@Composable
fun ReadoutScreen(
    spec: Spec,
    readings: List<Reading>,
    repository: Repository,
    vehicleId: Long,
    modifier: Modifier = Modifier,
) {
    val measurements = remember(spec) {
        spec.profiles["meb"]?.measurements ?: emptyList()
    }
    val specByKey = remember(measurements) { measurements.associateBy { it.key } }
    val valueByKey = remember(readings) { readings.associateBy { it.key } }

    var selectedTab by remember { mutableStateOf(0) }
    var query by remember { mutableStateOf("") }

    Column(modifier) {
        TabBar(
            listOf("Overview", "Battery", "Modules", "Trends"),
            selectedTab,
        ) { selectedTab = it }

        when (selectedTab) {
            0 -> OverviewTab(valueByKey, specByKey, Modifier.weight(1f))
            1 -> BatteryTab(measurements, specByKey, valueByKey, Modifier.weight(1f))
            2 -> ModulesTab(measurements, specByKey, valueByKey, query, { query = it },
                Modifier.weight(1f))
            else -> TrendsTab(specByKey, repository, vehicleId, Modifier.weight(1f))
        }
    }
}

@Composable
private fun TabBar(tabs: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    Column {
        Row(modifier = Modifier.fillMaxWidth()) {
            tabs.forEachIndexed { i, label ->
                val isSelected = i == selected
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier
                        .weight(1f)
                        .clickable { onSelect(i) }
                        .padding(vertical = 6.dp),
                ) {
                    Text(
                        label,
                        style = MaterialTheme.typography.labelLarge,
                        color = if (isSelected) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                    )
                    Surface(
                        modifier = Modifier
                            .padding(top = 4.dp)
                            .height(2.dp)
                            .fillMaxWidth(0.6f),
                        color = if (isSelected) MaterialTheme.colorScheme.primary
                        else Color.Transparent,
                        shape = RoundedCornerShape(1.dp),
                    ) {}
                }
            }
        }
    }
}

@Composable
private fun OverviewTab(
    valueByKey: Map<String, Reading>,
    specByKey: Map<String, MeasurementSpec>,
    modifier: Modifier = Modifier,
) {
    val cellVoltages = (0 until 108).map { i ->
        (valueByKey["cell_v_" + i.toString().padStart(3, '0')]?.value as? Number)?.toDouble()
    }
    val cellTemps = (0 until 24).map { i ->
        (valueByKey["cell_t_" + i.toString().padStart(2, '0')]?.value as? Number)?.toDouble()
    }

    LazyColumn(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            val gauges = SUMMARY_GAUGES.mapNotNull { g ->
                val unit = specByKey[g.key]?.unit ?: ""
                valueByKey[g.key]?.let { g.label to formatValue(it.value, unit) }
            }
            gauges.chunked(2).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEach { (label, value) ->
                        GaugeCard(label, value, Modifier.weight(1f))
                    }
                    if (row.size == 1) {
                        Surface(
                            shape = RoundedCornerShape(10.dp),
                            color = Color.Transparent,
                            modifier = Modifier.weight(1f),
                        ) {}
                    }
                }
            }
        }
        item { SensorStatsCard("Cell voltages", cellVoltages, "V", deltaMillivolts = true) }
        item { SensorStatsCard("Cell temperatures", cellTemps, "°C", deltaMillivolts = false) }
        if (valueByKey.isEmpty()) {
            item {
                Text(
                    "No data yet. Connect to the adapter to collect a pass.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 16.dp),
                )
            }
        }
    }
}

@Composable
private fun BatteryTab(
    measurements: List<MeasurementSpec>,
    specByKey: Map<String, MeasurementSpec>,
    valueByKey: Map<String, Reading>,
    modifier: Modifier = Modifier,
) {
    val cellVoltages = (0 until 108).map { i ->
        (valueByKey["cell_v_" + i.toString().padStart(3, '0')]?.value as? Number)?.toDouble()
    }
    val cellTemps = (0 until 24).map { i ->
        (valueByKey["cell_t_" + i.toString().padStart(2, '0')]?.value as? Number)?.toDouble()
    }
    val battery = measurements
        .filter { categoryOf(it.key, it.ecuKey) == Category.Battery }
        .filter { valueByKey.containsKey(it.key) }

    LazyColumn(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item { SensorStatsCard("Cell voltages", cellVoltages, "V", deltaMillivolts = true) }
        item { SensorStatsCard("Cell temperatures", cellTemps, "°C", deltaMillivolts = false) }
        item { CategoryHeader(Category.Battery, battery.size) }
        items(battery, key = { it.key }) { m ->
            MeasurementRow(m, valueByKey.getValue(m.key))
        }
    }
}

@Composable
private fun ModulesTab(
    measurements: List<MeasurementSpec>,
    specByKey: Map<String, MeasurementSpec>,
    valueByKey: Map<String, Reading>,
    query: String,
    onQueryChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val q = query.trim()
    fun matches(m: MeasurementSpec): Boolean =
        q.isEmpty() || m.name.contains(q, ignoreCase = true) || m.key.contains(q, ignoreCase = true)

    val grouped = MODULE_CATEGORIES.associateWith { cat ->
        measurements.filter {
            categoryOf(it.key, it.ecuKey) == cat && valueByKey.containsKey(it.key) && matches(it)
        }
    }

    LazyColumn(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            OutlinedTextField(
                value = query,
                onValueChange = onQueryChange,
                label = { Text("Filter") },
                placeholder = { Text("name or key, e.g. pack or torque") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        MODULE_CATEGORIES.forEach { cat ->
            val list = grouped[cat] ?: emptyList()
            if (list.isNotEmpty()) {
                item { CategoryHeader(cat, list.size) }
                items(list, key = { it.key }) { m ->
                    MeasurementRow(m, valueByKey.getValue(m.key))
                }
            }
        }
        if (valueByKey.isEmpty()) {
            item {
                Text(
                    "No data yet. Connect to the adapter to collect a pass.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 16.dp),
                )
            }
        }
    }
}

@Composable
private fun TrendsTab(
    specByKey: Map<String, MeasurementSpec>,
    repository: Repository,
    vehicleId: Long,
    modifier: Modifier = Modifier,
) {
    var selectedKey by remember { mutableStateOf(FAVORITE_TRENDS.first()) }
    var query by remember { mutableStateOf("") }
    var history by remember { mutableStateOf<List<Double>>(emptyList()) }

    LaunchedEffect(selectedKey, vehicleId) {
        history = repository.valueHistory(vehicleId, selectedKey)
    }

    val q = query.trim()
    // All chartable keys: not the per-cell arrays (those get their own strips),
    // and not the known text-only keys.
    val chartable = specByKey.values
        .filter { !it.key.startsWith("cell_v_") && !it.key.startsWith("cell_t_") }
        .filter { it.key != "vin" && it.key != "gear" && it.key != "battery_serial" }
        .filter { q.isEmpty() || it.name.contains(q, true) || it.key.contains(q, true) }
        .sortedBy { it.name.lowercase() }

    val selected = specByKey[selectedKey]

    LazyColumn(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                label = { Text("Find a metric") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(FAVORITE_TRENDS) { key ->
                    val name = specByKey[key]?.name ?: key
                    Chip(name, selectedKey == key) { selectedKey = key }
                }
            }
        }
        item {
            TrendChart(
                title = selected?.name ?: selectedKey,
                unit = selected?.unit ?: "",
                values = history,
            )
        }
        items(chartable, key = { it.key }) { m ->
            val isSelected = m.key == selectedKey
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { selectedKey = m.key }
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        m.name,
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        m.key,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (isSelected) {
                    Text(
                        "●",
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
    }
}

@Composable
private fun TrendChart(
    title: String,
    unit: String,
    values: List<Double>,
    modifier: Modifier = Modifier,
) {
    Surface(shape = RoundedCornerShape(10.dp), tonalElevation = 1.dp, modifier = modifier) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val current = values.lastOrNull()
                Text(
                    current?.let { formatNumber(it) + unitSuffix(unit) } ?: "—",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            val min = values.minOrNull()
            val max = values.maxOrNull()
            val avg = if (values.isEmpty()) null else values.average()
            Row(
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                modifier = Modifier.padding(top = 4.dp),
            ) {
                Stat("min", min, unit)
                Stat("max", max, unit)
                Stat("avg", avg, unit)
                Text(
                    "${values.size} pts",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.align(Alignment.CenterVertically),
                )
            }
            LineChart(values, Modifier.fillMaxWidth().padding(top = 8.dp).height(150.dp))
        }
    }
}

/** A self-scaling line chart with faint min/mid/max grid lines. */
@Composable
private fun LineChart(values: List<Double>, modifier: Modifier = Modifier) {
    val lineColor = MaterialTheme.colorScheme.primary
    val gridColor = MaterialTheme.colorScheme.outlineVariant
    Canvas(modifier = modifier) {
        if (values.size < 2) return@Canvas
        val min = values.minOrNull() ?: return@Canvas
        val max = values.maxOrNull() ?: return@Canvas
        val range = (max - min).takeIf { it > 0.0 } ?: 1.0
        val stepX = size.width / (values.size - 1)
        fun y(v: Double) = size.height - ((v - min) / range).toFloat() * size.height

        listOf(0f, size.height / 2f, size.height).forEach { gy ->
            drawLine(gridColor, Offset(0f, gy), Offset(size.width, gy), strokeWidth = 1f)
        }

        val path = Path()
        values.forEachIndexed { i, v ->
            val x = i * stepX
            val yy = y(v)
            if (i == 0) path.moveTo(x, yy) else path.lineTo(x, yy)
        }
        drawPath(
            path,
            color = lineColor,
            style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round),
        )
    }
}

@Composable
private fun Chip(text: String, selected: Boolean, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = if (selected) MaterialTheme.colorScheme.primaryContainer
        else MaterialTheme.colorScheme.surfaceVariant,
        modifier = Modifier.clickable { onClick() },
    ) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) MaterialTheme.colorScheme.onPrimaryContainer
            else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
    }
}

@Composable
private fun GaugeCard(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        shape = RoundedCornerShape(10.dp),
        tonalElevation = 1.dp,
        modifier = modifier,
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                label,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun SensorStatsCard(
    title: String,
    values: List<Double?>,
    unit: String,
    deltaMillivolts: Boolean,
) {
    val stats = cellStats(values)
    Surface(
        shape = RoundedCornerShape(10.dp),
        tonalElevation = 1.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                title,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                modifier = Modifier.padding(top = 4.dp),
            ) {
                Stat("min", stats.min, unit)
                Stat("max", stats.max, unit)
                Stat("avg", stats.avg, unit)
                if (deltaMillivolts) {
                    val delta = if (stats.min != null && stats.max != null) {
                        (stats.max - stats.min) * 1000.0
                    } else null
                    Text(
                        "Δ " + (delta?.let { formatNumber(it) } ?: "—") + " mV",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            if (stats.count > 0 && stats.min != null && stats.max != null) {
                CellStrip(
                    values = values,
                    min = stats.min,
                    max = stats.max,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun Stat(label: String, value: Double?, unit: String) {
    Column {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value?.let { formatNumber(it) + unitSuffix(unit) } ?: "—",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
        )
    }
}

/** A one-cell-wide coloured strip; green = low, red = high, grey = absent. */
@Composable
private fun CellStrip(
    values: List<Double?>,
    min: Double,
    max: Double,
    modifier: Modifier = Modifier,
) {
    val low = Color(0xFF43A047)
    val high = Color(0xFFE53935)
    val missing = Color(0xFF9E9E9E)
    Canvas(modifier = modifier.fillMaxWidth().height(26.dp)) {
        if (values.isEmpty()) return@Canvas
        val slot = size.width / values.size
        val range = (max - min).takeIf { it > 0.0 } ?: 1.0
        values.forEachIndexed { i, v ->
            val color = if (v == null) missing else {
                val t = ((v - min) / range).coerceIn(0.0, 1.0).toFloat()
                lerp(low, high, t)
            }
            drawRect(
                color = color,
                topLeft = Offset(i * slot, 0f),
                size = Size(slot + 1f, size.height),
            )
        }
    }
}

@Composable
private fun CategoryHeader(category: Category, count: Int) {
    Text(
        "${category.title}  ·  $count",
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(top = 10.dp, bottom = 2.dp),
    )
}

@Composable
private fun MeasurementRow(m: MeasurementSpec, r: Reading) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                m.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                m.key,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(
                formatValue(r.value, m.unit),
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.End,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            if (r.status != "ok") {
                Text(
                    r.status,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}
