'use client';

import { AnimatePresence, motion } from 'framer-motion';
import {
  BarChart3, Bell, Cpu, History, Layers, Pause, Play,
  Radar, Radio, Search, Settings2, Tag,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { StoreProvider, useStore } from '@/components/store';
import { Dot, GridBackdrop, useLenis } from '@/components/ui';
import { cn, duration } from '@/lib/format';

const NAV = [
  { href: '/', label: 'Dashboard', icon: Radar },
  { href: '/search', label: 'Buyee Search', icon: Search },
  { href: '/feed', label: 'Live Feed', icon: Radio },
  { href: '/keywords', label: 'Keywords', icon: Tag },
  { href: '/sources', label: 'Sources', icon: Layers },
  { href: '/analytics', label: 'Analytics', icon: BarChart3 },
  { href: '/history', label: 'History', icon: History },
  { href: '/system', label: 'System', icon: Cpu },
];

function Sidebar() {
  const pathname = usePathname();
  const { counts, keywords } = useStore();

  return (
    <aside className="hidden md:flex w-52 shrink-0 flex-col border-r border-line bg-surface/60 backdrop-blur">
      <div className="h-12 flex items-center gap-2 px-4 border-b border-line">
        <Radar className="h-4 w-4 text-accent" strokeWidth={2.2} />
        <span className="text-xs font-semibold tracking-tight">BUyee Intelligence</span>
      </div>

      <nav className="flex-1 p-2 space-y-0.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href} href={href}
              className={cn(
                'relative flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-xs transition-colors',
                active ? 'text-ink bg-white/[.06]' : 'text-muted hover:text-ink hover:bg-white/[.03]',
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-active"
                  className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-r bg-accent"
                  transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                />
              )}
              <Icon className="h-3.5 w-3.5" strokeWidth={1.9} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="p-3 border-t border-line space-y-1.5">
        {/* « interrogées / connues ». Le premier chiffre est le nombre de
            sources RÉELLEMENT scannées, pas le nombre de cases cochées. */}
        <div className="flex items-center justify-between text-2xs">
          <span className="text-faint">Sources Buyee</span>
          <span className="font-mono text-muted">{counts.scanned}/{counts.known}</span>
        </div>
        {counts.simulated > 0 && (
          <div className="flex items-center justify-between text-2xs">
            <span className="text-faint">dont simulées</span>
            <span className="font-mono text-warn">{counts.simulated}</span>
          </div>
        )}
        <div className="flex items-center justify-between text-2xs">
          <span className="text-faint">Mots-clés</span>
          <span className="font-mono text-muted">{keywords.length}</span>
        </div>
      </div>
    </aside>
  );
}

function Header() {
  const { connected, paused, setPaused, metrics, snapshot, counts } = useStore();
  const uptime = snapshot?.uptime_seconds ?? 0;

  return (
    <header className="h-12 shrink-0 flex items-center gap-3 px-4 border-b border-line bg-surface/60 backdrop-blur">
      <div className="md:hidden flex items-center gap-2">
        <Radar className="h-4 w-4 text-accent" strokeWidth={2.2} />
        <span className="text-xs font-semibold">BUyee Intelligence</span>
      </div>

      {/* L'état porte un LIBELLÉ, pas seulement une couleur. */}
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-line bg-raised">
        <Dot tone={connected ? (paused ? 'warn' : 'live') : 'danger'} pulse={connected && !paused} />
        <span className="text-2xs font-medium tracking-wide">
          {!connected ? 'HORS LIGNE' : paused ? 'EN PAUSE' : 'LIVE'}
        </span>
      </div>

      <div className="hidden md:flex items-center gap-1.5 px-2 py-1 rounded-md border border-line bg-raised">
        <Layers className="h-3 w-3 text-faint" />
        <span className="text-2xs font-mono text-muted">
          Sources {counts.scanned}/{counts.known}
        </span>
      </div>

      <div className="hidden lg:flex items-center gap-4 text-2xs text-faint font-mono">
        <span>{duration(uptime)}</span>
        <span>{metrics?.requests_per_min ?? 0} req/min</span>
        <span>{metrics?.new_per_min ?? 0} détections/min</span>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <NotifyPill />
        <button
          type="button" onClick={() => setPaused(!paused)}
          className={cn('btn', paused && 'btn-accent')}
          aria-pressed={paused}
        >
          {paused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
          <span className="hidden sm:inline">{paused ? 'Reprendre' : 'Pause'}</span>
        </button>
        <Link href="/system" className="btn" aria-label="Système">
          <Settings2 className="h-3 w-3" />
        </Link>
      </div>
    </header>
  );
}

function NotifyPill() {
  const { metrics } = useStore();
  const sent = metrics?.notifications ?? 0;
  const failed = metrics?.notification_failures ?? 0;
  return (
    <div className="hidden sm:flex items-center gap-1.5 px-2 py-1 rounded-md border border-line text-2xs font-mono">
      <Bell className="h-3 w-3 text-faint" />
      <span className="text-muted">{sent}</span>
      {failed > 0 && <span className="text-danger">/{failed} ✗</span>}
    </div>
  );
}

function Inner({ children }: { children: React.ReactNode }) {
  useLenis();
  const pathname = usePathname();
  return (
    <div className="flex h-screen overflow-hidden">
      <GridBackdrop />
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        <main className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={pathname}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className="p-4 max-w-[1600px] mx-auto"
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <StoreProvider>
      <Inner>{children}</Inner>
    </StoreProvider>
  );
}
