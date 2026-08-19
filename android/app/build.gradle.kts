plugins {
    id("com.android.application")
}

android {
    namespace = "com.mercarisniper.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mercarisniper.app"
        // Android 8.0 : permet les icônes adaptatives sans PNG par densité.
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "2.2.0"
    }

    buildTypes {
        // On ne produit que du debug : un APK release non signé ne s'installe
        // pas, alors que le debug est signé avec la clé de debug et s'installe
        // directement. C'est le bon compromis pour un usage personnel.
        getByName("debug") {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
