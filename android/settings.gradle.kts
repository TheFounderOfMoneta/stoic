pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

// Android SDK есть только в CI / Android Studio. Без него собирается только :core.
val hasAndroidSdk = System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        if (hasAndroidSdk) google()
        mavenCentral()
    }
}

rootProject.name = "ritm"
include(":core")
if (hasAndroidSdk) include(":app")
