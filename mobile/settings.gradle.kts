@file:Suppress("UnstableApiUsage")

enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS")

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "smart-youth"
include(":app")

// 系统盘只剩百来 MB，构建产物一律落在移动硬盘（gradle.properties 里的 project-cache-dir 同理）
gradle.beforeProject {
    layout.buildDirectory.set(file("/Volumes/AnkoQAQ/kotlin-dev/build/smart-youth/" + path.replace(':', '_')))
}
