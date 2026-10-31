package com.odbscanner.ai

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** A route change by voice needs the driver's yes to the change that was put to them. */
class ConfirmGateTest {
    @Test
    fun onlyTheChangeThatWasAskedAboutGoesThrough() {
        val g = ConfirmGate(120_000L)
        assertFalse(g.pass("avoid_stop:101", confirm = false, nowMs = 0L))     // asks
        assertTrue(g.pass("avoid_stop:101", confirm = true, nowMs = 30_000L))  // yes
        assertFalse(g.pass("avoid_stop:101", confirm = true, nowMs = 31_000L)) // not twice
    }

    @Test
    fun aConfirmWithNothingAskedOrForSomethingElseIsRefused() {
        val g = ConfirmGate(120_000L)
        assertFalse(g.pass("network:shell", confirm = true, nowMs = 0L))        // never asked
        assertFalse(g.pass("network:shell", confirm = false, nowMs = 1_000L))
        assertFalse(g.pass("network:ionity", confirm = true, nowMs = 2_000L))   // a different one
        assertFalse(g.pass("network:shell", confirm = true, nowMs = 3_000L))    // replaced by it
    }

    @Test
    fun aYesTooLateIsAskedAgain() {
        val g = ConfirmGate(120_000L)
        assertFalse(g.pass("use:1>2", confirm = false, nowMs = 0L))
        assertFalse(g.pass("use:1>2", confirm = true, nowMs = 121_000L))
        assertTrue(g.pass("use:1>2", confirm = true, nowMs = 150_000L))         // the late one re-asked
    }
}
