plugins {
    id("com.android.application")
}

android {
    namespace = "com.mercarisniper.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mercarisniper.app"
        // Android 8.0 : icônes adaptatives sans PNG par densité.
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "1.0"
    }

    buildTypes {
        // On ne produit que du debug : un APK release non signé ne s'installe
        // pas, alors que le debug est signé avec la clé de debug.
        getByName("debug") {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    // Aligne toutes les dépendances Kotlin sur une seule version. Sans ça,
    // kotlin-stdlib et kotlin-stdlib-jdk8 arrivent en versions différentes et
    // dupliquent une vingtaine de classes (checkDebugDuplicateClasses).
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.9.24"))

    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")

    // REST + WebSocket. Le flux temps réel du bot passe par WebSocket ;
    // le réimplémenter à la main serait une mauvaise idée.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
