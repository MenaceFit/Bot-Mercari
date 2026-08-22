package com.mercarisniper.app.ui;

import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.core.content.ContextCompat;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.button.MaterialButton;
import com.mercarisniper.app.R;
import com.mercarisniper.app.model.Listing;
import com.mercarisniper.app.util.Format;
import com.mercarisniper.app.util.ImageLoader;
import com.mercarisniper.app.util.Tier;

import java.util.ArrayList;
import java.util.List;

/** Liste des annonces détectées. */
public class ListingAdapter extends RecyclerView.Adapter<ListingAdapter.Holder> {

    private final List<Listing> items = new ArrayList<>();

    public void submit(List<Listing> listings) {
        items.clear();
        items.addAll(listings);
        notifyDataSetChanged();
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        View view = LayoutInflater.from(parent.getContext())
                .inflate(R.layout.item_listing, parent, false);
        return new Holder(view);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        holder.bind(items.get(position));
    }

    @Override
    public int getItemCount() {
        return items.size();
    }

    static class Holder extends RecyclerView.ViewHolder {

        private final ImageView thumb;
        private final TextView tier;
        private final TextView latency;
        private final TextView price;
        private final TextView title;
        private final TextView tags;
        private final MaterialButton order;

        Holder(@NonNull View view) {
            super(view);
            thumb = view.findViewById(R.id.thumb);
            tier = view.findViewById(R.id.tier);
            latency = view.findViewById(R.id.latency);
            price = view.findViewById(R.id.price);
            title = view.findViewById(R.id.title);
            tags = view.findViewById(R.id.tags);
            order = view.findViewById(R.id.orderBtn);
        }

        void bind(Listing listing) {
            Context context = itemView.getContext();

            title.setText(listing.title);
            price.setText(Format.yen(listing.price));
            ImageLoader.load(thumb, listing.image, R.drawable.bg_thumb);

            // Palier : couleur ET libellé, jamais la couleur seule.
            Tier level = Tier.of(listing.rarity);
            tier.setText(level.label);
            tier.setTextColor(ContextCompat.getColor(context, level.textColor));
            GradientDrawable background = new GradientDrawable();
            background.setCornerRadius(context.getResources().getDisplayMetrics().density * 4);
            background.setColor(ContextCompat.getColor(context, level.backgroundColor));
            tier.setBackground(background);

            String age = Format.ago(listing.created);
            String speed = Format.latency(listing.latencyMs);
            StringBuilder meta = new StringBuilder();
            if (!speed.isEmpty()) {
                meta.append("détecté en ").append(speed);
            }
            if (!age.isEmpty()) {
                if (meta.length() > 0) {
                    meta.append(" · ");
                }
                meta.append("il y a ").append(age);
            }
            latency.setText(meta.toString());

            StringBuilder labels = new StringBuilder();
            if (listing.backfill) {
                labels.append(context.getString(R.string.backfill)).append(" · ");
            }
            for (int i = 0; i < listing.matched.size() && i < 3; i++) {
                if (i > 0) {
                    labels.append(" · ");
                }
                labels.append(listing.matched.get(i));
            }
            tags.setText(labels.toString());

            // Toute la carte mène à l'annonce ; le bouton mène à la commande.
            itemView.setOnClickListener(v -> open(context, listing.url));

            boolean orderable = listing.buyeeUrl != null && !listing.buyeeUrl.isEmpty();
            order.setVisibility(orderable ? View.VISIBLE : View.GONE);
            order.setOnClickListener(v -> open(context, listing.buyeeUrl));
        }

        private void open(Context context, String url) {
            if (url == null || url.isEmpty()) {
                return;
            }
            try {
                // Navigateur du système : la commande a besoin de la session
                // et des moyens de paiement déjà enregistrés.
                context.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (Exception ignored) {
                // Aucun navigateur installé : rien de mieux à proposer.
            }
        }
    }
}
