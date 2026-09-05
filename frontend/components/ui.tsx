'use client';

/**
 * Briques d'interface. Écrites dans l'idiome shadcn/ui — des composants
 * possédés par le projet, pas une dépendance de plus.
 *
 * Choix assumé : plutôt que d'empiler shadcn + Magic UI + Aceternity +
 * React Bits, qui se recouvrent largement, les quelques effets réellement
 * demandés (grille animée, halo, compteurs, pulsations) sont implémentés
 * ici. Moins de dépendances, un style cohérent, et rien d'inutilisé.
 */

import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/format';

/** Compteur animé. Le nombre monte, il ne saute pas. */
export function Counter({
  value, className, decimals = 0, suffix = '',
}: { value: number; className?: string; decimals?: number; suffix?: string }) {
  const raw = useMotionValue(0);
  const spring = useSpring(raw, { stiffness: 90, damping: 20, mass: 0.6 });
  const text = useTransform(spring, (v) =>
    new Intl.NumberFormat('fr-FR', {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    }).format(v) + suffix,
  );
  useEffect(() => { raw.set(Number.isFinite(value) ? value : 0); }, [value, raw]);
  return <motion.span className={className}>{text}</motion.span>;
}

/** Pastille d'état. La couleur double toujours un libellé. */
export function Dot({ tone = 'live', pulse = false }: { tone?: string; pulse?: boolean }) {
  const tones: Record<string, string> = {
    live: 'bg-live', warn: 'bg-warn', danger: 'bg-danger',
    accent: 'bg-accent', faint: 'bg-faint',
  };
  return (
    <span className="relative inline-flex h-1.5 w-1.5">
      {pulse && (
        <span className={cn('absolute inline-flex h-full w-full rounded-full opacity-60 animate-pulse-live', tones[tone])} />
      )}
      <span className={cn('relative inline-flex h-1.5 w-1.5 rounded-full', tones[tone])} />
    </span>
  );
}

export function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('badge', className)}>{children}</span>;
}

export function Panel({
  title, action, children, className,
}: { title?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('panel', className)}>
      {(title || action) && (
        <header className="flex items-center justify-between px-3 py-2 border-b border-line">
          {title && <h2 className="label">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/** Tuile de mesure. Le libellé porte l'unité, la valeur reste nue. */
export function Stat({
  label, value, sub, tone, decimals = 0, suffix = '',
}: {
  label: string; value: number | string; sub?: string;
  tone?: 'accent' | 'live' | 'warn' | 'danger'; decimals?: number; suffix?: string;
}) {
  const tones = {
    accent: 'text-accent', live: 'text-live', warn: 'text-warn', danger: 'text-danger',
  };
  return (
    <div className="panel px-3 py-2.5 relative overflow-hidden group">
      <div className="label">{label}</div>
      <div className={cn('stat-value mt-1', tone && tones[tone])}>
        {typeof value === 'number'
          ? <Counter value={value} decimals={decimals} suffix={suffix} />
          : value}
      </div>
      {sub && <div className="text-2xs text-faint mt-0.5 font-mono">{sub}</div>}
      <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-accent/30 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
    </div>
  );
}

/** Barre de progression fine. Une magnitude, une seule teinte. */
export function Meter({ value, max = 100, tone = 'accent' }: { value: number; max?: number; tone?: string }) {
  const tones: Record<string, string> = {
    accent: 'bg-accent', live: 'bg-live', warn: 'bg-warn', danger: 'bg-danger', rare: 'bg-rare',
  };
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="h-1 w-full bg-white/5 rounded-full overflow-hidden">
      <motion.div
        className={cn('h-full rounded-full', tones[tone])}
        initial={{ width: 0 }} animate={{ width: `${pct}%` }}
        transition={{ type: 'spring', stiffness: 120, damping: 20 }}
      />
    </div>
  );
}

/** Sparkline SVG. Sans repère de grandeur, une courbe ne dit rien : le
 *  pic est donc toujours écrit à côté. */
export function Spark({ data, height = 32, tone = 'accent' }: { data: number[]; height?: number; tone?: string }) {
  if (!data.length) return <div style={{ height }} className="w-full" />;
  const max = Math.max(...data, 1);
  const step = 100 / Math.max(1, data.length - 1);
  const points = data.map((v, i) => `${i * step},${100 - (v / max) * 92}`).join(' ');
  const stroke = { accent: '#3b82f6', live: '#22c55e', warn: '#f59e0b' }[tone] ?? '#3b82f6';
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ height }} className="w-full">
      <polyline
        points={`0,100 ${points} 100,100`} fill={stroke} fillOpacity="0.10" stroke="none"
      />
      <polyline
        points={points} fill="none" stroke={stroke} strokeWidth="1.5"
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"
      />
    </svg>
  );
}

/** Grille de fond, avec un balayage lent façon radar. */
export function GridBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 grid-bg opacity-[.55]" />
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-void/60 to-void" />
      <div className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-accent/[.035] to-transparent animate-scan-sweep" />
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="h-10 w-10 rounded-full border border-line flex items-center justify-center mb-3">
        <Dot tone="faint" />
      </div>
      <p className="text-xs text-muted">{title}</p>
      {hint && <p className="text-2xs text-faint mt-1 max-w-sm">{hint}</p>}
    </div>
  );
}

/** Défilement doux (Lenis), désactivé si l'utilisateur préfère moins de
 *  mouvement — sinon on lui impose exactement ce qu'il a refusé. */
export function useLenis() {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    started.current = true;
    let frame = 0;
    let lenis: any;
    (async () => {
      const Lenis = (await import('@studio-freight/lenis')).default;
      lenis = new Lenis({ duration: 0.9, smoothWheel: true });
      const raf = (time: number) => { lenis.raf(time); frame = requestAnimationFrame(raf); };
      frame = requestAnimationFrame(raf);
    })();
    return () => { cancelAnimationFrame(frame); lenis?.destroy?.(); };
  }, []);
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
