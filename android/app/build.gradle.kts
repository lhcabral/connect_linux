plugins {
    id("com.android.application")
}

android {
    namespace = "dev.connectlinux.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.connectlinux.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "1.0.2"
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
