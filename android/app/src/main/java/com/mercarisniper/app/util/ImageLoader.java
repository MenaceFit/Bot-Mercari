package com.mercarisniper.app.util;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.widget.ImageView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Chargeur de vignettes minimal.
 *
 * Une bibliothèque comme Glide serait surdimensionnée ici : les vignettes
 * font 56 dp, sont peu nombreuses à l'écran, et une centaine de lignes
 * suffisent — sans dépendance supplémentaire ni traitement d'annotations.
 */
public final class ImageLoader {

    private static final ExecutorService POOL = Executors.newFixedThreadPool(3);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    /** ~4 Mo de vignettes en mémoire : largement de quoi couvrir le défilement. */
    private static final LruCache<String, Bitmap> CACHE = new LruCache<String, Bitmap>(4 * 1024 * 1024) {
        @Override
        protected int sizeOf(String key, Bitmap value) {
            return value.getByteCount();
        }
    };

    private ImageLoader() {
    }

    /**
     * Charge une vignette dans une ImageView recyclée.
     *
     * L'URL est posée en tag : quand la vue est réutilisée pendant que le
     * téléchargement est en cours, on jette le résultat au lieu d'afficher
     * l'image d'une autre annonce.
     */
    public static void load(ImageView target, String url, int placeholder) {
        target.setTag(url);

        if (url == null || url.isEmpty()) {
            target.setImageResource(placeholder);
            return;
        }

        Bitmap cached = CACHE.get(url);
        if (cached != null) {
            target.setImageBitmap(cached);
            return;
        }

        target.setImageResource(placeholder);
        POOL.execute(() -> {
            Bitmap bitmap = download(url);
            if (bitmap == null) {
                return;
            }
            CACHE.put(url, bitmap);
            MAIN.post(() -> {
                if (url.equals(target.getTag())) {
                    target.setImageBitmap(bitmap);
                }
            });
        });
    }

    private static Bitmap download(String url) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setConnectTimeout(6000);
            connection.setReadTimeout(6000);
            connection.setInstanceFollowRedirects(true);
            try (InputStream stream = connection.getInputStream()) {
                BitmapFactory.Options options = new BitmapFactory.Options();
                // Les vignettes s'affichent en 56 dp : inutile de décoder
                // l'image pleine résolution et de saturer la mémoire.
                options.inSampleSize = 2;
                return BitmapFactory.decodeStream(stream, null, options);
            }
        } catch (Exception e) {
            return null;   // vignette absente : le placeholder reste affiché
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
