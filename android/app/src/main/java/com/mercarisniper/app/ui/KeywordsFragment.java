package com.mercarisniper.app.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.snackbar.Snackbar;
import com.google.android.material.textfield.TextInputEditText;
import com.mercarisniper.app.R;
import com.mercarisniper.app.data.SniperClient;
import com.mercarisniper.app.model.Keyword;

import java.util.List;

/** Onglet « Mots-clés » : ce que le bot doit chercher. */
public class KeywordsFragment extends Fragment implements MainActivity.StateObserver {

    private KeywordAdapter adapter;
    private TextInputEditText input;
    private View empty;
    private RecyclerView list;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_keywords, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        list = view.findViewById(R.id.list);
        empty = view.findViewById(R.id.empty);
        input = view.findViewById(R.id.input);
        MaterialButton add = view.findViewById(R.id.addBtn);

        adapter = new KeywordAdapter(this::removeKeyword);
        list.setLayoutManager(new LinearLayoutManager(requireContext()));
        list.setAdapter(adapter);

        add.setOnClickListener(v -> addKeyword());
        input.setOnEditorActionListener((v, actionId, event) -> {
            addKeyword();
            return true;
        });
    }

    private void addKeyword() {
        MainActivity host = host();
        if (host == null || host.client() == null) {
            return;
        }
        String keyword = input.getText() == null ? "" : input.getText().toString().trim();
        if (keyword.isEmpty()) {
            return;
        }
        input.setText("");

        host.client().addKeyword(keyword, new SniperClient.Result<List<Keyword>>() {
            @Override
            public void onSuccess(List<Keyword> keywords) {
                host.replaceKeywords(keywords);
                showMessage("Mot-clé ajouté : " + keyword);
            }

            @Override
            public void onFailure(String message) {
                showMessage("Échec de l'ajout : " + message);
            }
        });
    }

    private void removeKeyword(String keyword) {
        MainActivity host = host();
        if (host == null || host.client() == null) {
            return;
        }
        host.client().removeKeyword(keyword, new SniperClient.Result<List<Keyword>>() {
            @Override
            public void onSuccess(List<Keyword> keywords) {
                host.replaceKeywords(keywords);
                showMessage("Mot-clé retiré : " + keyword);
            }

            @Override
            public void onFailure(String message) {
                showMessage("Échec du retrait : " + message);
            }
        });
    }

    /**
     * Nom délibérément distinct de `notify` : depuis une classe anonyme,
     * un appel non qualifié à `notify(...)` résout vers Object.notify(),
     * hérité par la classe anonyme, et masque la méthode de l'englobante.
     */
    private void showMessage(String message) {
        View view = getView();
        if (view != null) {
            Snackbar.make(view, message, Snackbar.LENGTH_SHORT).show();
        }
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
        if (host == null || !isAdded() || adapter == null) {
            return;
        }
        adapter.submit(host.keywords);
        boolean isEmpty = host.keywords.isEmpty();
        empty.setVisibility(isEmpty ? View.VISIBLE : View.GONE);
        list.setVisibility(isEmpty ? View.GONE : View.VISIBLE);
    }

    @Nullable
    private MainActivity host() {
        return getActivity() instanceof MainActivity ? (MainActivity) getActivity() : null;
    }
}
