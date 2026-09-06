export const yen = (value: number) =>
  '¥' + new Intl.NumberFormat('fr-FR').format(Math.round(value || 0));

export const eur = (value: number) =>
  value ? '≈ ' + new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(value) + ' €' : '';

export const num = (value: number) =>
  new Intl.NumberFormat('fr-FR').format(Math.round(value || 0));

/**
 * Une latence inconnue s'affiche « — », jamais « 0 ms ».
 * Beaucoup de sources ne datent pas leurs annonces : afficher zéro
 * laisserait croire à une détection instantanée.
 */
export const latency = (ms: number) => {
  if (!ms) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
};

export const ago = (timestamp: number) => {
  if (!timestamp) return '—';
  const seconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} j`;
};

export const duration = (seconds: number) => {
  if (!seconds) return '0s';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}j ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds)}s`;
};

export const TIER_STYLE: Record<string, { label: string; className: string }> = {
  'ULTRA RARE': { label: 'ULTRA RARE', className: 'text-danger border-danger/40 bg-danger/10' },
  'VERY RARE': { label: 'VERY RARE', className: 'text-rare border-rare/40 bg-rare/10' },
  RARE: { label: 'RARE', className: 'text-warn border-warn/40 bg-warn/10' },
  NORMAL: { label: 'NORMAL', className: 'text-muted border-edge bg-white/5' },
};

export const SUPPORT_STYLE: Record<string, { label: string; className: string }> = {
  verified: { label: 'VÉRIFIÉE', className: 'text-live border-live/40 bg-live/10' },
  // Les sources « sim_* » sont les seules à se déclarer vérifiées, et pour
  // cause : elles n'ont pas de réseau à décevoir. Les afficher « VÉRIFIÉE »
  // à côté d'une vraie marketplace serait le seul endroit de l'interface où
  // un simulateur pourrait passer pour autre chose.
  simulated: { label: 'SIMULÉE', className: 'text-muted border-edge bg-white/5' },
  cross_only: {
    label: 'VIA CROSS-SEARCH',
    className: 'text-accent border-accent/30 bg-accent/[.07]',
  },
  url_verified: { label: 'URL VÉRIFIÉE', className: 'text-accent border-accent/40 bg-accent/10' },
  needs_selectors: { label: 'À CALIBRER', className: 'text-warn border-warn/40 bg-warn/10' },
  unsupported: { label: 'NON SUPPORTÉE', className: 'text-faint border-edge bg-white/5' },
  unknown: { label: 'INCONNU', className: 'text-faint border-edge bg-white/5' },
};

/** Le badge d'une source. Un simulateur ne porte jamais celui d'une vraie. */
export function supportStyle(source: {
  source?: string; name?: string; support: string; cross_only?: boolean;
}) {
  const id = source.source ?? source.name ?? '';
  if (id.startsWith('sim_')) return SUPPORT_STYLE.simulated;
  // Une source sans URL propre ne sera jamais « calibrée » : lui coller
  // « À CALIBRER » enverrait l'utilisateur lancer une commande sans effet.
  if (source.cross_only) return SUPPORT_STYLE.cross_only;
  return SUPPORT_STYLE[source.support] ?? SUPPORT_STYLE.unknown;
}

export function cn(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}

/**
 * Familles de sources Buyee.
 *
 * Une métasource, un flux d'annonces et un catalogue marchand ne se lisent
 * pas de la même manière : le premier agrège, le deuxième se snipe, le
 * troisième n'a rien à sniper. L'interface le dit au lieu de tout aligner.
 */
export const KIND_STYLE: Record<string, { label: string; className: string }> = {
  meta: { label: 'MÉTASOURCE', className: 'text-accent border-accent/40 bg-accent/10' },
  c2c: { label: 'C2C', className: 'text-muted border-edge bg-white/5' },
  auction: { label: 'ENCHÈRES', className: 'text-muted border-edge bg-white/5' },
  catalog: { label: 'CATALOGUE', className: 'text-faint border-edge bg-white/5' },
  simulator: { label: 'SIMULÉE', className: 'text-muted border-edge bg-white/5' },
};

/** Libellé court d'une source, pour le flux. */
export function sourceLabel(source: string, sources: { source: string; label: string }[]) {
  return sources.find((s) => s.source === source)?.label ?? source;
}
