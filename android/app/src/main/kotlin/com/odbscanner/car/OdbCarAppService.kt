package com.odbscanner.car

import android.content.Intent
import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.validation.HostValidator

/**
 * Android Auto entry point: Battery, Route planner and Fault codes on the car
 * screen, built only from the Car App Library's driver-safe templates.
 *
 * Everything shown is read from the phone's own database (what the collector
 * last stored) or computed by the route planner; nothing here talks to the
 * vehicle or the adapter. Declared in the "charging" category -- the closest
 * Android Auto category to an EV trip planner. An app outside the Play Store
 * shows on the car only with Android Auto's developer mode "Unknown sources"
 * turned on (see README, Android Auto).
 */
class OdbCarAppService : CarAppService() {

    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        } else {
            // Release builds talk only to the known Android Auto hosts.
            HostValidator.Builder(applicationContext)
                .addAllowedHosts(androidx.car.app.R.array.hosts_allowlist_sample)
                .build()
        }

    override fun onCreateSession(): Session = object : Session() {
        override fun onCreateScreen(intent: Intent): Screen = HomeScreen(carContext)
    }
}
