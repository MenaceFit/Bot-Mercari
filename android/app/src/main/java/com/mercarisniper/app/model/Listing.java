package com.mercarisniper.app.model;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Une annonce détectée par le bot. */
public class Listing {

    public String id = "";
    public String title = "";
    public long price;
    public String url = "";
    public String buyeeUrl = "";
    public String image = "";
    public String rarity = "PREMIUM";
    public long created;
    public long latencyMs;
    public boolean backfill;
    public final List<String> matched = new ArrayList<>();

    public static Listing from(JSONObject json) {
        Listing listing = new Listing();
        listing.id = json.optString("id", "");
        listing.title = json.optString("title", "");
        listing.price = json.optLong("price");
        listing.url = json.optString("url", "");
        listing.buyeeUrl = json.optString("buyee_url", "");
        listing.image = json.optString("image", "");
        listing.rarity = json.optString("rarity", "PREMIUM");
        listing.created = json.optLong("created");
        listing.latencyMs = json.optLong("latency_ms");
        listing.backfill = json.optBoolean("backfill", false);

        JSONArray tags = json.optJSONArray("matched");
        if (tags != null) {
            for (int i = 0; i < tags.length(); i++) {
                listing.matched.add(tags.optString(i, ""));
            }
        }
        return listing;
    }
}
