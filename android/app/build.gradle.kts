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
    // Aligne toutes les dépendances Kotlin sur une seule version.
    //
    // Sans ça : androidx.appcompat tire kotlin-stdlib:1.8.22 tandis qu'une
    // dépendance plus ancienne tire encore kotlin-stdlib-jdk8:1.6.21. Or
    // depuis Kotlin 1.8, les artefacts -jdk7/-jdk8 ont été fusionnés dans
    // kotlin-stdlib : avoir les deux fait échouer la compilation sur des
    // classes en double (checkDebugDuplicateClasses).
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.9.24"))

    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
