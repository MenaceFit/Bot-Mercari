export function cn(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}

const NBSP = ' ';

export function yen(value: number) {
  return `¥${Math.round(value).toLocaleString('fr-FR').replace(/ |,/g, NBSP)}`;
}

export function eur(value: number) {
  if (!value) return '';
  return `${Math.round(value).toLocaleString('fr-FR').replace(/ |,/g, NBSP)}${NBSP}€`;
}

/**
 * Une latence de 0 n'est pas « instantané » : c'est une valeur inconnue.
 * L'afficher comme un chiffre serait une promesse que rien ne soutient.
 */
export function latency(ms: number) {
  if (!ms) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function ago(epoch: number) {
  if (!epoch) return '';
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return `il y a ${Math.round(s)} s`;
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}

export function duration(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}`;
}

/** Les paliers portent un LIBELLÉ, jamais une couleur seule. */
export const TIER: Record<string, { label: string; className: string }> = {
  'ULTRA RARE': { label: 'Ultra rare', className: 'bg-rare/15 text-rare' },
  'VERY RARE': { label: 'Très rare', className: 'bg-accent/15 text-accent' },
  RARE: { label: 'Rare', className: 'bg-warn/15 text-warn' },
  NORMAL: { label: 'Normal', className: 'bg-white/[.06] text-faint' },
};
