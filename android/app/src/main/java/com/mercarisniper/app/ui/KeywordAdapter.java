package com.mercarisniper.app.ui;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import com.mercarisniper.app.R;
import com.mercarisniper.app.model.Keyword;

import java.util.ArrayList;
import java.util.List;

/** Liste des mots-clés surveillés. */
public class KeywordAdapter extends RecyclerView.Adapter<KeywordAdapter.Holder> {

    /** Demande de retrait d'un mot-clé. */
    public interface OnRemove {
        void remove(String keyword);
    }

    private final List<Keyword> items = new ArrayList<>();
    private final OnRemove onRemove;

    public KeywordAdapter(OnRemove onRemove) {
        this.onRemove = onRemove;
    }

    public void submit(List<Keyword> keywords) {
        items.clear();
        items.addAll(keywords);
        notifyDataSetChanged();
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        View view = LayoutInflater.from(parent.getContext())
                .inflate(R.layout.item_keyword, parent, false);
        return new Holder(view);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        holder.bind(items.get(position), onRemove);
    }

    @Override
    public int getItemCount() {
        return items.size();
    }

    static class Holder extends RecyclerView.ViewHolder {

        private final TextView name;
        private final TextView hits;
        private final ImageView warning;
        private final ImageButton remove;

        Holder(@NonNull View view) {
            super(view);
            name = view.findViewById(R.id.name);
            hits = view.findViewById(R.id.hits);
            warning = view.findViewById(R.id.warning);
            remove = view.findViewById(R.id.removeBtn);
        }

        void bind(Keyword keyword, OnRemove onRemove) {
            name.setText(keyword.keyword);
            hits.setText(keyword.hits > 0 ? String.valueOf(keyword.hits) : "·");
            // Un mot-clé non couvert ne remontera rien : on le signale.
            warning.setVisibility(keyword.covered ? View.GONE : View.VISIBLE);
            remove.setOnClickListener(v -> onRemove.remove(keyword.keyword));
        }
    }
}
