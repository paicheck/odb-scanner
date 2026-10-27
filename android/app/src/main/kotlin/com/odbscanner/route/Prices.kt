package com.odbscanner.route

/**
 * Charging prices: a port of `routing/prices.py`, held to the "usage_cost",
 * "own_prices" and "price_for" cases in tests/data/route_vectors.json.
 *
 * Order: the driver's own price for a network (Route settings), then the
 * price Open Charge Map lists (free text, pounds and pence only), then a
 * default. "Free" listings are not believed -- usually stale.
 */
object Prices {
    const val MIN_PRICE = 0.05
    const val MAX_PRICE = 2.00

    private const val NUM = """(\d+(?:\.\d+)?)"""
    private val PER_KWH = Regex("""(?:£\s*$NUM\s*p?|$NUM\s*p|$NUM\s*gbp)\s*/\s*kwh""")
    private val FEE = Regex(
        """(?:£\s*$NUM|$NUM\s*p)\s*(?:(?:connection|session|unlock)\s+)?fee|£\s*$NUM\s*\+""")
    private val AC = Regex("""\bac\b""")

    /** Groups 1, 2, 3 of both patterns: pounds, pence, pounds. */
    private fun amount(m: MatchResult): Double? {
        val g = m.groups
        return g[1]?.value?.toDouble()
            ?: g[2]?.value?.toDouble()?.div(100.0)
            ?: g[3]?.value?.toDouble()
    }

    /** (£ per kWh, £ fee per session) from UsageCost text, or null. */
    fun parseUsageCost(text: String?): Pair<Double, Double>? {
        if (text.isNullOrEmpty()) return null
        val s = text.lowercase().split(Regex("""\s+""")).filter { it.isNotEmpty() }
            .joinToString(" ")
            .replace("per kwh", "/kwh").replace("kw/h", "kwh")
        val parts = s.split(Regex("[;,]")).filter { it.isNotBlank() }
        val dc = parts.filter { "dc" in it || "rapid" in it }
        val chosen = dc.ifEmpty { parts.filter { !AC.containsMatchIn(it) }.ifEmpty { parts } }
        for (p in chosen) {
            var part = p
            if (dc.isNotEmpty()) {
                val m = AC.find(part)
                if (m != null && m.range.first > 0) part = part.substring(0, m.range.first)
            }
            val price = PER_KWH.find(part) ?: continue
            val perKwh = amount(price)!!
            if (perKwh < MIN_PRICE || perKwh > MAX_PRICE) return null
            val f = FEE.find(part)
                ?: chosen.filter { !PER_KWH.containsMatchIn(it) }.firstNotNullOfOrNull { FEE.find(it) }
            val fee = f?.let { amount(it) } ?: 0.0
            return Planner.round(perKwh, 4) to Planner.round(fee, 2)
        }
        return null
    }

    /** The driver's prices from "Ionity = 0.69" lines, as (lower-case name, £/kWh). */
    fun ownPrices(text: String): List<Pair<String, Double>> =
        text.lines().filter { "=" in it }.mapNotNull { line ->
            val (name, value) = line.split("=", limit = 2)
            val n = name.trim().lowercase()
            val v = value.trim().trimStart('£').toDoubleOrNull() ?: return@mapNotNull null
            if (n.isNotEmpty() && v >= MIN_PRICE && v <= MAX_PRICE) n to v else null
        }

    /** (£ per kWh, £ fee, where it came from) for one charger. */
    fun priceFor(
        operator: String, usageCost: String?, own: List<Pair<String, Double>>, default: Double,
    ): Triple<Double, Double, String> {
        val op = operator.lowercase()
        for ((name, v) in own) if (name in op) return Triple(v, 0.0, "your price for ${title(name)}")
        parseUsageCost(usageCost)?.let { return Triple(it.first, it.second, "Open Charge Map") }
        if (usageCost != null && "free" in usageCost.lowercase()) {
            return Triple(default, 0.0, "listed as free (often out of date); default used")
        }
        return Triple(default, 0.0, "default price")
    }

    /** Python's str.title(): upper case after any non-letter, lower case otherwise. */
    fun title(s: String): String {
        val out = StringBuilder()
        var prevLetter = false
        for (c in s) {
            out.append(if (prevLetter) c.lowercaseChar() else c.uppercaseChar())
            prevLetter = c.isLetter()
        }
        return out.toString()
    }
}
