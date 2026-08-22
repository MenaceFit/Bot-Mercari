package com.mercarisniper.app.util;

import java.text.NumberFormat;
import java.util.Locale;

/** Mise en forme des valeurs affichées. */
public final class Format {

    private static final NumberFormat NUMBERS = NumberFormat.getInstance(Locale.FRANCE);

    private Format() {
    }

    public static String yen(long amount) {
        return "¥" + NUMBERS.format(amount);
    }

    public static String number(long value) {
        return NUMBERS.format(value);
    }

    /** Latence de détection, en secondes dès qu'on dépasse la seconde. */
    public static String latency(long millis) {
        if (millis <= 0) {
            return "";
        }
        if (millis < 1000) {
            return millis + " ms";
        }
        return String.format(Locale.FRANCE, "%.1f s", millis / 1000.0);
    }

    /** Âge d'une annonce, à partir d'un horodatage epoch en secondes. */
    public static String ago(long epochSeconds) {
        if (epochSeconds <= 0) {
            return "";
        }
        long seconds = Math.max(0, System.currentTimeMillis() / 1000 - epochSeconds);
        if (seconds < 60) {
            return seconds + " s";
        }
        if (seconds < 3600) {
            return (seconds / 60) + " min";
        }
        if (seconds < 86400) {
            return (seconds / 3600) + " h";
        }
        return (seconds / 86400) + " j";
    }

    public static String duration(long seconds) {
        long hours = seconds / 3600;
        long minutes = (seconds % 3600) / 60;
        if (hours > 0) {
            return hours + " h " + minutes + " min";
        }
        if (minutes > 0) {
            return minutes + " min";
        }
        return seconds + " s";
    }
}
