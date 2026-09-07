'use client';

/**
 * La page. Celle qu'on laisse ouverte.
 *
 * Trois chiffres en haut, les mots-clés surveillés, puis les annonces.
 * L'archive est un onglet, pas une page : c'est la même liste, lue
 * ailleurs.
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, BellOff, Loader2, Search } from 'lucide-react';
import { ListingCard } from '@/components/listing-card';
import { useStore } from '@/components/store';
import { api, type ChannelStatus, type Listing } from '@/lib/api';
import { cn, latency } from '@/lib/format';

export function FeedPage() {
  const { feed, freshKeys, metrics, keywords, sources, channels } = useStore();
  const [tab, setTab] = useState<'live' | 'archive'>('live');
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (search: string) => {
    setLoading(true);
    try { setRows(await api.listings({ limit: 200, search })); }
    catch { setRows([]); }
    finally { setLoading(false); }
  }, []);

  // On ne charge l'archive que si on la regarde, et on laisse retomber la
  // frappe : une requête par caractère saturerait la base pour rien.
  useEffect(() => {
    if (tab !== 'archive') return;
    const timer = setTimeout(() => load(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [tab, query, load]);

  const lat = metrics?.latency?.detection_ms ?? {};
  const source = sources[0];
  const items = tab === 'live' ? feed : rows;

  return (
    <div className="space-y-6">
      <Channels channels={channels} detections={metrics?.new_listings ?? 0} />

      <div className="grid grid-cols-3 gap-3">
        <Stat label="Annonces trouvées" value={metrics?.new_listings ?? 0}
              hint={`${metrics?.new_per_min ?? 0}/min`} />
        <Stat label="Délai de détection" value={latency(lat.p50 ?? 0)}
              hint={`p95 ${latency(lat.p95 ?? 0)}`} />
        {/* Le libellé vient de la source elle-même : en mode démo il dit
            « simulé », et l'écran ne peut pas faire croire à du réel. */}
        <Stat label={source?.label ?? 'Source'}
              value={source?.stats?.healthy === false ? 'En panne' : 'OK'}
              hint={`${source?.stats?.requests ?? 0} requêtes`}
              tone={source?.stats?.healthy === false ? 'danger' : 'live'} />
      </div>

      {keywords.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <span className="text-xs text-faint">Surveille</span>
          {keywords.filter((k: any) => k.enabled).map((k: any) => (
            // Un nom saisi par l'utilisateur peut être aussi long qu'il
            // veut : sans borne, la pastille pousse la page en largeur.
            <span key={k.name} className="chip pr-3 max-w-[16rem]" title={k.name}>
              <span className="truncate">{k.name}</span>
              {k.detections > 0 && (
                <span className="text-faint font-mono shrink-0">{k.detections}</span>
              )}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3">
        <div className="flex rounded-lg border border-line overflow-hidden">
          {(['live', 'archive'] as const).map((value) => (
            <button
              key={value} type="button" onClick={() => setTab(value)}
              aria-pressed={tab === value}
              className={cn(
                'h-9 px-4 text-sm transition-colors',
                tab === value ? 'bg-raised text-ink' : 'text-faint hover:text-muted',
              )}
            >
              {value === 'live' ? 'En direct' : 'Archive'}
            </button>
          ))}
        </div>

        {tab === 'archive' && (
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-faint" />
            <input
              value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Chercher…" aria-label="Chercher dans l'archive"
              className="input pl-9 w-full"
            />
          </div>
        )}

        <span className="ml-auto text-xs text-faint font-mono">
          {loading ? '…' : `${items.length}`}
        </span>
      </div>

      {loading ? (
        <div className="grid place-items-center py-20 text-faint">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : items.length ? (
        <div className="space-y-2">
          {items.map((listing) => (
            <ListingCard
              key={listing.key} listing={listing}
              fresh={tab === 'live' && freshKeys.has(listing.key)}
            />
          ))}
        </div>
      ) : (
        <Empty tab={tab} query={query} keywords={keywords.length} />
      )}
    </div>
  );
}

function Stat({ label, value, hint, tone }: {
  label: string; value: React.ReactNode; hint?: string; tone?: 'live' | 'danger';
}) {
  return (
    <div className="card p-4">
      <p className="text-xs text-faint mb-1.5">{label}</p>
      <p className={cn(
        'text-2xl font-semibold tabular-nums',
        tone === 'live' && 'text-live',
        tone === 'danger' && 'text-danger',
      )}>
        {value}
      </p>
      {hint && <p className="text-xs text-faint mt-1 font-mono">{hint}</p>}
    </div>
  );
}

function Empty({ tab, query, keywords }: {
  tab: string; query: string; keywords: number;
}) {
  const [title, hint] =
    keywords === 0
      ? ['Aucun mot-clé', 'Ajoute ce que tu cherches dans les réglages.']
      : tab === 'archive'
        ? query
          ? ['Aucun résultat', 'Rien dans l’archive ne contient ce texte.']
          : ['Archive vide', 'Les annonces détectées s’enregistrent ici.']
        : ['En attente', 'Le scanner tourne. Les nouvelles annonces apparaîtront ici.'];

  return (
    <div className="card grid place-items-center py-16 text-center px-6">
      <p className="text-sm mb-1">{title}</p>
      <p className="text-xs text-faint max-w-xs">{hint}</p>
    </div>
  );
}


/**
 * L'état des canaux de notification.
 *
 * Ce bandeau n'existait pas, et c'est ce qui a coûté le plus de temps :
 * quand rien n'arrivait sur Telegram, rien ne disait si le canal était
 * éteint, mal configuré, en panne, ou simplement au-dessus de son seuil.
 * Il ne s'affiche que quand il y a quelque chose à signaler.
 */
function Channels({ channels, detections }: {
  channels: ChannelStatus[]; detections: number;
}) {
  const problems = channels.filter(
    (c) => c.enabled && (!c.ready || c.failed > 0
      || (detections > 0 && c.sent === 0 && c.below_threshold > 0)),
  );
  const active = channels.filter((c) => c.ready);

  if (problems.length === 0 && active.length > 0) return null;
  if (problems.length === 0 && detections === 0) return null;

  if (problems.length === 0 && active.length === 0) {
    return (
      <Notice tone="warn" icon={BellOff}>
        Aucune notification configurée — les annonces n’arrivent que sur
        cette page.{' '}
        <a href="/reglages" className="underline underline-offset-2">
          Voir les réglages
        </a>
      </Notice>
    );
  }

  return (
    <>
      {problems.map((channel) => (
        <Notice key={channel.channel} tone="warn" icon={AlertTriangle}>
          <strong>{channel.label}</strong>{' '}
          {!channel.ready
            ? channel.reason
            : channel.failed > 0
              ? `${channel.failed} échec(s) — ${channel.last_error || 'sans détail'}`
              : `n’a rien envoyé : ${channel.below_threshold} annonce(s) sous le `
                + `seuil de score de ${channel.min_score}.`}
        </Notice>
      ))}
    </>
  );
}

function Notice({ tone, icon: Icon, children }: {
  tone: 'warn' | 'danger'; icon: any; children: React.ReactNode;
}) {
  return (
    <div className={cn(
      'card p-3 flex items-start gap-2.5 text-sm',
      tone === 'warn' ? 'border-warn/40 bg-warn/[.05]' : 'border-danger/40 bg-danger/[.05]',
    )}>
      <Icon className={cn('h-4 w-4 shrink-0 mt-0.5',
        tone === 'warn' ? 'text-warn' : 'text-danger')} />
      <p className="text-muted leading-relaxed">{children}</p>
    </div>
  );
}
