package com.odbscanner.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * The one place the foreground service publishes its state, so the activity can
 * show live status without polling Room or holding the service's coroutine.
 */
object CollectorStatusBus {
    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running

    private val _message = MutableStateFlow("")
    val message: StateFlow<String> = _message

    fun setRunning(value: Boolean) { _running.value = value }
    fun setMessage(value: String) { _message.value = value }
}
