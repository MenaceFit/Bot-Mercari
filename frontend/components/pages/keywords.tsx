'use client';

import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useStore } from '@/components/store';
import { Badge, Empty, Panel } from '@/components/ui';
import { api } from '@/lib/api';
import { cn, num } from '@/lib/format';

const PRIORITIES = ['high', 'medium', 'low'] as const;

export function KeywordsPage() {
  const { keywords, sources, refresh } = useStore();
  const [form, setForm] = useState({
    name: '', search: '', include: '', exclude: '',
    priority: 'medium', min_price: '', max_price: '',
  });
  // `null` = toutes les sources Buyee. Un mot-clé n'appartient à aucune
  // marketplace en particulier : c'est une requête adressée à la
  // plateforme, qui la répartit.
  const [pick, setPick] = useState<string[] | null>(null);
  const selectable = sources.filter((s) => s.usable || s.simulated);
  const chosen = pick ?? selectable.map((s) => s.source);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const split = (value: string) =>
    value.split(',').map((part) => part.trim()).filter(Boolean);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.name.trim()) return;
    setBusy(true); setError('');
    try {
      await api.post('/api/keywords', {
        name: form.name.trim(),
        search: split(form.search),
        include: split(form.include),
        exclude: split(form.exclude),
        priority: form.priority,
        min_price: form.min_price ? Number(form.min_price) : null,
        max_price: form.max_price ? Number(form.max_price) : null,
        // Liste vide = toutes les sources. C'est aussi la valeur par
        // défaut de la configuration, donc les deux chemins concordent.
        sources: pick ?? [],
      });
      setForm({ name: '', search: '', include: '', exclude: '',
                priority: 'medium', min_price: '', max_price: '' });
      setPick(null);
      await refresh();
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (name: string) => {
    await api.remove(`/api/keywords/${encodeURIComponent(name)}`);
    await refresh();
  };

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-semibold">Keywords</h1>

      <Panel title="Ajouter un mot-clé">
        <form onSubmit={submit} className="p-3 grid md:grid-cols-2 gap-2.5">
          <Field label="Nom" hint="affiché dans les notifications">
            <input className="input" value={form.name} placeholder="Nike Division"
                   onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Recherche" hint="envoyé à Buyee · séparé par des virgules">
            <input className="input" value={form.search} placeholder="ナイキ, nike"
                   onChange={(e) => setForm({ ...form, search: e.target.value })} />
          </Field>
          <Field label="Inclure" hint="alternatives d'écriture (OU) · filtré en local">
            <input className="input" value={form.include} placeholder="ディビジョン, division"
                   onChange={(e) => setForm({ ...form, include: e.target.value })} />
          </Field>
          <Field label="Exclure" hint="disqualifie l'annonce">
            <input className="input" value={form.exclude} placeholder="シューズ, shoes"
                   onChange={(e) => setForm({ ...form, exclude: e.target.value })} />
          </Field>
          <Field
            label="Sources Buyee"
            hint={pick === null
              ? 'toutes les sources activées'
              : `${chosen.length}/${selectable.length} sélectionnée${chosen.length > 1 ? 's' : ''}`}
          >
            <div className="flex flex-wrap gap-1">
              <button
                type="button" onClick={() => setPick(null)}
                aria-pressed={pick === null}
                className={cn(
                  'px-2 py-1 rounded-md border text-2xs transition-colors',
                  pick === null
                    ? 'border-accent/50 bg-accent/10 text-ink'
                    : 'border-line text-faint hover:text-muted',
                )}
              >
                Toutes
              </button>
              {selectable.map((source) => {
                const on = chosen.includes(source.source);
                return (
                  <button
                    key={source.source} type="button"
                    aria-pressed={on}
                    onClick={() => {
                      const base = pick ?? selectable.map((s) => s.source);
                      const next = on
                        ? base.filter((s) => s !== source.source)
                        : [...base, source.source];
                      setPick(next.length === selectable.length ? null : next);
                    }}
                    className={cn(
                      'px-2 py-1 rounded-md border text-2xs transition-colors',
                      on && pick !== null
                        ? 'border-accent/50 bg-accent/10 text-ink'
                        : on
                          ? 'border-line bg-white/[.04] text-muted'
                          : 'border-line text-faint hover:text-muted',
                    )}
                  >
                    {source.label}
                  </button>
                );
              })}
            </div>
          </Field>

          <Field label="Priorité" hint="high 2s · medium 5s · low 20s">
            <div className="flex gap-1">
              {PRIORITIES.map((p) => (
                <button
                  key={p} type="button" onClick={() => setForm({ ...form, priority: p })}
                  aria-pressed={form.priority === p}
                  className={cn('btn flex-1 justify-center',
                                form.priority === p && 'btn-accent')}
                >{p}</button>
              ))}
            </div>
          </Field>
          <Field label="Prix" hint="en yens, laisser vide = pas de borne">
            <div className="flex gap-1.5">
              <input className="input" type="number" placeholder="min" value={form.min_price}
                     onChange={(e) => setForm({ ...form, min_price: e.target.value })} />
              <input className="input" type="number" placeholder="max" value={form.max_price}
                     onChange={(e) => setForm({ ...form, max_price: e.target.value })} />
            </div>
          </Field>

          <div className="md:col-span-2 flex items-center gap-3">
            <button type="submit" className="btn btn-accent" disabled={busy}>
              <Plus className="h-3 w-3" />
              {busy ? 'Ajout…' : 'Ajouter'}
            </button>
            {error && <span className="text-2xs text-danger">{error}</span>}
            <p className="text-2xs text-faint ml-auto">
              Sans « Recherche », le nom sert de requête.
            </p>
          </div>
        </form>
      </Panel>

      {keywords.length === 0 ? (
        <Empty title="Aucun mot-clé" hint="Le scanner tournera sans rien chercher." />
      ) : (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-2.5">
          {keywords.map((keyword: any) => (
            <div key={keyword.name} className="panel p-3 group">
              <div className="flex items-start gap-2">
                <h3 className="text-xs text-ink font-medium flex-1 truncate">{keyword.name}</h3>
                <Badge className={cn(
                  keyword.priority === 'high'
                    ? 'text-accent border-accent/40 bg-accent/10'
                    : 'text-muted border-edge bg-white/5',
                )}>{keyword.priority}</Badge>
                <button
                  type="button" onClick={() => remove(keyword.name)}
                  aria-label={`Retirer ${keyword.name}`}
                  className="text-faint hover:text-danger transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>

              <div className="mt-2 space-y-1 text-2xs">
                <KeyRow label="Recherche" values={keyword.search} tone="accent" />
                <KeyRow label="Inclure" values={keyword.include} />
                <KeyRow label="Exclure" values={keyword.exclude} tone="danger" />
              </div>

              <div className="mt-2.5 pt-2 border-t border-line flex items-center justify-between text-2xs font-mono">
                <span className="text-faint">
                  {num(keyword.detections ?? 0)} détection(s)
                </span>
                <span className="text-faint">
                  {keyword.sources?.length
                    ? keyword.sources
                        .map((id: string) =>
                          sources.find((s) => s.source === id)?.label ?? id)
                        .join(' · ')
                    : 'toutes les sources Buyee'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-2xs text-faint">{hint}</span>}
    </label>
  );
}

function KeyRow({
  label, values, tone,
}: { label: string; values?: string[]; tone?: 'accent' | 'danger' }) {
  if (!values?.length) return null;
  return (
    <div className="flex gap-2">
      <span className="text-faint w-14 shrink-0">{label}</span>
      <span className={cn('font-mono truncate',
        tone === 'accent' ? 'text-accent' : tone === 'danger' ? 'text-danger/80' : 'text-muted')}>
        {values.join(' · ')}
      </span>
    </div>
  );
}
