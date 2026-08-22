package com.mercarisniper.app.ui;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import com.mercarisniper.app.R;
import com.mercarisniper.app.data.SniperClient;

/** Onglet « Flux » : les annonces détectées, la plus récente en tête. */
public class FeedFragment extends Fragment implements MainActivity.StateObserver {

    private final ListingAdapter adapter = new ListingAdapter();
    private SwipeRefreshLayout refresh;
    private View empty;
    private TextView emptyTitle;
    private TextView emptyBody;
    private RecyclerView list;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_feed, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        list = view.findViewById(R.id.list);
        refresh = view.findViewById(R.id.refresh);
        empty = view.findViewById(R.id.empty);
        emptyTitle = view.findViewById(R.id.emptyTitle);
        emptyBody = view.findViewById(R.id.emptyBody);

        list.setLayoutManager(new LinearLayoutManager(requireContext()));
        list.setAdapter(adapter);

        refresh.setOnRefreshListener(this::reload);
    }

    private void reload() {
        MainActivity host = host();
        if (host == null || host.client() == null) {
            refresh.setRefreshing(false);
            return;
        }
        host.client().fetchState(new SniperClient.Result<SniperClient.Snapshot>() {
            @Override
            public void onSuccess(SniperClient.Snapshot snapshot) {
                refresh.setRefreshing(false);
                host.onSnapshot(snapshot);
            }

            @Override
            public void onFailure(String message) {
                refresh.setRefreshing(false);
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
        adapter.submit(host.feed);

        boolean isEmpty = host.feed.isEmpty();
        empty.setVisibility(isEmpty ? View.VISIBLE : View.GONE);
        list.setVisibility(isEmpty ? View.GONE : View.VISIBLE);

        if (isEmpty) {
            // Sans mot-clé, le bot ne peut rien trouver : on dit quoi faire
            // plutôt que de laisser croire à une panne.
            boolean noKeywords = host.keywords.isEmpty();
            emptyTitle.setText(noKeywords ? R.string.feed_empty_kw_title : R.string.feed_empty_title);
            emptyBody.setText(noKeywords ? R.string.feed_empty_kw_body : R.string.feed_empty_body);
        }
    }

    @Nullable
    private MainActivity host() {
        return getActivity() instanceof MainActivity ? (MainActivity) getActivity() : null;
    }
}
