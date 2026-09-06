'use client';

/**
 * BUyee SOURCES.
 *
 * L'écran est organisé comme la plateforme l'est réellement :
 *
 *   BUyee
 *     ├── Cross-Search ....... une requête, cinq marketplaces
 *     ├── Flux d'annonces .... Mercari, Rakuma, JDirectItems…
 *     └── Catalogues ......... Rakuten, Amazon, ZOZOTOWN — rien à sniper
 *
 * Chaque source non exploitable affiche SON motif et les URL qui
 * l'attestent. Trois sources qui marchent valent mieux que huit qui font
 * semblant.
 */

import { motion } from 'framer-motion';
import { ChevronDown, Terminal } from 'lucide-react';
import { useState } from 'react';
import { useStore } from '@/components/store';
import { Badge, Meter, Panel } from '@/components/ui';
import { KIND_STYLE, SUPPORT_STYLE, cn, latency, num, supportStyle } from '@/lib/format';
import { type SourceInfo } from '@/lib/api';

const GROUPS: { kind: string; title: string; hint: string }[] = [
  {
    kind: 'meta',
    title: 'Recherche transversale',
    hint: "L'endpoint que Buyee expose lui-même : une requête couvre les cinq marketplaces d'occasion. Chaque résultat est ensuite rendu à sa vraie source.",
  },
  {
    kind: 'flux',
    title: "Flux d'annonces",
    hint: 'Marketplaces où les annonces apparaissent en continu. C’est là qu’il y a quelque chose à sniper.',
  },
  {
    kind: 'catalog',
    title: 'Catalogues marchands',
    hint: "Absents de la recherche transversale de Buyee. Une fiche produit de catalogue est durable et réapprovisionnée : il n'y a pas de « nouvelle annonce » à détecter.",
  },
  {
    kind: 'simulator',
    title: 'Sources simulées',
    hint: 'Hors ligne, pour éprouver la chaîne sans réseau. Le préfixe « sim_ » est visible partout.',
  },
];

function groupOf(source: SourceInfo): string {
  if (source.kind === 'meta') return 'meta';
  if (source.kind === 'catalog') return 'catalog';
  if (source.kind === 'simulator') return 'simulator';
  return 'flux';
}

