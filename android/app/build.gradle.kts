plugins {
    id("com.android.application")
}

// A versão do app acompanha a do programa do computador, que dá nome às releases.
val projectVersion: String = Regex("__version__\\s*=\\s*\"([^\"]+)\"")
    .find(rootDir.resolve("../desktop/connectlinux/__init__.py").readText())
    ?.groupValues?.get(1)
    ?: error("versão não encontrada em desktop/connectlinux/__init__.py")
val projectVersionCode: Int = projectVersion.split('.').map { it.toInt() }
    .let { (major, minor, patch) -> major * 10000 + minor * 100 + patch }

android {
    namespace = "dev.connectlinux.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.connectlinux.app"
        minSdk = 26
        targetSdk = 35
        versionCode = projectVersionCode
        versionName = projectVersion
    }

    val storePath = providers.gradleProperty("CONNECTLINUX_STORE_FILE").orNull
    signingConfigs {
        if (storePath != null) {
            create("release") {
                storeFile = file(storePath)
                storePassword = providers.gradleProperty("CONNECTLINUX_STORE_PASSWORD").get()
                keyAlias = providers.gradleProperty("CONNECTLINUX_KEY_ALIAS").get()
                keyPassword = providers.gradleProperty("CONNECTLINUX_KEY_PASSWORD").get()
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (storePath != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
}
