'use client';

/**
 * Le flux, en direct et en archive.
 *
 * C'étaient deux pages — « Live Feed » et « History » — qui affichaient la
 * même chose : une liste d'annonces. La seule différence tenait à la
 * provenance des lignes (mémoire ou base). Ce n'est pas une différence que
 * l'utilisateur a envie d'arbitrer dans un menu : il veut voir ce qui
 * arrive, et pouvoir chercher dedans. Un onglet suffit.
 */

import { useCallback, useEffect, useState } from 'react';
import { Loader2, Radio, Search } from 'lucide-react';
import { FeedList } from '@/components/feed-list';
import { ListingCard } from '@/components/listing-card';
import { useStore } from '@/components/store';
import { Empty } from '@/components/ui';
import { api, type Listing } from '@/lib/api';
import { cn } from '@/lib/format';

type Tab = 'live' | 'archive';

export function FeedPage() {
  const { feed } = useStore();
  const [tab, setTab] = useState<Tab>('live');
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (search: string) => {
    setLoading(true);
    try {
      setRows(await api.listings({ limit: 200, search }));
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // On ne charge l'archive que si on la regarde, et on laisse retomber la
  // frappe : une requête par caractère saturerait la base pour rien.
  useEffect(() => {
    if (tab !== 'archive') return;
    const timer = setTimeout(() => load(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [tab, query, load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-sm font-semibold">Flux</h1>

        <div className="flex rounded-md border border-line overflow-hidden">
          {([['live', 'En direct', Radio], ['archive', 'Archive', Search]] as const)
            .map(([value, label, Icon]) => (
              <button
                key={value} type="button" onClick={() => setTab(value)}
                aria-pressed={tab === value}
                className={cn(
                  'flex items-center gap-1.5 px-2.5 py-1 text-2xs transition-colors',
                  tab === value
                    ? 'bg-white/[.07] text-ink'
                    : 'text-faint hover:text-muted',
                )}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
        </div>

        {tab === 'archive' && (
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-faint" />
            <input
              value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Chercher dans tout l'historique…"
              className="input pl-7 w-full"
              aria-label="Rechercher dans l'historique"
            />
          </div>
        )}

        <span className="text-2xs text-faint font-mono ml-auto">
          {tab === 'live'
            ? `${feed.length} en mémoire`
            : loading ? '…' : `${rows.length} trouvée(s)`}
        </span>
      </div>

      {tab === 'live' ? (
        <FeedList limit={40} />
      ) : loading ? (
        <div className="flex items-center justify-center py-16 text-faint">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : rows.length ? (
        <div className="space-y-1.5">
          {rows.map((listing) => (
            <ListingCard key={listing.key} listing={listing} />
          ))}
        </div>
      ) : (
        <Empty
          title={query ? 'Aucun résultat' : 'Archive vide'}
          hint={query
            ? 'Aucune annonce enregistrée ne contient ce texte.'
            : 'Les annonces détectées sont enregistrées ici au fil du scan.'}
        />
      )}
    </div>
  );
}
