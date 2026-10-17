plugins {
    id("com.android.application") version "8.5.0" apply false
    id("org.jetbrains.kotlin.android") version "2.0.0" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.0" apply false
    id("com.google.devtools.ksp") version "2.0.0-1.0.21" apply false
}

// Redirect root build directory outside OneDrive to prevent file locking issues
layout.buildDirectory.set(file(System.getProperty("user.home") + "/odb-scanner-build/root"))
