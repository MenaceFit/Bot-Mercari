'use client';

import { Download, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useStore } from '@/components/store';
import { Empty, Panel } from '@/components/ui';
import { api, type Listing } from '@/lib/api';
import { TIER_STYLE, ago, cn, latency, num, yen } from '@/lib/format';

export function HistoryPage() {
  const { sources, keywords } = useStore();
  const [rows, setRows] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ search: '', source: '', keyword: '', min_score: 0 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await api.listings({ limit: 300, ...filters }));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  // Debounce : sans lui, chaque frappe déclenche une requête SQL.
  useEffect(() => {
    const id = setTimeout(load, 250);
    return () => clearTimeout(id);
  }, [load]);

  const exportAs = (format: 'csv' | 'json') => {
    const blob = format === 'json'
      ? new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' })
      : new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `radar-history.${format}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-sm font-semibold">History</h1>
        <span className="text-2xs text-faint font-mono">{num(rows.length)} ligne(s)</span>
        <div className="ml-auto flex gap-1.5">
          <button type="button" className="btn" onClick={() => exportAs('csv')}>
            <Download className="h-3 w-3" /> CSV
          </button>
          <button type="button" className="btn" onClick={() => exportAs('json')}>
            <Download className="h-3 w-3" /> JSON
          </button>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-faint" />
          <input
            className="input pl-7" placeholder="Filtrer les titres…"
            value={filters.search}
            onChange={(e) => setFilters({ ...filters, search: e.target.value })}
          />
        </div>
        <select className="input w-auto" value={filters.source}
                onChange={(e) => setFilters({ ...filters, source: e.target.value })}>
          <option value="">Toutes les sources</option>
          {sources.filter((s) => s.enabled).map((s) => (
            <option key={s.source} value={s.source}>{s.label}</option>
          ))}
        </select>
        <select className="input w-auto" value={filters.keyword}
                onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}>
          <option value="">Tous les mots-clés</option>
          {keywords.map((k: any) => <option key={k.name} value={k.name}>{k.name}</option>)}
        </select>
        <select className="input w-auto" value={filters.min_score}
                onChange={(e) => setFilters({ ...filters, min_score: Number(e.target.value) })}>
          <option value={0}>Tous les scores</option>
          <option value={50}>≥ 50 · RARE</option>
          <option value={70}>≥ 70 · VERY RARE</option>
          <option value={90}>≥ 90 · ULTRA RARE</option>
        </select>
      </div>

      {rows.length === 0 && !loading ? (
        <Empty title="Aucune annonce en base"
               hint="L'historique se remplit au fil des détections." />
      ) : (
        <Panel>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-line">
                <tr className="label">
                  <th className="cell font-medium">Palier</th>
                  <th className="cell font-medium">Titre</th>
                  <th className="cell font-medium">Source</th>
                  <th className="cell font-medium text-right">Prix</th>
                  <th className="cell font-medium text-right">Score</th>
                  <th className="cell font-medium text-right">Latence</th>
                  <th className="cell font-medium text-right">Détectée</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((row) => {
                  const tier = TIER_STYLE[row.tier] ?? TIER_STYLE.NORMAL;
                  return (
                    <tr key={row.key} className="hover:bg-white/[.02]">
                      <td className="cell">
                        <span className={cn('badge', tier.className)}>{tier.label}</span>
                      </td>
                      <td className="cell max-w-xs">
                        <a href={row.url} target="_blank" rel="noopener noreferrer"
                           className="text-ink hover:text-accent truncate block" title={row.title}>
                          {row.title}
                        </a>
                      </td>
                      <td className="cell text-muted font-mono">{row.source}</td>
                      <td className="cell text-right font-mono text-ink tabular-nums">{yen(row.price)}</td>
                      <td className="cell text-right font-mono text-muted tabular-nums">{row.score}</td>
                      <td className="cell text-right font-mono text-faint tabular-nums">
                        {latency(row.latency_ms)}
                      </td>
                      <td className="cell text-right font-mono text-faint tabular-nums">
                        {ago(row.detected_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

function toCsv(rows: Listing[]): string {
  const columns = ['source', 'listing_id', 'title', 'price', 'price_eur',
                   'score', 'tier', 'keyword', 'latency_ms', 'detected_at', 'url'];
  const escape = (value: unknown) => {
    const text = String(value ?? '');
    // Les titres japonais contiennent virgules et guillemets : sans
    // échappement le CSV serait décalé d'une colonne.
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [
    columns.join(','),
    ...rows.map((row) => columns.map((c) => escape((row as any)[c])).join(',')),
  ].join('\n');
}
