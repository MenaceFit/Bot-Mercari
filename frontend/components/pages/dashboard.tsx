'use client';

import { AlertTriangle, Zap } from 'lucide-react';
import Link from 'next/link';
import { useMemo } from 'react';
import { FeedList } from '@/components/feed-list';
import { useStore } from '@/components/store';
import { Meter, Panel, Spark, Stat } from '@/components/ui';
import { cn, latency, num, supportStyle } from '@/lib/format';

export function Dashboard() {
  const { metrics, scheduler, sources, keywords, feed, snapshot } = useStore();
  const lat = metrics?.latency ?? {};
  const online = sources.filter((s) => s.stats?.healthy).length;
  const active = sources.filter((s) => s.enabled).length;

  // Détections par minute sur la dernière demi-heure, reconstruites depuis
  // le flux : pas d'appel réseau supplémentaire pour un sparkline.
  const spark = useMemo(() => {
    const buckets = new Array(30).fill(0);
    const now = Date.now() / 1000;
    for (const item of feed) {
      const age = Math.floor((now - item.detected_at) / 60);
      if (age >= 0 && age < 30) buckets[29 - age] += 1;
    }
    return buckets;
  }, [feed]);

  const needCalibration = sources.filter(
    (s) => s.enabled && s.support === 'needs_selectors',
  );
  const saturated = scheduler?.saturated;

  return (
    <div className="space-y-4">
      {(needCalibration.length > 0 || saturated) && (
        <div className="space-y-2">
          {needCalibration.length > 0 && (
            <Notice tone="warn" icon={<AlertTriangle className="h-3.5 w-3.5" />}>
              {needCalibration.length} source(s) attendent leurs sélecteurs :{' '}
              <span className="font-mono">{needCalibration.map((s) => s.source).join(', ')}</span>.
              Lance <code className="text-ink">buyee-radar calibrate</code> — elles
              ne ramèneront rien tant que ce n'est pas fait.{' '}
              <Link href="/sources" className="underline">Voir les sources</Link>
            </Notice>
          )}
          {saturated && (
            <Notice tone="warn" icon={<Zap className="h-3.5 w-3.5" />}>
              Budget de requêtes saturé : {scheduler.tasks} requêtes pour{' '}
              {scheduler.budget} req/s. Chaque mot-clé n'est réellement revisité que
              toutes les <span className="font-mono">{scheduler.effective_interval}s</span>.
              Monte <code className="text-ink">scanner.budget_per_second</code> ou
              retire des mots-clés.
            </Notice>
          )}
        </div>
      )}

      {/* Quatre chiffres, pas six. La latence réseau et le nombre de
          mots-clés sont déjà lisibles ailleurs (page Système, barre
          latérale) : les répéter ici diluait ce qui compte. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        <Stat label="Détections" value={metrics?.new_listings ?? 0} tone="accent"
              sub={`${metrics?.new_per_min ?? 0}/min`} />
        <Stat label="Latence détection" value={latency(lat.detection_ms?.p50 ?? 0)}
              sub={`p95 ${latency(lat.detection_ms?.p95 ?? 0)}`} />
        <Stat label="Sources" value={`${online}/${active}`} tone={online === active ? 'live' : 'warn'}
              sub="en ligne" />
        <Stat label="Doublons écartés" value={metrics?.duplicates ?? 0}
              sub={`${metrics?.duplicates_per_min ?? 0}/min`} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <Panel
            title="Activité · 30 dernières minutes"
            action={<span className="text-2xs font-mono text-faint">
              pic {Math.max(...spark, 0)}/min
            </span>}
          >
            <div className="px-3 pb-3 pt-2">
              <Spark data={spark} height={56} />
            </div>
          </Panel>

          <div>
            <div className="flex items-center justify-between mb-2">
              <h2 className="label">Flux en direct</h2>
              <Link href="/feed" className="text-2xs text-muted hover:text-ink">
                tout voir →
              </Link>
            </div>
            <FeedList limit={12} showFilters={false} />
          </div>
        </div>

        <div className="space-y-4">
          <Panel title="Santé des sources">
            <div className="divide-y divide-line">
              {sources.filter((s) => s.enabled).map((source) => {
                const support = supportStyle(source);
                const p95 = source.stats?.latency_ms?.p95 ?? 0;
                const state = source.breaker?.state;
                return (
                  <div key={source.source} className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-ink truncate flex-1">{source.label}</span>
                      <span className={cn('badge', support.className)}>{support.label}</span>
                    </div>
                    <div className="grid grid-cols-4 gap-x-3 gap-y-0.5 mt-1.5 text-2xs font-mono tabular-nums">
                      <Cell label="last req" value={clock(source.stats?.last_ok)} />
                      <Cell label="latency" value={latency(p95)} />
                      <Cell label="results" value={num(source.stats?.items ?? 0)} />
                      <Cell
                        label="errors"
                        value={num(source.stats?.errors ?? 0)}
                        tone={(source.stats?.errors ?? 0) > 0 ? 'danger' : undefined}
                      />
                    </div>
                    {state && state !== 'closed' && (
                      <p className="text-2xs text-danger uppercase mt-1">
                        circuit {state}
                      </p>
                    )}
                    {/* Échelle plafonnée à 2 s : au-delà, une source est
                        lente quoi qu'il arrive, et l'échelle linéaire
                        écraserait toutes les autres. */}
                    <div className="mt-1.5">
                      <Meter value={Math.min(p95, 2000)} max={2000}
                             tone={p95 > 1500 ? 'danger' : p95 > 800 ? 'warn' : 'live'} />
                    </div>
                  </div>
                );
              })}
              {sources.filter((s) => s.enabled).length === 0 && (
                <p className="px-3 py-4 text-2xs text-faint">Aucune source active.</p>
              )}
            </div>
          </Panel>

          <Panel title="Détections par source">
            {/* Le cahier des charges est explicite : les statistiques
                doivent être PAR SOURCE, pas seulement un total. Un total
                seul ne dit pas si une source s'est tue. */}
            <div className="px-3 py-2 space-y-1.5">
              {(() => {
                const rows = sources
                  .filter((source) => source.enabled)
                  .map((source) => ({
                    source,
                    value: source.stats?.items ?? 0,
                  }))
                  .sort((a, b) => b.value - a.value);
                const total = rows.reduce((sum, row) => sum + row.value, 0);
                if (total === 0) {
                  return (
                    <p className="text-2xs text-faint py-2">
                      Rien encore détecté.
                    </p>
                  );
                }
                return (
                  <>
                    <div className="flex items-baseline justify-between">
                      <span className="text-2xs text-faint">Total</span>
                      <span className="font-mono text-sm text-ink tabular-nums">
                        {num(total)}
                      </span>
                    </div>
                    {rows.map(({ source, value }) => (
                      <div key={source.source}>
                        <div className="flex items-center justify-between text-2xs">
                          <span className="text-muted truncate">{source.label}</span>
                          <span className="font-mono text-faint tabular-nums">
                            {num(value)}
                            <span className="text-edge ml-1.5">
                              {total ? Math.round((value / total) * 100) : 0}%
                            </span>
                          </span>
                        </div>
                        <div className="mt-1">
                          <Meter value={value} max={rows[0].value || 1} tone="live" />
                        </div>
                      </div>
                    ))}
                  </>
                );
              })()}
            </div>
          </Panel>

        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-faint">{label}</dt>
      <dd className="font-mono text-muted tabular-nums">{value}</dd>
    </div>
  );
}

function Notice({
  tone, icon, children,
}: { tone: 'warn' | 'danger'; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className={cn(
      'flex items-start gap-2 px-3 py-2 rounded-lg border text-xs leading-relaxed',
      tone === 'warn'
        ? 'border-warn/30 bg-warn/[.06] text-warn'
        : 'border-danger/30 bg-danger/[.06] text-danger',
    )}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <p className="text-muted [&_code]:font-mono [&_code]:text-2xs">{children}</p>
    </div>
  );
}


/** Une cellule « libellé / valeur » du bloc de santé d'une source. */
function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-faint truncate">{label}</div>
      <div className={cn(
        'truncate',
        tone === 'danger' ? 'text-danger' : 'text-muted',
      )}>
        {value}
      </div>
    </div>
  );
}

/** Heure de la dernière requête réussie. « — » tant qu'il n'y en a pas eu. */
function clock(epoch?: number): string {
  if (!epoch) return '—';
  return new Date(epoch * 1000).toLocaleTimeString('fr-FR', { hour12: false });
}
