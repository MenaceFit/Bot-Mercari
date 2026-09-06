/**
 * Client de l'API locale + WebSocket.
 *
 * Le WebSocket est le chemin par défaut : une annonce détectée doit
 * apparaître sans rafraîchissement. Le REST ne sert qu'à l'état initial,
 * à l'historique et aux actions.
 *
 * La reconnexion est à recul exponentiel plafonné : un backend redémarré
 * ne doit pas laisser le dashboard mort, mais un backend éteint ne doit
 * pas non plus marteler la boucle d'événements du navigateur.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (typeof window !== 'undefined' && window.location.port === '3000'
    ? 'http://127.0.0.1:8899'
    : '');

export type Listing = {
  key: string;
  source: string;
  listing_id: string;
  title: string;
  url: string;
  /** Lien Buyee — le bouton principal. On achète PAR Buyee. */
  buy_url: string;
  /** Lien vers la marketplace d'origine, vide s'il n'est pas certain. */
  origin_url: string;
  /** 'crosssearch' quand l'annonce est arrivée par la recherche transversale. */
  via: string;
  price: number;
  currency: string;
  price_eur: number;
  image_url: string;
  seller: string;
  keyword: string;
  keywords: string[];
  score: number;
  tier: string;
  created_at: number;
  detected_at: number;
  latency_ms: number;
  network_ms: number;
  end_to_end_ms: number;
  score_parts?: Record<string, number>;
};

export type SourceInfo = {
  source: string;
  label: string;
  /** 'meta' | 'c2c' | 'auction' | 'catalog' | 'simulator' */
  kind: string;
  /** Plateforme d'appartenance : 'buyee', 'mandarake', … */
  platform: string;
  platform_label: string;
  support: string;
  support_note: string;
  /** URL réellement observées qui attestent cette source. */
  evidence: string[];
  in_crosssearch: boolean;
  aggregates: string[];
  search_url: string;
  /** Atteignable seulement via la recherche transversale : rien à calibrer. */
  cross_only: boolean;
  simulated: boolean;
  enabled: boolean;
  /** Activée ET réellement interrogeable maintenant. */
  usable: boolean;
  stats: Record<string, any>;
  breaker: Record<string, any>;
};

export type SourceCounts = {
  known: number;
  enabled: number;
  usable: number;
  simulated: number;
  scanned: number;
};

export type SearchOutcome = {
  source: string;
  label: string;
  queried: boolean;
  ok: boolean;
  count: number;
  latency_ms: number;
  error: string;
  skipped_reason: string;
  support: string;
};

export type SearchReport = {
  query: string;
  total: number;
  elapsed_ms: number;
  sources_queried: number;
  sources_ok: number;
  per_source: Record<string, number>;
  outcomes: SearchOutcome[];
  listings: Listing[];
};

export type RadarEvent = { type: string; data: any; at: number };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} sur ${path}`);
  return res.json();
}

export const api = {
  state: () => get<any>('/api/state'),
  sources: () => get<SourceInfo[]>('/api/sources'),
  system: () => get<any>('/api/system'),
  keywords: () => get<{ keywords: any[] }>('/api/keywords'),
  analytics: (minutes = 60) => get<any>(`/api/analytics?minutes=${minutes}`),
  buyee: () => get<{ counts: SourceCounts; sources: SourceInfo[] }>('/api/buyee'),
  /** Une requête → toutes les sources activées, en parallèle côté serveur. */
  search: async (query: string, sources?: string[]): Promise<SearchReport> => {
    const res = await fetch(`${API_BASE}/api/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, sources }),
    });
    if (!res.ok) throw new Error((await res.json()).error ?? `${res.status}`);
    return res.json();
  },
  listings: (params: Record<string, string | number> = {}) => {
    const query = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== '' && v !== 0)
        .map(([k, v]) => [k, String(v)]),
    );
    return get<Listing[]>(`/api/listings?${query}`);
  },
  async post<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
    return res.json();
  },
  async remove<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  },
};

export function connectStream(
  onEvent: (event: RadarEvent) => void,
  onStatus: (connected: boolean) => void,
): () => void {
  let socket: WebSocket | null = null;
  let retry = 500;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const open = () => {
    if (closed) return;
    const base = API_BASE || window.location.origin;
    const url = base.replace(/^http/, 'ws') + '/ws';
    socket = new WebSocket(url);

    socket.onopen = () => {
      retry = 500;
      onStatus(true);
    };
    socket.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data));
      } catch {
        /* trame illisible : on l'ignore plutôt que de tuer le flux */
      }
    };
    socket.onclose = () => {
      onStatus(false);
      if (closed) return;
      // Plafonné à 10 s : un backend éteint ne doit pas marteler le
      // navigateur, mais un redémarrage doit être rattrapé vite.
      timer = setTimeout(open, retry);
      retry = Math.min(retry * 2, 10_000);
    };
    socket.onerror = () => socket?.close();
  };

  open();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    socket?.close();
  };
}
