import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

// The product version is a single source of truth shared with the Python CLI:
// version.json at the repo root. Both apps read it, so they cannot drift and a
// release bump is one edit. `versionName` is that string verbatim; `versionCode`
// is derived from it (MAJOR*10000 + MINOR*100 + PATCH) so it stays monotonic
// without ever being hand-maintained.
val versionFile = file("../../version.json")
val appVersion: String = (JsonSlurper().parse(versionFile) as Map<String, Any>)["version"] as String
val semver = appVersion.substringBefore("-").substringBefore("+").split(".")
require(semver.size == 3) { "version.json must be MAJOR.MINOR.PATCH, got '$appVersion'" }
val appVersionCode = semver[0].toInt() * 10000 + semver[1].toInt() * 100 + semver[2].toInt()

android {
    namespace = "com.odbscanner"
    compileSdk = 34

    // The collector logs refusals and timeouts with android.util.Log. On the
    // plain JVM those stubs throw "not mocked", which would make the paths
    // that matter most for retirement (refused, timed out) untestable.
    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    defaultConfig {
        applicationId = "com.odbscanner"
        minSdk = 26
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersion
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // The wake word's speech engine (Vosk) is native code: ship it for the
        // phone (arm64) and the emulator (x86_64) only, not all four ABIs.
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildFeatures {
        compose = true
    }

    // The shared test data (the Mapbox route sample, ...) for the device tests.
    sourceSets {
        getByName("androidTest").assets.srcDir("../../tests/data")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }
}

// Room writes each schema version here (exportSchema = true in AppDatabase);
// the JSON files are committed so migrations have a history to test against.
ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.05.00"))
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.core:core-ktx:1.13.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    // Android Auto screens (car/): driver-safe templates only.
    implementation("androidx.car.app:app:1.4.0")
    // Assistant: Claude through Anthropic's official SDK (the other AI services
    // share the OpenAI-style request, sent with HttpURLConnection like the rest).
    implementation("com.anthropic:anthropic-java:3.2.0")
    // Wake word: Vosk keyword spotting, offline -- nothing leaves the phone
    // until the phrase is heard.
    implementation("com.alphacephei:vosk-android:0.3.75")

    // JVM unit tests (run with :app:testDebugUnitTest). Elm327ProtocolTest pins
    // the MEB wire form to the same captured truth the Python suite checks, so
    // the phone cannot drift from the desktop transport.
    testImplementation("junit:junit:4.13.2")
    // android.jar's org.json is a stub on the JVM; PlannerTest parses the
    // shared route vectors with the real one.
    testImplementation("org.json:json:20240303")

    // Instrumented tests (connectedDebugAndroidTest, on an emulator): the wake
    // word against recorded speech, through the real Vosk engine.
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}

// This project lives inside a OneDrive-synced folder, and OneDrive's sync agent
// holds locks on files in build/ while Gradle rewrites them, which surfaces as
// "Unable to delete directory ... cacheable". Point the build output at a
// non-synced directory under the user home instead.
layout.buildDirectory.set(file(System.getProperty("user.home") + "/odb-scanner-build/app"))
