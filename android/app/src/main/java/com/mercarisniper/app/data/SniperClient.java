package com.mercarisniper.app.data;

import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.mercarisniper.app.model.Keyword;
import com.mercarisniper.app.model.Listing;
import com.mercarisniper.app.model.Stats;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/**
 * Accès au bot : REST pour les actions, WebSocket pour le flux temps réel.
 *
 * Toutes les réponses sont remises sur le thread principal, pour que
 * l'interface n'ait jamais à s'en préoccuper.
 */
public class SniperClient {

    private static final MediaType JSON_TYPE = MediaType.get("application/json");

    /** Résultat d'un appel, réussi ou non — jamais les deux. */
    public interface Result<T> {
        void onSuccess(T value);

        void onFailure(String message);
    }

    /** Événements du flux temps réel. */
    public interface StreamListener {
        void onSnapshot(Snapshot snapshot);

        void onListing(Listing listing);

        void onStats(Stats stats);

        void onConnected();

        void onDisconnected();
    }

    /** État complet envoyé à l'ouverture de la connexion. */
    public static class Snapshot {
        public final List<Listing> feed = new ArrayList<>();
        public final List<Keyword> keywords = new ArrayList<>();
        public Stats stats = new Stats();
    }

    private final OkHttpClient http;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final String baseUrl;

    @Nullable private WebSocket socket;
    @Nullable private StreamListener listener;
    private boolean streaming;
    private long retryDelayMs = 1000;

    public SniperClient(String baseUrl) {
        this.baseUrl = baseUrl;
        this.http = new OkHttpClient.Builder()
                .connectTimeout(6, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)   // 0 = pas de coupure du WebSocket
                .pingInterval(20, TimeUnit.SECONDS)      // détecte un PC éteint
                .build();
    }

    public String baseUrl() {
        return baseUrl;
    }