export function SourcesPage() {
  const { sources, counts } = useStore();

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-sm font-semibold">BUyee Sources</h1>
        <span className="font-mono text-2xs text-faint">
          {counts.scanned} interrogée{counts.scanned > 1 ? 's' : ''} ·{' '}
          {counts.known} connue{counts.known > 1 ? 's' : ''}
          {counts.simulated > 0 && ` · ${counts.simulated} simulée${counts.simulated > 1 ? 's' : ''}`}
        </span>
      </div>

      <PlatformTree />

      {GROUPS.map((group) => {
        const members = sources.filter((s) => groupOf(s) === group.kind);
        if (members.length === 0) return null;
        return (
          <section key={group.kind} className="space-y-2">
            <div>
              <h2 className="label">{group.title}</h2>
              <p className="text-2xs text-faint mt-0.5 max-w-3xl leading-relaxed">
                {group.hint}
              </p>
            </div>
            <div className={cn(
              'grid gap-2.5',
              group.kind === 'meta' ? 'grid-cols-1' : 'lg:grid-cols-2',
            )}>
              {members.map((source) => (
                <SourceCard key={source.source} source={source} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function SourceCard({ source }: { source: SourceInfo }) {
  const [open, setOpen] = useState(false);
  const support = supportStyle(source);
  const kind = KIND_STYLE[source.kind] ?? KIND_STYLE.c2c;
  const stats = source.stats ?? {};
  const breaker = source.breaker ?? {};
  const live = source.enabled && stats.healthy;

  return (
    <motion.article layout className="panel overflow-hidden">
      <header className="px-3 py-2.5 flex items-start gap-2 border-b border-line">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs font-medium text-ink">{source.label}</span>
            <Badge className={cn('scale-90', kind.className)}>{kind.label}</Badge>
          </div>
          <p className="font-mono text-2xs text-faint mt-0.5">{source.source}</p>
        </div>
        <Badge className={support.className}>{support.label}</Badge>
      </header>

      <div className="px-3 py-2 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge className={cn(
            live ? 'text-live border-live/40 bg-live/10'
              : source.enabled ? 'text-warn border-warn/40 bg-warn/10'
                : 'text-faint border-edge bg-white/5',
          )}>
            {live ? 'ACTIVE' : source.enabled ? 'ACTIVÉE' : 'OFF'}
          </Badge>
          {source.in_crosssearch && source.kind !== 'meta' && (
            <span className="text-2xs text-faint">
              couverte par la recherche transversale
            </span>
          )}
        </div>

        {source.aggregates.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-2xs text-faint">couvre</span>
            {source.aggregates.map((id) => (
              <span key={id}
                    className="badge text-muted border-edge bg-white/[.04]">
                {id}
              </span>
            ))}
          </div>
        )}

        {source.enabled ? (
          <>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-2xs">
              <Row label="Requêtes" value={num(stats.requests ?? 0)} />
              <Row label="req/min" value={num(stats.requests_per_min ?? 0)} />
              <Row label="Annonces" value={num(stats.items ?? 0)} />
              <Row label="Erreurs" value={num(stats.errors ?? 0)}
                   tone={(stats.errors ?? 0) > 0 ? 'danger' : undefined} />
              <Row label="429" value={num(stats.rate_limits ?? 0)}
                   tone={(stats.rate_limits ?? 0) > 0 ? 'warn' : undefined} />
              <Row label="Disponibilité"
                   value={`${((stats.availability ?? 0) * 100).toFixed(1)} %`} />
            </dl>
            <div>
              <div className="flex items-center justify-between text-2xs mb-1">
                <span className="text-faint">Latence p95</span>
                <span className="font-mono text-muted tabular-nums">
                  {latency(stats.latency_ms?.p95 ?? 0)}
                </span>
              </div>
              <Meter value={Math.min(100, (stats.latency_ms?.p95 ?? 0) / 12)} />
            </div>
            {breaker.state && breaker.state !== 'closed' && (
              <p className="text-2xs text-warn">
                Disjoncteur {breaker.state} — réouverture dans{' '}
                {Math.round(breaker.reopens_in ?? 0)} s
              </p>
            )}
          </>
        ) : (
          <p className="text-2xs text-faint leading-relaxed">
            {source.support_note}
          </p>
        )}

        {source.support === 'needs_selectors' && !source.cross_only && (
          <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-md bg-raised border border-line">
            <Terminal className="h-3 w-3 text-faint shrink-0" />
            <code className="text-2xs text-muted truncate">
              buyee-radar calibrate --source {source.source}
            </code>
          </div>
        )}

        {source.evidence.length > 0 && (
          <div>
            <button
              type="button" onClick={() => setOpen(!open)}
              className="flex items-center gap-1 text-2xs text-faint hover:text-muted transition-colors"
              aria-expanded={open}
            >
              <ChevronDown className={cn(
                'h-3 w-3 transition-transform', open && 'rotate-180',
              )} />
              {source.evidence.length} URL observée
              {source.evidence.length > 1 ? 's' : ''}
            </button>
            {open && (
              <motion.ul
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                className="mt-1.5 space-y-1 overflow-hidden"
              >
                {source.evidence.map((url) => (
                  <li key={url}
                      className="font-mono text-2xs text-faint break-all leading-relaxed">
                    {url}
                  </li>
                ))}
              </motion.ul>
            )}
          </div>
        )}
      </div>
    </motion.article>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <>
      <dt className="text-faint">{label}</dt>
      <dd className={cn(
        'font-mono text-right tabular-nums',
        tone === 'danger' ? 'text-danger' : tone === 'warn' ? 'text-warn' : 'text-muted',
      )}>
        {value}
      </dd>
    </>
  );
}


/**
 * L'architecture, dessinée.
 *
 * Buyee est la plateforme cible ; les marketplaces en sont les sources.
 * Le dire en une phrase ne suffit pas — l'arbre le montre, et c'est
 * exactement la forme que le code a pris.
 */
function PlatformTree() {
  const { sources } = useStore();
  const of = (id: string) => sources.find((s) => s.source === id);
  const flux = ['mercari', 'rakuma', 'jdirectitems_auction',
                'jdirectitems_fleamarket', 'luxewholesale'];
  const catalogs = ['jdirectitems_shopping', 'rakuten', 'amazon', 'zozotown'];

  return (
    <Panel title="Architecture">
      <div className="p-3 overflow-x-auto">
        <div className="min-w-[620px]">
          <div className="flex flex-col items-center">
            <span className="px-3 py-1.5 rounded-md border border-accent/40 bg-accent/10
                             text-xs font-semibold text-ink">
              BUyee
            </span>
            <span className="h-4 w-px bg-edge" />
            <span className="text-2xs text-faint mb-2">plateforme cible</span>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Branch
              title="Cross-Search"
              hint="une requête → cinq sources"
              tone="accent"
              items={flux.map(of)}
            />
            <Branch
              title="Catalogues"
              hint="absents du cross-search · rien à sniper"
              tone="faint"
              items={catalogs.map(of)}
            />
          </div>
        </div>
      </div>
    </Panel>
  );
}

function Branch({
  title, hint, tone, items,
}: {
  title: string; hint: string; tone: 'accent' | 'faint';
  items: (SourceInfo | undefined)[];
}) {
  return (
    <div className={cn(
      'rounded-md border p-2.5',
      tone === 'accent' ? 'border-accent/25 bg-accent/[.03]' : 'border-line',
    )}>
      <div className="flex items-baseline gap-2 mb-2">
        <span className={cn(
          'text-2xs font-semibold tracking-wide',
          tone === 'accent' ? 'text-accent' : 'text-faint',
        )}>
          {title.toUpperCase()}
        </span>
        <span className="text-2xs text-faint truncate">{hint}</span>
      </div>
      <div className="space-y-1">
        {items.filter(Boolean).map((source) => {
          const s = source as SourceInfo;
          return (
            <div key={s.source} className="flex items-center gap-2 text-2xs">
              <span className="text-edge font-mono">└</span>
              <span className={cn(
                'truncate',
                s.support === 'unsupported' ? 'text-faint line-through decoration-edge'
                  : 'text-muted',
              )}>
                {s.label}
              </span>
              <span className="ml-auto font-mono text-faint">{s.source}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
