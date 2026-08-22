package com.mercarisniper.app.model;

import org.json.JSONObject;

/** Compteurs globaux affichés dans l'onglet Réglages. */
public class Stats {

    public int totalHits;
    public int listings24h;
    public int keywords;
    public int sources;
    public long itemsSeen;
    public long latencyP50Ms;
    public double pollsPerMinute;
    public long uptimeSeconds;

    public static Stats from(JSONObject json) {
        Stats stats = new Stats();
        if (json == null) {
            return stats;
        }
        stats.totalHits = json.optInt("total_hits");
        stats.listings24h = json.optInt("listings_24h");
        stats.keywords = json.optInt("keywords");
        stats.sources = json.optInt("sources");
        stats.itemsSeen = json.optLong("total_items_seen");
        stats.latencyP50Ms = json.optLong("latency_p50_ms");
        stats.pollsPerMinute = json.optDouble("polls_per_minute", 0);
        stats.uptimeSeconds = json.optLong("uptime_seconds");
        return stats;
    }
}
