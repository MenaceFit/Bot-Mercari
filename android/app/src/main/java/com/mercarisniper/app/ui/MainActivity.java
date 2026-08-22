package com.mercarisniper.app.ui;

import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;

import com.google.android.material.bottomnavigation.BottomNavigationView;
import com.mercarisniper.app.R;
import com.mercarisniper.app.data.Prefs;
import com.mercarisniper.app.data.SniperClient;
import com.mercarisniper.app.model.Keyword;
import com.mercarisniper.app.model.Listing;
import com.mercarisniper.app.model.Stats;

import java.util.ArrayList;
import java.util.List;

/**
 * Écran principal : trois onglets alimentés par une connexion unique au bot.
 *
 * L'activité détient le client et l'état ; les fragments s'y abonnent. Ça
 * évite d'ouvrir trois WebSockets et garde le flux cohérent d'un onglet à
 * l'autre.
 */
public class MainActivity extends AppCompatActivity implements SniperClient.StreamListener {

    /** Un fragment qui veut être prévenu des changements d'état. */
    public interface StateObserver {
        void onStateChanged();
    }

    private static final int MAX_FEED = 300;

    private SniperClient client;
    private View statusBar;
    private TextView statusText;

    private final List<StateObserver> observers = new ArrayList<>();

    // État partagé, lu par les fragments.
    public final List<Listing> feed = new ArrayList<>();
    public final List<Keyword> keywords = new ArrayList<>();
    public Stats stats = new Stats();
    public boolean connected;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        String server = Prefs.serverUrl(this);
        if (server == null) {
            // Pas encore configuré : on part sur l'écran de connexion plutôt
            // que d'afficher une interface vide.
            startActivity(new Intent(this, SetupActivity.class));
            finish();
            return;
        }

        setContentView(R.layout.activity_main);
        statusBar = findViewById(R.id.statusBar);
        statusText = findViewById(R.id.statusText);

        client = new SniperClient(server);

        BottomNavigationView nav = findViewById(R.id.bottomNav);
        nav.setOnItemSelectedListener(item -> {
            int id = item.getItemId();
            if (id == R.id.nav_feed) {
                return show(new FeedFragment());
            }
            if (id == R.id.nav_keywords) {
                return show(new KeywordsFragment());
            }
            if (id == R.id.nav_settings) {
                return show(new SettingsFragment());
            }
            return false;
        });

        if (savedInstanceState == null) {
            nav.setSelectedItemId(R.id.nav_feed);
        }
    }

    private boolean show(Fragment fragment) {
        getSupportFragmentManager()
                .beginTransaction()
                .replace(R.id.container, fragment)
                .commit();
        return true;
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (client != null) {
            client.openStream(this);
        }
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (client != null) {
            client.closeStream();
        }
    }

    public SniperClient client() {
        return client;
    }

    // ── Abonnement des fragments ──────────────────────────────────────────
    public void addObserver(StateObserver observer) {
        if (!observers.contains(observer)) {
            observers.add(observer);
        }
        observer.onStateChanged();   // l'état courant, tout de suite
    }

    public void removeObserver(StateObserver observer) {
        observers.remove(observer);
    }

    private void notifyObservers() {
        for (StateObserver observer : new ArrayList<>(observers)) {
            observer.onStateChanged();
        }
    }

    /** Appelé par l'onglet Mots-clés après un ajout ou un retrait. */
    public void replaceKeywords(List<Keyword> updated) {
        keywords.clear();
        keywords.addAll(updated);
        notifyObservers();
    }

    // ── Flux temps réel ───────────────────────────────────────────────────
    @Override
    public void onSnapshot(SniperClient.Snapshot snapshot) {
        feed.clear();
        feed.addAll(snapshot.feed);
        keywords.clear();
        keywords.addAll(snapshot.keywords);
        stats = snapshot.stats;
        notifyObservers();
    }

    @Override
    public void onListing(Listing listing) {
        feed.add(0, listing);
        while (feed.size() > MAX_FEED) {
            feed.remove(feed.size() - 1);
        }
        notifyObservers();
    }

    @Override
    public void onStats(Stats updated) {
        stats = updated;
        notifyObservers();
    }

    @Override
    public void onConnected() {
        connected = true;
        statusBar.setVisibility(View.GONE);
        notifyObservers();
    }

    @Override
    public void onDisconnected() {
        connected = false;
        statusBar.setVisibility(View.VISIBLE);
        statusText.setText(R.string.status_offline);
        notifyObservers();
    }
}
