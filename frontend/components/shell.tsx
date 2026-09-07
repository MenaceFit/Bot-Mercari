'use client';

/**
 * Une barre en haut, deux pages. C'est tout.
 *
 * L'ancienne interface avait sept entrées de menu et une dizaine de
 * panneaux. Pour un outil qu'on regarde en diagonale — « est-ce qu'il y a
 * du nouveau ? » — c'était du bruit. Ce qui compte tient sur un écran :
 * l'état du scanner, et les annonces.
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Pause, Play, Radar, Settings } from 'lucide-react';
import { StoreProvider, useStore } from '@/components/store';
import { cn, duration } from '@/lib/format';

function Bar() {
  const { connected, paused, setPaused, metrics, snapshot } = useStore();
  const pathname = usePathname();
  const state = !connected ? 'offline' : paused ? 'paused' : 'live';

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
      <div className="mx-auto max-w-5xl px-4 h-14 flex items-center gap-4">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <Radar className="h-5 w-5 text-accent" strokeWidth={2.2} />
          <span className="font-semibold tracking-tight">Radar Mercari</span>
        </Link>

        {/* L'état est écrit, pas seulement coloré. */}
        <span className="flex items-center gap-2 text-xs">
          <span className="relative flex h-2 w-2">
            {state === 'live' && (
              <span className="absolute inline-flex h-full w-full rounded-full bg-live/70 animate-ping" />
            )}
            <span className={cn(
              'relative inline-flex h-2 w-2 rounded-full',
              state === 'live' ? 'bg-live' : state === 'paused' ? 'bg-warn' : 'bg-danger',
            )} />
          </span>
          <span className={cn(
            'font-medium',
            state === 'live' ? 'text-live' : state === 'paused' ? 'text-warn' : 'text-danger',
          )}>
            {state === 'live' ? 'En direct' : state === 'paused' ? 'En pause' : 'Hors ligne'}
          </span>
        </span>

        <span className="hidden sm:flex items-center gap-4 text-xs text-faint font-mono">
          <span>{duration(snapshot?.uptime_seconds ?? 0)}</span>
          <span>{metrics?.new_listings ?? 0} trouvées</span>
        </span>

        <div className="ml-auto flex items-center gap-2">
          <button
            type="button" onClick={() => setPaused(!paused)}
            className={cn('btn', paused && 'btn-accent')}
          >
            {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
            <span className="hidden sm:inline">{paused ? 'Reprendre' : 'Pause'}</span>
          </button>
          <Link
            href={pathname === '/reglages' ? '/' : '/reglages'}
            className={cn('btn', pathname === '/reglages' && 'text-ink')}
            aria-label="Réglages"
          >
            <Settings className="h-4 w-4" />
            <span className="hidden sm:inline">Réglages</span>
          </Link>
        </div>
      </div>
    </header>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <StoreProvider>
      <div className="min-h-screen">
        <Bar />
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </div>
    </StoreProvider>
  );
}
