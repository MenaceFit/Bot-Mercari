package com.mercarisniper.app.ui;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;

import com.google.android.material.button.MaterialButton;
import com.mercarisniper.app.R;
import com.mercarisniper.app.data.Prefs;
import com.mercarisniper.app.model.Stats;
import com.mercarisniper.app.util.Format;

/** Onglet « Réglages » : connexion et compteurs. */
public class SettingsFragment extends Fragment implements MainActivity.StateObserver {

    private TextView server;
    private TextView state;
    private TextView stats;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_settings, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        server = view.findViewById(R.id.serverValue);
        state = view.findViewById(R.id.connectionState);
        stats = view.findViewById(R.id.statsValue);

        MaterialButton change = view.findViewById(R.id.changeBtn);
        MaterialButton browser = view.findViewById(R.id.browserBtn);

        change.setOnClickListener(v -> {
            startActivity(new Intent(requireContext(), SetupActivity.class));
            requireActivity().finish();
        });

        browser.setOnClickListener(v -> {
            String url = Prefs.serverUrl(requireContext());
            if (url == null) {
                return;
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (Exception ignored) {
                // Aucun navigateur : rien de mieux à proposer.
            }
        });
    }

    @Override
    public void onResume() {
        super.onResume();
        MainActivity host = host();
        if (host != null) {
            host.addObserver(this);
        }
    }

    @Override
    public void onPause() {
        super.onPause();
        MainActivity host = host();
        if (host != null) {
            host.removeObserver(this);
        }
    }

    @Override
    public void onStateChanged() {
        MainActivity host = host();
        if (host == null || !isAdded()) {
            return;
        }

        server.setText(Prefs.serverUrl(requireContext()));

        state.setText(host.connected ? R.string.status_live : R.string.status_offline);
        state.setTextColor(ContextCompat.getColor(requireContext(),
                host.connected ? R.color.good_ink : R.color.critical_ink));

        Stats current = host.stats;
        stats.setText(
                "Trouvailles : " + Format.number(current.totalHits) + "\n"
                        + "Sur 24 h : " + Format.number(current.listings24h) + "\n"
                        + "Mots-clés : " + current.keywords
                        + "  ·  Sources : " + current.sources + "\n"
                        + "Annonces analysées : " + Format.number(current.itemsSeen) + "\n"
                        + "Latence médiane : " + Format.latency(current.latencyP50Ms) + "\n"
                        + "Actif depuis : " + Format.duration(current.uptimeSeconds));
    }

    @Nullable
    private MainActivity host() {
        return getActivity() instanceof MainActivity ? (MainActivity) getActivity() : null;
    }
}
