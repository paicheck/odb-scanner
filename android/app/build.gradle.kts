plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.odbscanner"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.odbscanner"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildFeatures {
        compose = true
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

    // JVM unit tests (run with :app:testDebugUnitTest). Elm327ProtocolTest pins
    // the MEB wire form to the same captured truth the Python suite checks, so
    // the phone cannot drift from the desktop transport.
    testImplementation("junit:junit:4.13.2")
}

// This project lives inside a OneDrive-synced folder, and OneDrive's sync agent
// holds locks on files in build/ while Gradle rewrites them, which surfaces as
// "Unable to delete directory ... cacheable". Point the build output at a
// non-synced directory under the user home instead.
layout.buildDirectory.set(file(System.getProperty("user.home") + "/odb-scanner-build/app"))
