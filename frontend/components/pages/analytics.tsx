'use client';

import { useEffect, useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { Empty, Panel } from '@/components/ui';
import { api } from '@/lib/api';
import { cn, latency, num } from '@/lib/format';

const WINDOWS = [
  { minutes: 30, label: '30 min' },
  { minutes: 60, label: '1 h' },
  { minutes: 360, label: '6 h' },
  { minutes: 1440, label: '24 h' },
];

/** Une seule teinte séquentielle pour les magnitudes. Le violet et l'orange
 *  restent réservés aux paliers de rareté, qui sont un statut. */
const ACCENT = '#3b82f6';

export function AnalyticsPage() {
  const [minutes, setMinutes] = useState(60);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const load = () =>
      api.analytics(minutes)
        .then((result) => { if (alive) { setData(result); setLoading(false); } })
        .catch(() => { if (alive) setLoading(false); });
    load();
    const id = setInterval(load, 15_000);
    return () => { alive = false; clearInterval(id); };
  }, [minutes]);

  const timeline = (data?.timeline ?? []).map((point: any) => ({
    ...point,
    time: new Date(point.at * 1000).toLocaleTimeString('fr-FR',
      { hour: '2-digit', minute: '2-digit' }),
  }));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-semibold">Analytics</h1>
        <div className="ml-auto flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.minutes} type="button" onClick={() => setMinutes(w.minutes)}
              aria-pressed={minutes === w.minutes}
              className={cn('btn', minutes === w.minutes && 'btn-accent')}
            >{w.label}</button>
          ))}
        </div>
      </div>

      {!loading && timeline.length === 0 ? (
        <Empty
          title="Pas encore de données"
          hint="Les graphiques se remplissent au fil des détections. Lance le scanner en mode démo pour voir l'interface vivre sans réseau."
        />
      ) : (
        <>
          <Panel title="Détections par intervalle">
            <div className="h-56 px-2 pb-2 pt-3">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={timeline}>
                  <defs>
                    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={ACCENT} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#1c1c21" vertical={false} />
                  <XAxis dataKey="time" stroke="#5a5a64" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis stroke="#5a5a64" fontSize={10} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
                  <Tooltip content={<ChartTip />} cursor={{ stroke: '#26262d' }} />
                  <Area type="monotone" dataKey="detections" stroke={ACCENT}
                        strokeWidth={2} fill="url(#fill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Panel>

          <div className="grid lg:grid-cols-2 gap-4">
            <Panel title="Latence moyenne de détection">
              <div className="h-48 px-2 pb-2 pt-3">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={timeline}>
                    <CartesianGrid stroke="#1c1c21" vertical={false} />
                    <XAxis dataKey="time" stroke="#5a5a64" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke="#5a5a64" fontSize={10} tickLine={false} axisLine={false} width={40}
                           tickFormatter={(v) => `${Math.round(v / 100) / 10}s`} />
                    <Tooltip content={<ChartTip suffix=" ms" />} cursor={{ stroke: '#26262d' }} />
                    <Area type="monotone" dataKey="avg_latency_ms" stroke={ACCENT}
                          strokeWidth={2} fill="url(#fill)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Panel>

            <Panel title="Répartition par source">
              <div className="h-48 px-2 pb-2 pt-3">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data?.by_source ?? []} layout="vertical">
                    <CartesianGrid stroke="#1c1c21" horizontal={false} />
                    <XAxis type="number" stroke="#5a5a64" fontSize={10} tickLine={false} axisLine={false} allowDecimals={false} />
                    <YAxis type="category" dataKey="source" stroke="#5a5a64" fontSize={10}
                           tickLine={false} axisLine={false} width={110} />
                    <Tooltip content={<ChartTip />} cursor={{ fill: '#ffffff08' }} />
                    <Bar dataKey="detections" fill={ACCENT} radius={[0, 3, 3, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <Panel title="Mots-clés les plus productifs">
              <div className="divide-y divide-line max-h-64 overflow-y-auto">
                {(data?.by_keyword ?? []).map((row: any) => (
                  <div key={row.keyword} className="flex items-center justify-between cell">
                    <span className="text-muted truncate">{row.keyword}</span>
                    <span className="font-mono text-faint tabular-nums">{num(row.detections)}</span>
                  </div>
                ))}
                {!(data?.by_keyword ?? []).length && (
                  <p className="cell text-faint">Aucune détection sur la période.</p>
                )}
              </div>
            </Panel>

            <Panel title="Paliers de rareté">
              <div className="divide-y divide-line">
                {(data?.by_tier ?? []).map((row: any) => (
                  <div key={row.tier} className="flex items-center justify-between cell">
                    <span className="text-muted">{row.tier}</span>
                    <span className="font-mono text-faint tabular-nums">{num(row.count)}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between cell border-t border-line">
                  <span className="text-faint">Notifications envoyées</span>
                  <span className="font-mono text-muted tabular-nums">
                    {num(data?.notifications?.ok ?? 0)} / {num(data?.notifications?.total ?? 0)}
                    {' · '}{latency(data?.notifications?.avg_ms ?? 0)}
                  </span>
                </div>
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

function ChartTip({ active, payload, label, suffix = '' }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="panel px-2 py-1.5 text-2xs font-mono shadow-lg">
      <div className="text-faint">{label}</div>
      {payload.map((entry: any) => (
        <div key={entry.dataKey} className="text-ink">
          {num(entry.value)}{suffix}
        </div>
      ))}
    </div>
  );
}
