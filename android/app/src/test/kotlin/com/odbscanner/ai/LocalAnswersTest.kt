package com.odbscanner.ai

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the phone can answer by itself, from the tools' own figures, when no AI service can. */
class LocalAnswersTest {
    @Test
    fun theQuestionIsMatchedToWhatThePhoneKnows() {
        assertEquals(LocalAnswers.Intent.ARRIVAL, LocalAnswers.intent("Will I make it to the next charger?"))
        assertEquals(LocalAnswers.Intent.NEXT_STOP, LocalAnswers.intent("Where's my next charging stop"))
        assertEquals(LocalAnswers.Intent.BATTERY, LocalAnswers.intent("How much battery have I got?"))
        assertEquals(LocalAnswers.Intent.RANGE, LocalAnswers.intent("What's my range"))
        assertEquals(LocalAnswers.Intent.TRIP_LEFT, LocalAnswers.intent("how far to go"))
        assertEquals(LocalAnswers.Intent.FAULTS, LocalAnswers.intent("any fault codes?"))
        assertNull(LocalAnswers.intent("tell me a joke"))
    }

    @Test
    fun theFiguresAreTheToolsOwnWithTheirAge() {
        val battery = JSONObject("""{"charge_display_pct":{"value":61.6,"unit":"%","age":"5 min ago"}}""")
        assertEquals("The battery is at 62 per cent (read 5 min ago).",
            LocalAnswers.say(LocalAnswers.Intent.BATTERY, battery))
        val trip = JSONObject("""{"destination":"Leeds","remaining":{"km":200,"miles":124.3},
            "stops_ahead":[{"charger":"Cobham","from_here":{"km":29,"miles":18.0},"arrive_pct":34.0}],
            "live_check":{"projected_at_next_pct":31.6,"planned_at_next_pct":34.0}}""")
        assertEquals("Your next stop is Cobham, about 18 miles away. Planned arrival charge is 34 per cent.",
            LocalAnswers.say(LocalAnswers.Intent.NEXT_STOP, trip))
        assertEquals("At the rate you're using, about 32 per cent at Cobham; the plan said 34.",
            LocalAnswers.say(LocalAnswers.Intent.ARRIVAL, trip))
        assertEquals("About 124 miles to Leeds.", LocalAnswers.say(LocalAnswers.Intent.TRIP_LEFT, trip))
        assertEquals("No fault codes are stored.",
            LocalAnswers.say(LocalAnswers.Intent.FAULTS, JSONObject("""{"count":0}""")))
        assertEquals("There's no trip planned.",
            LocalAnswers.say(LocalAnswers.Intent.NEXT_STOP, JSONObject("""{"error":"no trip"}""")))
        assertNull("no figure, no answer", LocalAnswers.say(LocalAnswers.Intent.BATTERY, JSONObject("{}")))
    }

    @Test
    fun onlyWhenNoServiceCouldAnswer() {
        assertTrue(LocalAnswers.covers(AiError(AiErrorCategory.NETWORK_ERROR, "S", "x")))
        assertTrue(LocalAnswers.covers(AiError(AiErrorCategory.NOT_CONFIGURED, "S", "x")))
        assertFalse(LocalAnswers.covers(AiError(AiErrorCategory.UNSUPPORTED_CAPABILITY, "S", "x")))
    }

    @Test
    fun anOlderReadingIsSaidAsTheLastOneNeverAsNow() {
        fun battery(age: String) = JSONObject().put("charge_display_pct",
            JSONObject().put("value", 61.6).put("age", age))
        assertEquals("The last battery reading, 3 h ago, was 62 per cent.",
            LocalAnswers.say(LocalAnswers.Intent.BATTERY, battery("3 h ago")))
        assertEquals("The last battery reading was 62 per cent; when it was read is not known.",
            LocalAnswers.say(LocalAnswers.Intent.BATTERY, battery("unknown")))
        val range = JSONObject().put("range_shown_by_car",
            JSONObject().put("value", 120.2).put("unit", "miles").put("age", "2 days ago"))
        assertEquals("The last range the car showed, 2 days ago, was 120 miles.",
            LocalAnswers.say(LocalAnswers.Intent.RANGE, range))
        val trip = JSONObject("""{"stops_ahead":[{"charger":"Cobham","arrive_pct":34.0}],
            "live_check":{"projected_at_next_pct":31.6,"battery_reading_age":"25 min ago"}}""")
        assertEquals("At the rate you're using, about 32 per cent at Cobham, from the battery " +
            "reading 25 min ago.", LocalAnswers.say(LocalAnswers.Intent.ARRIVAL, trip))
    }

    @Test
    fun withoutThePhonesPositionNothingIsSaidAsFromHere() {
        val trip = JSONObject("""{"destination":"Leeds","total":{"km":200,"miles":124.3},
            "position":"unknown: no fix",
            "stops_ahead":[{"charger":"Cobham","from_start":{"km":29,"miles":18.0},"arrive_pct":34.0}]}""")
        assertEquals("The first charging stop on the plan is Cobham, about 18 miles from the " +
            "start. Planned arrival charge is 34 per cent. ${LocalAnswers.NO_POSITION}",
            LocalAnswers.say(LocalAnswers.Intent.NEXT_STOP, trip))
        assertEquals("The whole trip to Leeds is about 124 miles. ${LocalAnswers.NO_POSITION}",
            LocalAnswers.say(LocalAnswers.Intent.TRIP_LEFT, trip))
    }

    @Test
    fun aQuestionThePhoneCannotAnswerGetsAnHonestReply() {
        assertNull(LocalAnswers.intent("what's the weather in Leeds"))
        val help = LocalAnswers.cannot(null)
        assertTrue(help.contains("battery") && help.contains("fault codes"))
        assertEquals("The phone's own data has no answer to that right now.",
            LocalAnswers.cannot(LocalAnswers.Intent.BATTERY))
    }
}
