package com.mercarisniper.app.util;

import com.mercarisniper.app.R;

/**
 * Palier de rareté d'une annonce.
 *
 * Chaque palier porte une icône ET un libellé : la couleur seule ne peut pas
 * distinguer rouge et jaune pour tout le monde.
 */
public enum Tier {

    ULTRA("ULTRA RARE", "Ultra rare", R.color.critical_ink, R.color.critical_bg, R.drawable.ic_alert),
    RARE("RARE", "Rare", R.color.warn_ink, R.color.warn_bg, R.drawable.ic_alert),
    PREMIUM("PREMIUM", "Premium", R.color.ink_2, R.color.neutral_bg, R.drawable.ic_feed);

    public final String key;
    public final String label;
    public final int textColor;
    public final int backgroundColor;
    public final int icon;

    Tier(String key, String label, int textColor, int backgroundColor, int icon) {
        this.key = key;
        this.label = label;
        this.textColor = textColor;
        this.backgroundColor = backgroundColor;
        this.icon = icon;
    }

    public static Tier of(String rarity) {
        for (Tier tier : values()) {
            if (tier.key.equals(rarity)) {
                return tier;
            }
        }
        return PREMIUM;
    }
}
