package com.mercarisniper.app.data;

import android.content.Context;
import android.content.SharedPreferences;

/** Réglages persistants de l'application. */
public final class Prefs {

    private static final String FILE = "sniper";
    private static final String KEY_SERVER = "server_url";

    private Prefs() {
    }

    private static SharedPreferences of(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    /** Adresse du bot, ou null tant qu'elle n'a pas été configurée. */
    public static String serverUrl(Context context) {
        return of(context).getString(KEY_SERVER, null);
    }

    public static void setServerUrl(Context context, String url) {
        of(context).edit().putString(KEY_SERVER, url).apply();
    }

    /**
     * Tolère une saisie sans schéma : « 192.168.1.20:8420 » suffit, et le
     * port par défaut est ajouté si on n'en donne pas.
     */
    public static String normalise(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) {
            return "";
        }
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        // Pas de port explicite après l'hôte : on ajoute celui du bot.
        int schemeEnd = value.indexOf("://") + 3;
        String hostPart = value.substring(schemeEnd);
        if (!hostPart.contains(":") && !hostPart.contains("/")) {
            value = value + ":8420";
        }
        return value;
    }
}
