'use client';

import { Terminal } from 'lucide-react';
import { useStore } from '@/components/store';
import { Meter, Panel } from '@/components/ui';
import { cn, latency, num, supportStyle } from '@/lib/format';

export function SourcesPage() {
  const { sources } = useStore();
  const enabled = sources.filter((s) => s.enabled);
  const others = sources.filter((s) => !s.enabled);

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-semibold">Sources</h1>

      <div className="grid md:grid-cols-2 gap-2.5">
        {enabled.map((source) => <SourceCard key={source.source} source={source} />)}
      </div>

      {others.length > 0 && (
        <Panel title="Marketplaces connues, non activées">
          <div className="divide-y divide-line">
            {others.map((source) => {
              const support = supportStyle(source);
              return (
                <div key={source.source} className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted flex-1">{source.label}</span>
                    <span className={cn('badge', support.className)}>{support.label}</span>
                  </div>
                  {source.support_note && (
                    <p className="text-2xs text-faint mt-1 leading-relaxed">
                      {source.support_note}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </Panel>
      )}
    </div>
  );
}

function SourceCard({ source }: { source: any }) {
  const support = supportStyle(source);
  const stats = source.stats ?? {};
  const p95 = stats.latency_ms?.p95 ?? 0;
  const breaker = source.breaker ?? {};
  const healthy = stats.healthy;

  return (
    <div className="panel p-3 space-y-2.5">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-xs text-ink font-medium">{source.label}</h3>
          <p className="text-2xs text-faint font-mono">{source.source}</p>
        </div>
        <span className={cn('badge', support.className)}>{support.label}</span>
      </div>

      {/* L'état est écrit en toutes lettres : la couleur ne le porte jamais
          seule. */}
      <div className="flex items-center gap-2 text-2xs">
        <span className={cn('badge',
          healthy === true ? 'text-live border-live/40 bg-live/10'
          : healthy === false ? 'text-danger border-danger/40 bg-danger/10'
          : 'text-faint border-edge bg-white/5')}>
          {healthy === true ? 'ONLINE' : healthy === false ? 'INDISPONIBLE' : 'JAMAIS TESTÉE'}
        </span>
        {breaker.state && breaker.state !== 'closed' && (
          <span className="badge text-danger border-danger/40 bg-danger/10">
            CIRCUIT {breaker.state.toUpperCase()}
            {breaker.reopens_in > 0 && ` · ${Math.round(breaker.reopens_in)}s`}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs font-mono tabular-nums">
        <Row label="Requêtes" value={num(stats.requests ?? 0)} />
        <Row label="req/min" value={stats.requests_per_min ?? 0} />
        <Row label="Annonces" value={num(stats.items ?? 0)} />
        <Row label="Erreurs" value={num(stats.errors ?? 0)} />
        <Row label="429" value={num(stats.rate_limits ?? 0)} />
        <Row label="Disponibilité"
             value={`${((stats.availability ?? 1) * 100).toFixed(1)} %`} />
      </dl>

      <div>
        <div className="flex items-center justify-between text-2xs text-faint mb-1">
          <span>Latence p95</span>
          <span className="font-mono">{latency(p95)}</span>
        </div>
        <Meter value={Math.min(p95, 2000)} max={2000}
               tone={p95 > 1500 ? 'danger' : p95 > 800 ? 'warn' : 'live'} />
      </div>

      {source.support === 'needs_selectors' && (
        <div className="flex items-start gap-2 px-2 py-1.5 rounded border border-warn/30 bg-warn/[.06]">
          <Terminal className="h-3 w-3 text-warn mt-0.5 shrink-0" />
          <div className="text-2xs text-muted leading-relaxed">
            Sélecteurs à calibrer. Lance sur ta machine :
            <code className="block mt-1 font-mono text-ink">
              buyee-radar calibrate --source {source.source}
            </code>
          </div>
        </div>
      )}

      {stats.last_error && (
        <p className="text-2xs text-danger/80 font-mono truncate" title={stats.last_error}>
          {stats.last_error}
        </p>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between">
      <dt className="text-faint">{label}</dt>
      <dd className="text-muted">{value}</dd>
    </div>
  );
}
