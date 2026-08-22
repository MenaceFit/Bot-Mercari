package com.mercarisniper.app.model;

import org.json.JSONObject;

/** Un mot-clé surveillé, avec son compteur de trouvailles. */
public class Keyword {

    public String keyword = "";
    public int hits;
    /** false quand aucune source ne ramène encore ce mot-clé. */
    public boolean covered = true;

    public static Keyword from(JSONObject json) {
        Keyword item = new Keyword();
        item.keyword = json.optString("keyword", "");
        item.hits = json.optInt("hits");
        item.covered = json.optBoolean("covered", true);
        return item;
    }
}
