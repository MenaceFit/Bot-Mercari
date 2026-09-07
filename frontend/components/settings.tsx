'use client';

/**
 * Tout ce qui se règle, sur une page.
 *
 * Les mots-clés d'abord — c'est la seule chose qu'on touche souvent. Les
 * filtres globaux ensuite. L'état technique en bas, replié : utile le
 * jour où quelque chose cloche, invisible le reste du temps.
 */

import { useState } from 'react';
import { ChevronDown, Plus, Trash2 } from 'lucide-react';
import { useStore } from '@/components/store';
import { api } from '@/lib/api';
import { cn, duration, latency } from '@/lib/format';

const PRIORITIES = [
  { id: 'high', label: 'Haute', hint: 'toutes les 2 s' },
  { id: 'medium', label: 'Moyenne', hint: 'toutes les 5 s' },
  { id: 'low', label: 'Basse', hint: 'toutes les 20 s' },
] as const;

export function SettingsPage() {
  const { keywords, sources, snapshot, metrics, refresh } = useStore();
  const [form, setForm] = useState({
    name: '', search: '', exclude: '', priority: 'medium',
    min_price: '', max_price: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const split = (v: string) =>
    v.split(',').map((p) => p.trim()).filter(Boolean);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) return;
    setBusy(true); setError('');
    try {
      await api.post('/api/keywords', {
        name,
        // Sans terme de recherche explicite, on cherche le nom lui-même :
        // c'est ce que l'utilisateur attend en tapant « Nike ACG ».
        search: split(form.search).length ? split(form.search) : [name],
        exclude: split(form.exclude),
        priority: form.priority,
        min_price: form.min_price ? Number(form.min_price) : null,
        max_price: form.max_price ? Number(form.max_price) : null,
      });
      setForm({ name: '', search: '', exclude: '', priority: 'medium',
                min_price: '', max_price: '' });
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (name: string) => {
    await api.remove(`/api/keywords/${encodeURIComponent(name)}`);
    await refresh();
  };

  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="text-sm font-semibold mb-1">Ajouter un mot-clé</h2>
        <p className="text-xs text-faint mb-4">
          Le nom sert de recherche si tu ne précises rien d’autre.
        </p>

        <form onSubmit={submit} className="space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Nom" hint="ce que tu cherches">
              <input
                className="input w-full" value={form.name} placeholder="Nike ACG"
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="Recherches" hint="séparées par des virgules · optionnel">
              <input
                className="input w-full" value={form.search}
                placeholder="ナイキ ACG, nike acg"
                onChange={(e) => setForm({ ...form, search: e.target.value })}
              />
            </Field>
            <Field label="Exclure" hint="rejette l’annonce si présent">
              <input
                className="input w-full" value={form.exclude}
                placeholder="レプリカ, コピー"
                onChange={(e) => setForm({ ...form, exclude: e.target.value })}
              />
            </Field>
            <Field label="Prix" hint="en yens · optionnel">
              <div className="flex items-center gap-2">
                <input
                  className="input w-full" inputMode="numeric" placeholder="min"
                  value={form.min_price}
                  onChange={(e) => setForm({ ...form, min_price: e.target.value })}
                />
                <span className="text-faint">–</span>
                <input
                  className="input w-full" inputMode="numeric" placeholder="max"
                  value={form.max_price}
                  onChange={(e) => setForm({ ...form, max_price: e.target.value })}
                />
              </div>
            </Field>
          </div>

          <Field label="Fréquence" hint="le budget de requêtes est partagé entre les mots-clés">
            <div className="flex gap-2 flex-wrap">
              {PRIORITIES.map((p) => (
                <button
                  key={p.id} type="button"
                  onClick={() => setForm({ ...form, priority: p.id })}
                  aria-pressed={form.priority === p.id}
                  className={cn(
                    'btn', form.priority === p.id && 'btn-accent',
                  )}
                >
                  {p.label}
                  <span className={cn(
                    'text-xs',
                    form.priority === p.id ? 'text-white/70' : 'text-faint',
                  )}>
                    {p.hint}
                  </span>
                </button>
              ))}
            </div>
          </Field>

          {error && <p className="text-xs text-danger">{error}</p>}

          <button type="submit" disabled={busy || !form.name.trim()}
                  className="btn btn-accent">
            <Plus className="h-4 w-4" />
            Ajouter
          </button>
        </form>
      </section>

      <section className="card">
        <h2 className="text-sm font-semibold p-4 pb-3">
          Mots-clés surveillés
          <span className="ml-2 text-xs text-faint font-normal">
            {keywords.length}
          </span>
        </h2>
        {keywords.length ? (
          <ul className="divide-y divide-line">
            {keywords.map((k: any) => (
              <li key={k.name} className="px-4 py-3 flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm truncate">{k.name}</p>
                  <p className="text-xs text-faint truncate">
                    {(k.search || []).join(' · ') || k.name}
                    {k.min_price || k.max_price
                      ? ` · ${k.min_price ?? 0}–${k.max_price ?? '∞'} ¥` : ''}
                  </p>
                </div>
                <span className="text-xs text-faint font-mono shrink-0">
                  {k.detections ?? 0}
                </span>
                <button
                  type="button" onClick={() => remove(k.name)}
                  className="btn h-8 w-8 p-0 hover:text-danger"
                  aria-label={`Supprimer ${k.name}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-4 pb-4 text-xs text-faint">
            Aucun mot-clé. Le scanner tourne sans rien chercher.
          </p>
        )}
      </section>

      <Details title="État technique">
        <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
          <Row label="Source"
               value={sources[0]?.label ?? '—'} />
          <Row label="Requêtes"
               value={`${sources[0]?.stats?.requests ?? 0} · ${sources[0]?.stats?.requests_per_min ?? 0}/min`} />
          <Row label="Erreurs" value={sources[0]?.stats?.errors ?? 0} />
          <Row label="Latence réseau p95"
               value={latency(sources[0]?.stats?.latency_ms?.p95 ?? 0)} />
          <Row label="Doublons écartés" value={metrics?.duplicates ?? 0} />
          <Row label="Notifications"
               value={`${metrics?.notifications ?? 0} envoyées · ${metrics?.notification_failures ?? 0} échecs`} />
          <Row label="En base" value={snapshot?.store?.listings ?? 0} />
          <Row label="Durée" value={duration(snapshot?.uptime_seconds ?? 0)} />
          {snapshot?.currency?.available && (
            <Row label="Taux JPY→EUR"
                 value={`${snapshot.currency.rate?.toFixed(5)} (${snapshot.currency.stale ? 'périmé' : 'à jour'})`} />
          )}
        </dl>
      </Details>
    </div>
  );
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs text-muted">{label}</span>
      {hint && <span className="text-xs text-faint ml-2">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-line/60 pb-1.5">
      <dt className="text-faint">{label}</dt>
      <dd className="font-mono text-muted tabular-nums truncate">{value}</dd>
    </div>
  );
}

function Details({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card">
      <button
        type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="w-full p-4 flex items-center gap-2 text-sm font-semibold"
      >
        <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
        {title}
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </section>
  );
}
