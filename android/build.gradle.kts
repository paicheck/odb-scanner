plugins {
    id("com.android.application") version "8.5.0" apply false
    id("org.jetbrains.kotlin.android") version "2.1.10" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.10" apply false
    id("com.google.devtools.ksp") version "2.1.10-1.0.31" apply false
}

// Redirect root build directory outside OneDrive to prevent file locking issues
// -PodbBuildDir=<dir> moves it (and the app's) elsewhere: see app/build.gradle.kts.
layout.buildDirectory.set(file(((findProperty("odbBuildDir") as String?)
    ?: (System.getProperty("user.home") + "/odb-scanner-build")) + "/root"))
