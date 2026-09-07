'use client';

import { useEffect, useState } from 'react';
import { useStore } from '@/components/store';
import { Panel } from '@/components/ui';
import { api } from '@/lib/api';
import { cn, duration, latency, num } from '@/lib/format';

export function SystemPage() {
  const { snapshot, metrics, events, connected, scheduler } = useStore();
  const [system, setSystem] = useState<any>(null);

  useEffect(() => {
    const load = () => api.system().then(setSystem).catch(() => {});
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, []);

  const lat = metrics?.latency ?? {};

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-semibold">System</h1>

      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
        <Panel title="Environnement">
          <dl className="px-3 py-2 space-y-1.5 text-2xs">
            <Row label="Python" value={system?.python ?? '—'} />
            <Row label="Plateforme" value={system?.platform ?? '—'} />
            <Row label="SQLite" value={system?.sqlite ?? '—'} />
            <Row label="Uptime" value={duration(snapshot?.uptime_seconds ?? 0)} />
            <Row label="WebSocket" value={connected ? 'connecté' : 'déconnecté'} />
          </dl>
        </Panel>

        <Panel title="Base de données">
          <dl className="px-3 py-2 space-y-1.5 text-2xs">
            <Row label="Fichier" value={system?.database_path ?? '—'} mono />
            <Row label="Taille"
                 value={system ? `${(system.database_bytes / 1024 / 1024).toFixed(2)} Mo` : '—'} />
            <Row label="Annonces" value={num(snapshot?.store?.listings_total ?? 0)} />
            <Row label="Sur 24 h" value={num(snapshot?.store?.listings_24h ?? 0)} />
            <Row label="Cache dédup" value={num(snapshot?.dedup?.size ?? 0)} />
            <Row label="Taux de doublons"
                 value={`${((snapshot?.dedup?.duplicate_ratio ?? 0) * 100).toFixed(1)} %`} />
          </dl>
        </Panel>

        {/* Déplacés depuis le tableau de bord : ce sont des détails de
            fonctionnement, pas de surveillance. */}
        <Panel title="Ordonnanceur">
          <dl className="px-3 py-2 space-y-1.5 text-2xs">
            <Row label="Requêtes planifiées" value={scheduler?.tasks ?? 0} />
            <Row label="Budget" value={`${scheduler?.budget ?? 0} req/s`} />
            <Row label="Demande" value={`${scheduler?.demand ?? 0} req/s`} />
            <Row label="Cadence réelle" value={`${scheduler?.effective_interval ?? 0} s`} />
            <Row label="Ralenties" value={scheduler?.throttled ?? 0} />
            <Row label="Notifications" value={metrics?.notifications ?? 0} />
            <Row label="Échecs d'envoi" value={metrics?.notification_failures ?? 0} />
          </dl>
        </Panel>

        {snapshot?.currency?.available && (
          <Panel title="Taux de change">
            <dl className="px-3 py-2 space-y-1.5 text-2xs">
              <Row label="Paire" value={`${snapshot.currency.base} → ${snapshot.currency.target}`} />
              <Row label="Taux" value={snapshot.currency.rate?.toFixed(5)} />
              <Row label="Source" value={snapshot.currency.source} />
              <Row label="État" value={snapshot.currency.stale ? 'périmé' : 'à jour'} />
            </dl>
          </Panel>
        )}

        <Panel title="Mémoire et bus">
          <dl className="px-3 py-2 space-y-1.5 text-2xs">
            <Row label="RSS"
                 value={system ? `${(system.memory?.rss_bytes / 1024 / 1024).toFixed(0)} Mo` : '—'} />
            <Row label="Abonnés au bus" value={snapshot?.bus?.subscribers ?? 0} />
            <Row label="Événements publiés" value={num(snapshot?.bus?.published ?? 0)} />
            <Row label="Événements perdus" value={num(snapshot?.bus?.dropped ?? 0)} />
            <Row label="Adapters" value={system?.adapters ?? 0} />
          </dl>
        </Panel>
      </div>

      <Panel title="Latences par étape (percentiles sur fenêtre glissante)">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="border-b border-line">
              <tr className="label">
                <th className="cell font-medium">Étape</th>
                <th className="cell font-medium text-right">Mesures</th>
                <th className="cell font-medium text-right">Moyenne</th>
                <th className="cell font-medium text-right">p50</th>
                <th className="cell font-medium text-right">p95</th>
                <th className="cell font-medium text-right">p99</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {[
                ['Réseau · T2→T3', lat.network_ms],
                ['Détection · T0→T3', lat.detection_ms],
                ['Pipeline · T3→T4', lat.pipeline_ms],
                ['Notification · T4→T5', lat.notify_ms],
                ['Bout en bout · T0→T5', lat.total_ms],
              ].map(([label, stage]: any) => (
                <tr key={label} className="hover:bg-white/[.02]">
                  <td className="cell text-muted">{label}</td>
                  <td className="cell text-right font-mono text-faint tabular-nums">
                    {num(stage?.count ?? 0)}
                  </td>
                  {['avg', 'p50', 'p95', 'p99'].map((key) => (
                    <td key={key} className="cell text-right font-mono text-ink tabular-nums">
                      {latency(stage?.[key] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-3 pb-2 text-2xs text-faint leading-relaxed">
          Une étape dont un horodatage est inconnu n'est pas enregistrée. Beaucoup
          de pages de résultats n'indiquent pas l'heure de publication : « — »
          signifie « non mesurable », jamais « instantané ».
        </p>
      </Panel>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Circuit breakers">
          <div className="divide-y divide-line">
            {(snapshot?.breakers ?? []).map((breaker: any) => (
              <div key={breaker.name} className="flex items-center gap-2 cell">
                <span className="text-muted flex-1">{breaker.name}</span>
                <span className={cn('badge',
                  breaker.state === 'closed' ? 'text-live border-live/40 bg-live/10'
                  : breaker.state === 'half_open' ? 'text-warn border-warn/40 bg-warn/10'
                  : 'text-danger border-danger/40 bg-danger/10')}>
                  {breaker.state}
                </span>
                <span className="font-mono text-2xs text-faint tabular-nums">
                  {breaker.trips} ouverture(s)
                </span>
              </div>
            ))}
            {!(snapshot?.breakers ?? []).length && (
              <p className="cell text-faint">Aucun adapter démarré.</p>
            )}
          </div>
        </Panel>

        <Panel title="Événements récents">
          <div className="divide-y divide-line max-h-72 overflow-y-auto">
            {events.slice(0, 40).map((event, index) => (
              <div key={index} className="flex items-center gap-2 cell font-mono text-2xs">
                <span className="text-faint tabular-nums">
                  {new Date(event.at * 1000).toLocaleTimeString('fr-FR')}
                </span>
                <span className={cn(
                  event.type === 'source_error' ? 'text-danger'
                  : event.type === 'gap_detected' ? 'text-warn'
                  : event.type === 'listing' ? 'text-accent' : 'text-muted',
                )}>{event.type}</span>
                <span className="text-faint truncate">
                  {typeof event.data === 'object' && event.data
                    ? (event.data.source ?? event.data.title ?? event.data.query ?? '')
                    : ''}
                </span>
              </div>
            ))}
            {!events.length && <p className="cell text-faint">Aucun événement reçu.</p>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-faint shrink-0">{label}</dt>
      <dd className={cn('text-muted tabular-nums truncate', mono !== false && 'font-mono')}>
        {value}
      </dd>
    </div>
  );
}
