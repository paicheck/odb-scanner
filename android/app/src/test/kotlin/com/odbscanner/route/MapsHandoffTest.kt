package com.odbscanner.route

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The spoken answer to "send the new route to Google Maps?". */
class MapsHandoffTest {
    @Test
    fun aYesInAnyCommonFormSendsIt() {
        for (said in listOf("yes", "Yes please", "yeah", "yep go on", "OK", "okay then",
                "send it", "update Google Maps", "do it", "sure")) {
            assertTrue(said, MapsHandoff.isYes(said))
        }
    }

    @Test
    fun aNoAnywhereOrNothingClearKeepsGoogleMapsAsItIs() {
        for (said in listOf(null, "", "no", "no thanks", "don't", "not now", "yes no",
                "keep it", "leave it", "cancel", "wait", "what", "hmm")) {
            assertFalse(said.toString(), MapsHandoff.isYes(said))
        }
    }
}
