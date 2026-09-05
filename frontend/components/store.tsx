'use client';

/**
 * État partagé du dashboard, alimenté par le WebSocket.
 *
 * Un seul socket pour toute l'application : chaque page qui ouvrirait le
 * sien multiplierait les connexions et les abonnés côté bus, pour la même
 * information.
 *
 * Le flux est plafonné en mémoire (MAX_FEED). Sans cela, un onglet laissé
 * ouvert une nuit accumulerait des dizaines de milliers d'objets et
 * finirait par ramer — exactement ce que le §47 demande d'éviter.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { api, connectStream, type Listing, type RadarEvent, type SourceInfo } from '@/lib/api';

const MAX_FEED = 500;

type State = {
  connected: boolean;
  paused: boolean;
  feed: Listing[];
  freshKeys: Set<string>;
  metrics: any;
  scheduler: any;
  sources: SourceInfo[];
  keywords: any[];
  snapshot: any;
  events: RadarEvent[];
  refresh: () => Promise<void>;
  setPaused: (paused: boolean) => Promise<void>;
};

const StoreContext = createContext<State | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [paused, setPausedState] = useState(false);
  const [feed, setFeed] = useState<Listing[]>([]);
  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set());
  const [snapshot, setSnapshot] = useState<any>({});
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [keywords, setKeywords] = useState<any[]>([]);
  const [events, setEvents] = useState<RadarEvent[]>([]);
  const freshTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const markFresh = useCallback((key: string) => {
    setFreshKeys((previous) => new Set(previous).add(key));
    const existing = freshTimers.current.get(key);
    if (existing) clearTimeout(existing);
    // Le halo « NEW » retombe après 12 s : passé ce délai il ne distingue
    // plus rien et devient du bruit visuel.
    freshTimers.current.set(
      key,
      setTimeout(() => {
        setFreshKeys((previous) => {
          const next = new Set(previous);
          next.delete(key);
          return next;
        });
        freshTimers.current.delete(key);
      }, 12_000),
    );
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [state, sourceList, keywordList] = await Promise.all([
        api.state(), api.sources(), api.keywords(),
      ]);
      setSnapshot(state);
      setPausedState(Boolean(state.paused));
      setSources(sourceList);
      setKeywords(keywordList.keywords ?? []);
      if (Array.isArray(state.feed) && state.feed.length) {
        setFeed((current) => (current.length ? current : state.feed));
      }
    } catch {
      /* backend absent : le WebSocket réessaiera, l'UI reste utilisable */
    }
  }, []);

  useEffect(() => {
    refresh();
    const disconnect = connectStream((event) => {
      setEvents((current) => [event, ...current].slice(0, 200));

      switch (event.type) {
        case 'snapshot':
          setSnapshot(event.data);
          setPausedState(Boolean(event.data?.paused));
          if (Array.isArray(event.data?.feed)) setFeed(event.data.feed);
          break;
        case 'listing':
          setFeed((current) => [event.data, ...current].slice(0, MAX_FEED));
          markFresh(event.data.key);
          break;
        case 'metrics':
          setSnapshot((current: any) => ({ ...current, metrics: event.data }));
          break;
        case 'scanner_paused':
          setPausedState(Boolean(event.data?.paused));
          break;
        case 'health':
          setSources((current) =>
            current.map((source) => {
              const report = (event.data as any[]).find((r) => r.source === source.source);
              return report ? { ...source, stats: { ...source.stats, healthy: report.ok } } : source;
            }),
          );
          break;
        case 'plan_changed':
          setSnapshot((current: any) => ({ ...current, scheduler: event.data }));
          break;
      }
    }, setConnected);

    // Filet de sécurité : si le WebSocket tombe sans que le navigateur le
    // signale, ce rafraîchissement garde l'écran cohérent.
    const interval = setInterval(refresh, 20_000);
    return () => {
      disconnect();
      clearInterval(interval);
      freshTimers.current.forEach(clearTimeout);
    };
  }, [refresh, markFresh]);

  const setPaused = useCallback(async (next: boolean) => {
    setPausedState(next);       // retour visuel immédiat
    try {
      await api.post('/api/scanner/pause', { paused: next });
    } catch {
      setPausedState(!next);    // le serveur a refusé : on revient en arrière
    }
  }, []);

  const value = useMemo<State>(
    () => ({
      connected, paused, feed, freshKeys,
      metrics: snapshot.metrics ?? {},
      scheduler: snapshot.scheduler ?? {},
      sources, keywords, snapshot, events, refresh, setPaused,
    }),
    [connected, paused, feed, freshKeys, snapshot, sources, keywords, events, refresh, setPaused],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
  const store = useContext(StoreContext);
  if (!store) throw new Error('useStore doit être utilisé dans StoreProvider');
  return store;
}
