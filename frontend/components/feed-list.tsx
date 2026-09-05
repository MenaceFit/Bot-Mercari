'use client';

import { AnimatePresence } from 'framer-motion';
import { useMemo, useState } from 'react';
import { ListingCard } from '@/components/listing-card';
import { useStore } from '@/components/store';
import { Empty } from '@/components/ui';
import { cn } from '@/lib/format';

/**
 * Le flux peut contenir des centaines d'annonces. On n'en rend qu'une
 * fenêtre (§47) : au-delà, le navigateur passe son temps à recalculer un
 * DOM que personne ne regarde.
 */
export function FeedList({ limit = 40, showFilters = true }: { limit?: number; showFilters?: boolean }) {
  const { feed, freshKeys, sources } = useStore();
  const [source, setSource] = useState('');
  const [tier, setTier] = useState('');
  const [visible, setVisible] = useState(limit);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const item of feed) map[item.source] = (map[item.source] ?? 0) + 1;
    return map;
  }, [feed]);

  const filtered = useMemo(
    () => feed.filter((item) =>
      (!source || item.source === source) && (!tier || item.tier === tier)),
    [feed, source, tier],
  );
  const shown = filtered.slice(0, visible);

  return (
    <div className="space-y-2.5">
      {showFilters && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <Chip active={!source} onClick={() => setSource('')} label="TOUTES" count={feed.length} />
          {sources.filter((s) => s.enabled).map((s) => (
            <Chip
              key={s.source} active={source === s.source}
              onClick={() => setSource(source === s.source ? '' : s.source)}
              label={s.source.replace('sim_', '').toUpperCase()}
              count={counts[s.source] ?? 0}
            />
          ))}
          <div className="ml-auto flex items-center gap-1.5">
            {['ULTRA RARE', 'VERY RARE', 'RARE'].map((t) => (
              <Chip key={t} active={tier === t} onClick={() => setTier(tier === t ? '' : t)} label={t} />
            ))}
          </div>
        </div>
      )}

      {shown.length === 0 ? (
        <Empty
          title="Aucune annonce pour l'instant"
          hint="Le scanner mémorise silencieusement le premier passage de chaque requête, pour ne pas envoyer tout le catalogue déjà en ligne. Les nouveautés apparaissent ensuite ici sans rafraîchissement."
        />
      ) : (
        <div className="space-y-2">
          <AnimatePresence initial={false}>
            {shown.map((item) => (
              <ListingCard key={item.key} listing={item} fresh={freshKeys.has(item.key)} />
            ))}
          </AnimatePresence>
        </div>
      )}

      {filtered.length > visible && (
        <button type="button" className="btn w-full justify-center"
                onClick={() => setVisible((v) => v + limit)}>
          Afficher {Math.min(limit, filtered.length - visible)} de plus
          <span className="text-faint">({filtered.length - visible} restantes)</span>
        </button>
      )}
    </div>
  );
}

function Chip({
  active, onClick, label, count,
}: { active: boolean; onClick: () => void; label: string; count?: number }) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={active}
      className={cn(
        'px-2 py-1 rounded-md border text-2xs font-medium tracking-wide transition-colors',
        active
          ? 'border-accent/50 text-accent bg-accent/10'
          : 'border-line text-muted hover:text-ink hover:border-edge',
      )}
    >
      {label}
      {count !== undefined && <span className="ml-1.5 font-mono text-faint">{count}</span>}
    </button>
  );
}