    // ── Vérification ──────────────────────────────────────────────────────
    /** Ping utilisé par l'écran de connexion avant d'enregistrer l'adresse. */
    public void ping(Result<Boolean> result) {
        Request request = new Request.Builder().url(baseUrl + "/api/health").build();
        http.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(@NonNull Call call, @NonNull IOException e) {
                post(() -> result.onFailure(e.getMessage() == null ? "réseau" : e.getMessage()));
            }

            @Override
            public void onResponse(@NonNull Call call, @NonNull Response response) {
                try (Response closeable = response) {
                    boolean ok = closeable.isSuccessful();
                    post(() -> {
                        if (ok) {
                            result.onSuccess(true);
                        } else {
                            result.onFailure("HTTP " + closeable.code());
                        }
                    });
                }
            }
        });
    }

    // ── État ──────────────────────────────────────────────────────────────
    public void fetchState(Result<Snapshot> result) {
        Request request = new Request.Builder().url(baseUrl + "/api/state").build();
        http.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(@NonNull Call call, @NonNull IOException e) {
                post(() -> result.onFailure(String.valueOf(e.getMessage())));
            }

            @Override
            public void onResponse(@NonNull Call call, @NonNull Response response) {
                handleJson(response, result, SniperClient::parseSnapshot);
            }
        });
    }

    // ── Mots-clés ─────────────────────────────────────────────────────────
    public void addKeyword(String keyword, Result<List<Keyword>> result) {
        JSONObject body = new JSONObject();
        try {
            body.put("keyword", keyword);
        } catch (Exception ignored) {
            // JSONObject.put ne lève que sur une clé nulle : impossible ici.
        }
        Request request = new Request.Builder()
                .url(baseUrl + "/api/keywords")
                .post(RequestBody.create(body.toString(), JSON_TYPE))
                .build();
        enqueueKeywords(request, result);
    }

    public void removeKeyword(String keyword, Result<List<Keyword>> result) {
        Request request = new Request.Builder()
                .url(baseUrl + "/api/keywords/" + encode(keyword))
                .delete()
                .build();
        enqueueKeywords(request, result);
    }

    private void enqueueKeywords(Request request, Result<List<Keyword>> result) {
        http.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(@NonNull Call call, @NonNull IOException e) {
                post(() -> result.onFailure(String.valueOf(e.getMessage())));
            }

            @Override
            public void onResponse(@NonNull Call call, @NonNull Response response) {
                handleJson(response, result, json -> parseKeywords(json.optJSONArray("keywords")));
            }
        });
    }

    // ── Flux temps réel ───────────────────────────────────────────────────
    public void openStream(StreamListener streamListener) {
        this.listener = streamListener;
        this.streaming = true;
        connectSocket();
    }

    public void closeStream() {
        streaming = false;
        listener = null;
        if (socket != null) {
            socket.close(1000, "fermeture");
            socket = null;
        }
    }

    private void connectSocket() {
        if (!streaming) {
            return;
        }
        Request request = new Request.Builder()
                .url(baseUrl.replaceFirst("^http", "ws") + "/ws")
                .build();

        socket = http.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onOpen(@NonNull WebSocket webSocket, @NonNull Response response) {
                retryDelayMs = 1000;
                post(() -> {
                    if (listener != null) {
                        listener.onConnected();
                    }
                });
            }

            @Override
            public void onMessage(@NonNull WebSocket webSocket, @NonNull String text) {
                dispatch(text);
            }

            @Override
            public void onFailure(@NonNull WebSocket webSocket, @NonNull Throwable t,
                                  @Nullable Response response) {
                scheduleReconnect();
            }

            @Override
            public void onClosed(@NonNull WebSocket webSocket, int code, @NonNull String reason) {
                scheduleReconnect();
            }
        });
    }

    private void scheduleReconnect() {
        post(() -> {
            if (listener != null) {
                listener.onDisconnected();
            }
        });
        if (!streaming) {
            return;
        }
        // Recul exponentiel plafonné : ne martèle pas un PC éteint.
        long delay = retryDelayMs;
        retryDelayMs = Math.min(retryDelayMs * 2, 15000);
        main.postDelayed(this::connectSocket, delay);
    }

    private void dispatch(String text) {
        final JSONObject message;
        try {
            message = new JSONObject(text);
        } catch (Exception e) {
            return;   // trame illisible : on l'ignore plutôt que de tomber
        }
        String type = message.optString("type");
        post(() -> {
            if (listener == null) {
                return;
            }
            switch (type) {
                case "snapshot":
                    listener.onSnapshot(parseSnapshot(message.optJSONObject("data")));
                    break;
                case "listing":
                    JSONObject item = message.optJSONObject("data");
                    if (item != null) {
                        listener.onListing(Listing.from(item));
                    }
                    break;
                case "stats":
                    listener.onStats(Stats.from(message.optJSONObject("data")));
                    break;
                default:
                    break;   // les autres événements n'intéressent pas le mobile
            }
        });
    }

    // ── Utilitaires ───────────────────────────────────────────────────────
    private interface Parser<T> {
        T parse(JSONObject json);
    }

    private <T> void handleJson(Response response, Result<T> result, Parser<T> parser) {
        try (Response closeable = response) {
            String raw = closeable.body() == null ? "" : closeable.body().string();
            if (!closeable.isSuccessful()) {
                post(() -> result.onFailure("HTTP " + closeable.code()));
                return;
            }
            T parsed = parser.parse(new JSONObject(raw));
            post(() -> result.onSuccess(parsed));
        } catch (Exception e) {
            post(() -> result.onFailure(String.valueOf(e.getMessage())));
        }
    }

    static Snapshot parseSnapshot(@Nullable JSONObject data) {
        Snapshot snapshot = new Snapshot();
        if (data == null) {
            return snapshot;
        }
        snapshot.stats = Stats.from(data.optJSONObject("stats"));
        snapshot.keywords.addAll(parseKeywords(data.optJSONArray("keywords")));

        JSONArray feed = data.optJSONArray("feed");
        if (feed != null) {
            for (int i = 0; i < feed.length(); i++) {
                JSONObject item = feed.optJSONObject(i);
                if (item != null) {
                    snapshot.feed.add(Listing.from(item));
                }
            }
        }
        return snapshot;
    }

    static List<Keyword> parseKeywords(@Nullable JSONArray array) {
        List<Keyword> keywords = new ArrayList<>();
        if (array == null) {
            return keywords;
        }
        for (int i = 0; i < array.length(); i++) {
            JSONObject item = array.optJSONObject(i);
            if (item != null) {
                keywords.add(Keyword.from(item));
            }
        }
        return keywords;
    }

    private static String encode(String value) {
        try {
            return java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20");
        } catch (Exception e) {
            return value;
        }
    }

    private void post(Runnable action) {
        main.post(action);
    }
}
