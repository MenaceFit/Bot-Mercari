package com.mercarisniper.app;

import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

/**
 * Client du dashboard Mercari Sniper.
 *
 * L'application n'exécute PAS le bot : Android arrête les processus en
 * arrière-plan (Doze), une boucle de scan à 2 s y serait tuée en quelques
 * minutes et viderait la batterie. Le bot tourne sur le PC ; cette
 * application en est la fenêtre.
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "sniper";
    private static final String KEY_SERVER = "server_url";

    private WebView web;
    private SwipeRefreshLayout refresh;
    private LinearLayout offline;
    private String serverUrl;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        refresh = findViewById(R.id.refresh);
        offline = findViewById(R.id.offline);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);        // le dashboard est en JS
        settings.setDomStorageEnabled(true);        // mémorise le thème choisi
        settings.setMediaPlaybackRequiresUserGesture(false);  // alerte sonore

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                // Buyee et Mercari s'ouvrent dans le vrai navigateur : la
                // commande a besoin de la session et des moyens de paiement
                // déjà enregistrés, qu'une WebView isolée n'a pas.
                if (isExternal(uri)) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    return true;
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                refresh.setRefreshing(false);
                offline.setVisibility(View.GONE);
                web.setVisibility(View.VISIBLE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // Seul l'échec du document principal signifie « bot injoignable ».
                if (request.isForMainFrame()) {
                    refresh.setRefreshing(false);
                    web.setVisibility(View.GONE);
                    offline.setVisibility(View.VISIBLE);
                }
            }
        });

        refresh.setOnRefreshListener(() -> web.reload());
        ((Button) findViewById(R.id.retryBtn)).setOnClickListener(v -> load());
        ((Button) findViewById(R.id.changeBtn)).setOnClickListener(v -> askForServer());

        // Le bouton retour navigue dans l'historique du dashboard avant de
        // quitter l'application.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (web.canGoBack()) {
                    web.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        serverUrl = prefs().getString(KEY_SERVER, null);
        if (serverUrl == null) {
            askForServer();
        } else {
            load();
        }
    }

    private boolean isExternal(Uri uri) {
        if (serverUrl == null) return true;
        String host = uri.getHost();
        if (host == null) return false;
        String ourHost = Uri.parse(serverUrl).getHost();
        return ourHost == null || !host.equalsIgnoreCase(ourHost);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private void load() {
        if (serverUrl == null) {
            askForServer();
            return;
        }
        offline.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(serverUrl);
    }

    private void askForServer() {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint(R.string.setup_hint);
        input.setText(serverUrl != null ? serverUrl : "http://192.168.1.");

        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setPadding(pad, pad / 2, pad, 0);
        wrapper.addView(input);

        new AlertDialog.Builder(this)
                .setTitle(R.string.setup_title)
                .setMessage(R.string.setup_message)
                .setView(wrapper)
                .setCancelable(serverUrl != null)
                .setPositiveButton(R.string.save, (dialog, which) -> {
                    String value = normalise(input.getText().toString());
                    if (value.isEmpty()) {
                        askForServer();
                        return;
                    }
                    serverUrl = value;
                    prefs().edit().putString(KEY_SERVER, value).apply();
                    load();
                })
                .show();
    }

    /** Tolère une saisie sans schéma : « 192.168.1.20:8420 » suffit. */
    private static String normalise(String raw) {
        String value = raw.trim();
        if (value.isEmpty()) return "";
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        return value;
    }
}
