'use client';

import { ExternalLink, Clock } from 'lucide-react';
import { type Listing } from '@/lib/api';
import { TIER, ago, cn, eur, latency, yen } from '@/lib/format';

/**
 * Une annonce.
 *
 * Trois informations priment, dans cet ordre : ce que c'est, ce que ça
 * coûte en euros, et où cliquer. Le reste — score, latence, ancienneté —
 * est secondaire et se lit en petit, sans encombrer.
 */
export function ListingCard({ listing, fresh }: { listing: Listing; fresh?: boolean }) {
  const tier = TIER[listing.tier] ?? TIER.NORMAL;

  return (
    <article
      className={cn(
        'card p-3 flex gap-3.5 items-start transition-colors animate-in',
        fresh ? 'border-accent/50 bg-accent/[.04]' : 'hover:border-line/70',
      )}
    >
      <div className="h-20 w-20 shrink-0 rounded-lg bg-raised overflow-hidden">
        {listing.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={listing.image_url} alt="" loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="h-full w-full grid place-items-center text-faint text-xs">
            —
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className={cn('tag', tier.className)}>{tier.label}</span>
          {fresh && (
            <span className="tag bg-live/15 text-live">Nouveau</span>
          )}
          <span className="ml-auto text-xs text-faint font-mono">
            {listing.score > 0 ? `${listing.score}/100` : ''}
          </span>
        </div>

        <h3 className="text-sm leading-snug line-clamp-2 mb-2 break-words"
            title={listing.title}>
          {listing.title}
        </h3>

        <div className="flex items-baseline gap-2.5 flex-wrap">
          {/* L'euro d'abord : c'est le chiffre qu'on compare à son budget. */}
          {listing.price_eur > 0 ? (
            <>
              <span className="text-lg font-semibold">{eur(listing.price_eur)}</span>
              <span className="text-xs text-faint font-mono">{yen(listing.price)}</span>
            </>
          ) : (
            <span className="text-lg font-semibold">{yen(listing.price)}</span>
          )}

          <span className="ml-auto flex items-center gap-1.5 text-xs text-faint">
            <Clock className="h-3.5 w-3.5" />
            {/* Beaucoup d'annonces n'ont pas d'horodatage fiable : « — »
                plutôt qu'un zéro qui ferait croire à l'instantané. */}
            <span title="Délai entre la publication et la détection">
              {latency(listing.latency_ms)}
            </span>
            {listing.detected_at > 0 && (
              <span className="hidden sm:inline">· {ago(listing.detected_at)}</span>
            )}
          </span>
        </div>
      </div>

      <a
        href={listing.url} target="_blank" rel="noopener noreferrer"
        className="btn btn-accent shrink-0 self-center"
      >
        <ExternalLink className="h-4 w-4" />
        <span className="hidden sm:inline">Voir</span>
      </a>
    </article>
  );
}
