'use client';

import { motion } from 'framer-motion';
import { ExternalLink, ShoppingCart, Timer } from 'lucide-react';
import { type Listing } from '@/lib/api';
import { TIER_STYLE, ago, cn, eur, latency, yen } from '@/lib/format';
import { Badge } from '@/components/ui';
import { useStore } from '@/components/store';

/**
 * Carte d'annonce du flux.
 *
 * `fresh` déclenche un halo temporaire. Il retombe après quelques secondes :
 * un halo permanent ne distinguerait plus rien.
 */
export function ListingCard({ listing, fresh }: { listing: Listing; fresh?: boolean }) {
  const tier = TIER_STYLE[listing.tier] ?? TIER_STYLE.NORMAL;
  const { sources } = useStore();
  // Le libellé lisible de la marketplace — « JDirectItems Auction »
  // plutôt que « jdirectitems_auction ».
  const label =
    sources.find((s) => s.source === listing.source)?.label ?? listing.source;
  // Les sources simulées pointent sur « example.invalid », un TLD réservé
  // qui ne résout jamais (RFC 2606). Un bouton bleu qui mène à une erreur
  // de connexion serait un mensonge de plus dans l'interface.
  const target = listing.buy_url || listing.url;
  const reachable = Boolean(target) && !target.includes('example.invalid');

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: -8, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, height: 0, marginBottom: 0 }}
      transition={{ type: 'spring', stiffness: 300, damping: 26 }}
      className={cn(
        'panel p-2.5 flex gap-3 relative overflow-hidden transition-colors',
        'hover:border-edge',
        fresh && 'border-accent/45 shadow-[0_0_0_1px_rgba(59,130,246,.18),0_0_28px_-8px_rgba(59,130,246,.4)]',
      )}
    >
      {fresh && (
        <motion.span
          aria-hidden
          className="absolute inset-0 bg-accent/[.05] pointer-events-none"
          initial={{ opacity: 1 }} animate={{ opacity: 0 }}
          transition={{ duration: 2.2, ease: 'easeOut' }}
        />
      )}

      <div className="h-14 w-14 shrink-0 rounded-md bg-raised border border-line overflow-hidden">
        {listing.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={listing.image_url} alt="" className="h-full w-full object-cover" loading="lazy" />
        ) : (
          <div className="h-full w-full flex items-center justify-center text-faint text-2xs">—</div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge className={tier.className}>{tier.label}</Badge>
          {fresh && <Badge className="text-accent border-accent/40 bg-accent/10">NEW</Badge>}
          {/* La source est mise en avant : chaque résultat DOIT dire
              d'où il vient. */}
          <Badge className="text-ink border-edge bg-white/[.08] font-semibold tracking-wide">
            {label.toUpperCase()}
          </Badge>
          {listing.via === 'crosssearch' && (
            <Badge className="text-accent border-accent/30 bg-accent/[.07]" title="Trouvée par la recherche transversale de Buyee">
              CROSS
            </Badge>
          )}
          {/* Un score de 0 n'est pas un score : c'est une annonce que le
              moteur de scoring n'a pas encore vue (recherche à la demande).
              Afficher « 0/100 » la ferait passer pour sans intérêt. */}
          {listing.score > 0 && (
            <span className="text-2xs text-faint font-mono ml-auto tabular-nums">
              {listing.score}/100
            </span>
          )}
        </div>

        <h3 className="text-xs text-ink mt-1.5 leading-snug line-clamp-2" title={listing.title}>
          {listing.title}
        </h3>

        <div className="flex items-center gap-3 mt-1.5 flex-wrap">
          <span className="font-mono text-sm text-ink tabular-nums">{yen(listing.price)}</span>
          {listing.price_eur > 0 && (
            <span className="font-mono text-2xs text-faint tabular-nums">{eur(listing.price_eur)}</span>
          )}
          {listing.keyword && (
            <span className="text-2xs text-muted truncate">« {listing.keyword} »</span>
          )}
          <span className="flex items-center gap-1 text-2xs text-faint font-mono ml-auto tabular-nums">
            <Timer className="h-3 w-3" />
            {/* Beaucoup de sources ne datent pas leurs annonces : on affiche
                alors « — », jamais un zéro qui ferait croire à l'instantané. */}
            {latency(listing.latency_ms)}
            <span className="text-edge">·</span>
            {ago(listing.detected_at)}
          </span>
        </div>
      </div>

      {/* Buyee d'abord : c'est par lui qu'on achète depuis l'étranger.
          Le lien vers la marketplace d'origine est secondaire, et n'est
          affiché que quand il est certain. */}
      <div className="flex flex-col gap-1 shrink-0 justify-center">
        {/* Un lien vide ne doit pas devenir un bouton : il ouvrirait la
            page courante. Les sources simulées n'ont pas d'URL. */}
        {reachable ? (
          <a
            href={target}
            target="_blank" rel="noopener noreferrer"
            className="btn btn-accent whitespace-nowrap"
          >
            <ShoppingCart className="h-3 w-3" />
            <span className="hidden sm:inline">Open on BUyee</span>
          </a>
        ) : (
          <span className="btn opacity-40 cursor-not-allowed whitespace-nowrap"
                title="Source simulée : pas de page réelle à ouvrir">
            <ShoppingCart className="h-3 w-3" />
            <span className="hidden sm:inline">Open on BUyee</span>
          </span>
        )}
        {listing.origin_url && (
          <a
            href={listing.origin_url}
            target="_blank" rel="noopener noreferrer"
            className="btn whitespace-nowrap"
            title={`Voir sur ${label}`}
          >
            <ExternalLink className="h-3 w-3" />
            <span className="hidden sm:inline">Open original</span>
          </a>
        )}
      </div>
    </motion.article>
  );
}
