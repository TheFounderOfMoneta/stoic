buildscript {
    val hasAndroidSdk = System.getenv("ANDROID_HOME") != null || System.getenv("ANDROID_SDK_ROOT") != null
    repositories {
        if (hasAndroidSdk) google()
        mavenCentral()
        gradlePluginPortal()
    }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.1.0")
        classpath("org.jetbrains.kotlin:kotlin-serialization:2.1.0")
        classpath("org.jetbrains.kotlin:compose-compiler-gradle-plugin:2.1.0")
        if (hasAndroidSdk) {
            classpath("com.android.tools.build:gradle:8.7.3")
            classpath("com.google.devtools.ksp:symbol-processing-gradle-plugin:2.1.0-1.0.29")
        }
    }
}
