'use client';

/**
 * BUyee SEARCH — une requête, toutes les sources.
 *
 * La page dit trois choses que le cahier des charges exige, et qu'un
 * simple total masquerait :
 *   1. quelles sources ont été interrogées,
 *   2. combien chacune a rendu,
 *   3. pourquoi les autres ont été écartées.
 *
 * Le total ne peut donc jamais être « le compte d'une seule source
 * déguisé » : il est décomposé sous les yeux de l'utilisateur.
 */

import { motion } from 'framer-motion';
import { AlertTriangle, ChevronDown, Loader2, Search } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/components/store';
import { ListingCard } from '@/components/listing-card';
import { Badge, Empty, Panel } from '@/components/ui';
import { api, type SearchReport } from '@/lib/api';
import { KIND_STYLE, cn } from '@/lib/format';

export function SearchPage() {
  const { sources, counts } = useStore();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string[] | null>(null);
  const [report, setReport] = useState<SearchReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showSkipped, setShowSkipped] = useState(false);
  // Échelle des barres : la source la plus productive de CETTE requête.
  const maxCount = Math.max(
    1, ...(report?.outcomes.filter((o) => o.queried).map((o) => o.count) ?? [1]),
  );

  // Une source qu'on ne peut pas interroger ne doit pas être cochable :
  // proposer la case donnerait l'illusion d'un choix sans effet.
  const selectable = useMemo(
    () => sources.filter((s) => s.usable || s.simulated),
    [sources],
  );
  const unavailable = useMemo(
    () => sources.filter((s) => !s.usable && !s.simulated),
    [sources],
  );

  const toggle = useCallback((name: string) => {
    setSelected((current) => {
      const base = current ?? selectable.map((s) => s.source);
      const next = base.includes(name)
        ? base.filter((s) => s !== name)
        : [...base, name];
      return next.length === selectable.length ? null : next;
    });
  }, [selectable]);

  const run = useCallback(async () => {
    const text = query.trim();
    if (!text) return;
    setBusy(true);
    setError('');
    try {
      setReport(await api.search(text, selected ?? undefined));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setReport(null);
    } finally {
      setBusy(false);
    }
  }, [query, selected]);

  const active = selected ?? selectable.map((s) => s.source);

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-sm font-semibold">BUyee Search</h1>
        <span className="text-2xs text-faint">
          Buyee est la plateforme cible ; les marketplaces ci-dessous en sont
          les sources.
        </span>
      </div>

      <Panel title="Query">
        <div className="p-3 space-y-3">
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && run()}
              placeholder="nike trail"
              className="flex-1 bg-raised border border-line rounded-md px-3 py-2 text-xs
                         text-ink placeholder:text-faint outline-none
                         focus:border-accent/60 transition-colors"
              aria-label="Requête Buyee"
            />
            <button
              type="button" onClick={run} disabled={busy || !query.trim()}
              className="btn btn-accent px-4 disabled:opacity-40"
            >
              {busy
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Search className="h-3 w-3" />}
              Rechercher
            </button>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-2xs text-faint uppercase tracking-wider">
                Sources ({active.length}/{selectable.length} interrogeables
                {' '}· {counts.known} connues)
              </span>
              <button
                type="button" onClick={() => setSelected(null)}
                className="text-2xs text-muted hover:text-ink transition-colors"
              >
                Toutes
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {selectable.map((source) => {
                const on = active.includes(source.source);
                const kind = KIND_STYLE[source.kind] ?? KIND_STYLE.c2c;
                return (
                  <button
                    key={source.source} type="button"
                    onClick={() => toggle(source.source)}
                    aria-pressed={on}
                    className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-md border text-2xs transition-colors',
                      on
                        ? 'border-accent/50 bg-accent/10 text-ink'
                        : 'border-line text-faint hover:text-muted',
                    )}
                  >
                    <span className={cn(
                      'h-1.5 w-1.5 rounded-full',
                      on ? 'bg-accent' : 'bg-edge',
                    )} />
                    {source.label}
                    <span className={cn('badge scale-90', kind.className)}>
                      {kind.label}
                    </span>
                  </button>
                );
              })}
            </div>

            {unavailable.length > 0 && (
              <p className="text-2xs text-faint mt-2 leading-relaxed">
                {unavailable.length} source
                {unavailable.length > 1 ? 's' : ''} non interrogeable
                {unavailable.length > 1 ? 's' : ''} pour l'instant —{' '}
                <a href="/sources" className="text-muted hover:text-ink underline underline-offset-2">
                  voir le motif de chacune
                </a>.
              </p>
            )}
          </div>
        </div>
      </Panel>

      {error && (
        <div className="panel p-3 flex items-start gap-2 border-danger/40">
          <AlertTriangle className="h-3.5 w-3.5 text-danger shrink-0 mt-0.5" />
          <span className="text-xs text-muted">{error}</span>
        </div>
      )}

      {report && (
        <motion.div
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
          className="space-y-4"
        >
          <Panel
            title={`Résultats · ${report.total} annonces sur ${report.sources_queried} source${report.sources_queried > 1 ? 's' : ''}`}
            action={
              <span className="text-2xs font-mono text-faint">
                {report.sources_queried} source
                {report.sources_queried > 1 ? 's' : ''} interrogée
                {report.sources_queried > 1 ? 's' : ''} en parallèle ·{' '}
                {report.elapsed_ms} ms
              </span>
            }
          >
            <div className="divide-y divide-line">
              {/* Les sources qui ont RÉPONDU d'abord : c'est là que se
                  lit le résultat. Les écartées sont juste en dessous,
                  repliées — visibles, mais elles n'encombrent plus. */}
              {report.outcomes.filter((o) => o.queried).map((outcome) => {
                const count = report.per_source[outcome.source];
                return (
                  <div
                    key={outcome.source}
                    className="px-3 py-2 flex items-center gap-2.5 text-xs"
                  >
                    <span className={cn(
                      'h-1.5 w-1.5 rounded-full shrink-0',
                      outcome.ok ? 'bg-live' : 'bg-danger',
                    )} />
                    <span className="text-ink w-52 shrink-0 truncate font-mono">
                      [{outcome.label}]
                    </span>
                    <span className="font-mono tabular-nums text-muted w-24">
                      {outcome.count} results
                    </span>
                    <span className="font-mono text-2xs text-faint tabular-nums w-16">
                      {outcome.latency_ms} ms
                    </span>
                    <div className="flex-1 h-1 rounded-full bg-raised overflow-hidden max-w-[220px]">
                      <motion.div
                        className="h-full bg-accent/60"
                        initial={{ width: 0 }}
                        animate={{
                          width: `${maxCount ? (outcome.count / maxCount) * 100 : 0}%`,
                        }}
                        transition={{ duration: 0.4, ease: 'easeOut' }}
                      />
                    </div>
                    {typeof count === 'number' && count !== outcome.count && (
                      <span className="text-2xs text-faint whitespace-nowrap">
                        → {count} après attribution
                      </span>
                    )}
                    {outcome.error && (
                      <span className="text-2xs text-danger truncate">
                        {outcome.error}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            {report.outcomes.some((o) => !o.queried) && (
              <div className="border-t border-line">
                <button
                  type="button" onClick={() => setShowSkipped(!showSkipped)}
                  aria-expanded={showSkipped}
                  className="w-full px-3 py-2 flex items-center gap-1.5 text-2xs
                             text-faint hover:text-muted transition-colors"
                >
                  <ChevronDown className={cn(
                    'h-3 w-3 transition-transform', showSkipped && 'rotate-180',
                  )} />
                  {report.outcomes.filter((o) => !o.queried).length} source
                  {report.outcomes.filter((o) => !o.queried).length > 1 ? 's' : ''}{' '}
                  écartée
                  {report.outcomes.filter((o) => !o.queried).length > 1 ? 's' : ''}
                  {' '}— et pourquoi
                </button>
                {showSkipped && (
                  <div className="divide-y divide-line border-t border-line">
                    {report.outcomes.filter((o) => !o.queried).map((outcome) => (
                      <div key={outcome.source}
                           className="px-3 py-1.5 flex items-start gap-2.5 text-2xs">
                        <span className="h-1.5 w-1.5 rounded-full bg-edge shrink-0 mt-1" />
                        <span className="text-muted w-52 shrink-0 truncate font-mono">
                          [{outcome.label}]
                        </span>
                        <span className="text-faint leading-relaxed">
                          {outcome.skipped_reason}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="px-3 py-2 border-t border-line flex items-center gap-3">
              <span className="text-xs font-semibold text-ink">
                Total : {report.total}
              </span>
              <span className="text-2xs text-faint">
                somme des sources, jamais le compte d'une seule
              </span>
            </div>
          </Panel>

          {report.listings.length > 0 ? (
            <div className="space-y-1.5">
              {report.listings.map((listing) => (
                <ListingCard key={listing.key} listing={listing} />
              ))}
            </div>
          ) : (
            <Empty
              title="Aucune annonce"
              hint="Les sources interrogées ont répondu, mais rien ne correspond — ou leurs sélecteurs restent à calibrer."
            />
          )}
        </motion.div>
      )}

      {!report && !busy && (
        <Empty
          title="Tape une requête"
          hint="Elle partira vers toutes les sources cochées en parallèle, et chaque résultat dira de quelle marketplace il vient."
        />
      )}
    </div>
  );
}
