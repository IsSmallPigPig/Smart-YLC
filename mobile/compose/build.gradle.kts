plugins {
    kotlin("multiplatform") version "2.4.21"
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21"
    id("org.jetbrains.compose") version "1.12.2"
    id("com.android.application") version "9.5.1"
}

kotlin {
    androidTarget()

    sourceSets {
        val commonMain by getting {
            dependencies {
                implementation(compose.runtime)
                implementation(compose.foundation)
                implementation(compose.material3)
                implementation(compose.ui)
                implementation("top.yukonga.miuix.kmp:miuix-ui:0.9.5-beta01")
                implementation("top.yukonga.miuix.kmp:miuix-icons:0.9.5-beta01")
                implementation("top.yukonga.miuix.kmp:miuix-preference:0.9.5-beta01")
            }
        }

        val androidMain by getting {
            dependencies {
                implementation("androidx.activity:activity-compose:1.13.0")
            }
        }

    }
}

android {
    namespace = "com.smallylc.smartyouth"
    compileSdk = 37
    compileSdkMinor = 2

    defaultConfig {
        applicationId = "com.smallylc.smartyouth"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.2"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
