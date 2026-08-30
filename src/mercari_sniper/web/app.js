/* Mercari Sniper — logique du dashboard.
   Aucune dépendance externe : tout est servi en local. */
'use strict';

const MAX_FEED = 300;

const state = {
  filters: null,
  feed: [],
  stats: {},
  sources: [],
  keywords: [],
  coverage: null,
  activity: [],
  backend: '',
  discord: false,
  frozen: false,
  sound: false,
  rarity: '',
  search: '',
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
};

/* ── Formatage ──────────────────────────────────────────────────────── */
const nf = new Intl.NumberFormat('fr-FR');
const yen = (n) => '¥' + nf.format(Number(n) || 0);

function compact(n) {
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(n < 10000 ? 1 : 0).replace('.0', '') + 'k';
  return (n / 1e6).toFixed(1).replace('.0', '') + 'M';
}

function seconds(ms) {
  if (!ms) return '—';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} j`;
}

function duration(s) {
  s = Math.floor(s || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${String(m).padStart(2, '0')}`;
  if (m) return `${m} min`;
  return `${s} s`;
}

const TIERS = {
  'ULTRA RARE': { cls: 'tier--ultra', icon: '#i-alert', label: 'Ultra rare' },
  'RARE':       { cls: 'tier--rare', icon: '#i-star', label: 'Rare' },
  'PREMIUM':    { cls: 'tier--premium', icon: '#i-check', label: 'Premium' },
};

const HEALTH = {
  ok:        { cls: 'status--ok', icon: '#i-check', label: 'Active' },
  saturated: { cls: 'status--saturated', icon: '#i-alert', label: 'Saturée' },
  degraded:  { cls: 'status--degraded', icon: '#i-alert', label: 'Instable' },
  error:     { cls: 'status--error', icon: '#i-error', label: 'En échec' },
  paused:    { cls: '', icon: '#i-pause', label: 'En pause' },
  starting:  { cls: '', icon: '#i-clock', label: 'Démarrage' },
};

function svgIcon(href, cls = 'icon') {
  return `<svg class="${cls}" aria-hidden="true"><use href="${href}"/></svg>`;
}

/* ── Alerte sonore (WebAudio : aucun fichier à charger) ─────────────── */
let audioCtx = null;
function beep() {
  if (!state.sound) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const t = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain); gain.connect(audioCtx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, t);
    osc.frequency.setValueAtTime(1245, t + 0.08);
    gain.gain.setValueAtTime(0.18, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.26);
    osc.start(t); osc.stop(t + 0.28);
  } catch { /* audio indisponible : sans conséquence */ }
}

/* ── Toasts ─────────────────────────────────────────────────────────── */
function toast(title, body, kind) {
  const node = el('div', 'toast' + (kind ? ` toast--${kind}` : ''));
  const head = el('div', 'toast__title');
  head.textContent = title;
  node.appendChild(head);
  if (body) {
    const sub = el('div', 'toast__body');
    sub.textContent = body;
    node.appendChild(sub);
  }
  const stack = $('toasts');
  stack.appendChild(node);
  // Au-delà de 3, les toasts masqueraient le flux : on évince les plus anciens.
  while (stack.children.length > 3) stack.firstElementChild.remove();
  setTimeout(() => node.remove(), 4500);
}

/* ── Indicateurs ────────────────────────────────────────────────────── */
function renderKpis() {
  const s = state.stats;
  $('kpiHits').textContent = nf.format(s.total_hits || 0);
  $('kpiHitsSub').textContent = s.listings_24h
    ? `session · ${nf.format(s.listings_24h)} en base sur 24 h`
    : 'depuis le démarrage';

  $('kpiLatency').textContent = seconds(s.latency_p50_ms);
  $('kpiLatencySub').textContent = s.latency_p95_ms
    ? `p95 ${seconds(s.latency_p95_ms)}`
    : 'médiane · p95';

  $('kpiRate').textContent = s.polls_per_minute != null ? nf.format(s.polls_per_minute) : '—';
  $('kpiRateSub').textContent = s.throttled
    ? `ralenti · budget ${s.rate_limit}/s`
    : `budget ${s.rate_limit ?? '—'}/s`;

  $('kpiScanned').textContent = compact(s.total_items_seen);
  $('kpiScannedSub').textContent = s.total_overflows
    ? `${s.total_overflows} rattrapage(s)`
    : `${compact(s.buffer_size || 0)} en mémoire`;

  // Le rendement explique « il ne trouve rien » : un chiffre proche de zéro
  // signale des requêtes trop larges, pas un marché calme.
  const perMille = (s.yield_ratio || 0) * 1000;
  $('kpiYield').textContent = s.total_items_seen
    ? (perMille >= 10 ? Math.round(perMille) : perMille.toFixed(1))
    : '—';
  const drops = s.drops || {};
  const filtered = Object.values(drops).reduce((a, b) => a + b, 0);
  $('kpiYieldSub').textContent = filtered
    ? `${compact(filtered)} écartée(s) · ${s.filtered_terms || 0} filtres`
    : 'trouvailles pour 1 000 annonces';
  $('kpiYield').title = Object.entries(drops)
    .map(([reason, n]) => `${nf.format(n)} ${reason}`)
    .join(' · ') || '';

  $('uptime').textContent = s.uptime_seconds ? `Actif ${duration(s.uptime_seconds)}` : '';
  $('backendLabel').textContent = state.backend === 'simulator'
    ? 'Mode démo' : (state.discord ? 'Discord actif' : 'Discord inactif');
}

/* ── Graphe d'activité (aire + ligne, survol avec repère) ───────────── */
const CHART_W = 320, CHART_H = 64;

function renderChart() {
  const svg = $('chartSvg');
  const data = state.activity || [];
  if (!data.length || data.every((v) => v === 0)) {
    svg.innerHTML = `<text x="4" y="34" class="chart__empty">Aucune trouvaille sur la période</text>`;
    $('chartScale').textContent = '';
    return;
  }

  const max = Math.max(...data, 1);
  // Sans repère de grandeur, une courbe ne dit rien : le même tracé vaut
  // pour 1 trouvaille par minute comme pour 200.
  $('chartScale').textContent = `pic ${nf.format(max)}/min`;
  const step = CHART_W / Math.max(1, data.length - 1);
  const y = (v) => CHART_H - 4 - (v / max) * (CHART_H - 12);
  const points = data.map((v, i) => [i * step, y(v)]);

  const line = points.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
  const area = `${line} L${CHART_W},${CHART_H} L0,${CHART_H} Z`;

  svg.innerHTML =
    `<path class="chart__area" d="${area}"/>` +
    `<path class="chart__line" d="${line}"/>` +
    `<line class="chart__cursor" id="chartCursor" y1="0" y2="${CHART_H}" style="display:none"/>` +
    `<circle class="chart__dot" id="chartDot" r="3.5" style="display:none"/>`;
}

function bindChartHover() {
  const figure = $('chart');
  const svg = $('chartSvg');
  const tip = $('chartTip');

  const move = (event) => {
    const data = state.activity || [];
    if (!data.length) return;
    const rect = svg.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const index = Math.round(ratio * (data.length - 1));

    const cursor = $('chartCursor');
    const dot = $('chartDot');
    if (!cursor || !dot) return;

    const max = Math.max(...data, 1);
    const step = CHART_W / Math.max(1, data.length - 1);
    const px = index * step;
    const py = CHART_H - 4 - (data[index] / max) * (CHART_H - 12);

    cursor.setAttribute('x1', px); cursor.setAttribute('x2', px);
    cursor.style.display = '';
    dot.setAttribute('cx', px); dot.setAttribute('cy', py);
    dot.style.display = '';

    const minutesAgo = data.length - 1 - index;
    tip.hidden = false;
    tip.style.left = `${(px / CHART_W) * rect.width}px`;
    tip.textContent = `${minutesAgo === 0 ? "à l'instant" : `il y a ${minutesAgo} min`} · ${data[index]}`;
  };

  figure.addEventListener('pointermove', move);
  figure.addEventListener('pointerleave', () => {
    tip.hidden = true;
    const cursor = $('chartCursor'), dot = $('chartDot');
    if (cursor) cursor.style.display = 'none';
    if (dot) dot.style.display = 'none';
  });
}

/* ── Mots-clés ──────────────────────────────────────────────────────── */
function renderKeywords() {
  const list = $('kwList');
  list.textContent = '';
  $('kwCount').textContent = state.keywords.length;
  $('kwEmpty').hidden = state.keywords.length > 0;

  for (const entry of state.keywords) {
    const item = el('li', 'kw');

    const name = el('span', 'kw__name');
    name.textContent = entry.keyword;
    name.title = entry.keyword;
    item.appendChild(name);

    if (!entry.covered) {
      const warn = el('span', 'kw__warn');
      warn.innerHTML = svgIcon('#i-alert');
      warn.title = "Aucune source ne couvre ce mot-clé pour l'instant";
      item.appendChild(warn);
    }

    const hits = el('span', 'kw__hits');
    hits.textContent = entry.hits ? nf.format(entry.hits) : '·';
    // La question qu'on se pose vraiment devant un mot-clé silencieux :
    // « est-ce le marché qui est calme, ou le bot qui passe trop rarement ? »
    hits.title = entry.interval
      ? `${nf.format(entry.hits || 0)} trouvaille(s) · surveillé toutes les ${entry.interval}s`
      : `${nf.format(entry.hits || 0)} trouvaille(s)`;
    item.appendChild(hits);

    const del = el('button', 'kw__del');
    del.type = 'button';
    del.innerHTML = svgIcon('#i-close');
    del.title = `Retirer « ${entry.keyword} »`;
    del.addEventListener('click', () => removeKeyword(entry.keyword));
    item.appendChild(del);

    list.appendChild(item);
  }
}

/* ── Sources ────────────────────────────────────────────────────────── */
function renderSources() {
  const list = $('srcList');
  list.textContent = '';
  $('srcCount').textContent = state.sources.length;
  $('srcEmpty').hidden = state.sources.length > 0;

  for (const source of state.sources) {
    const health = HEALTH[source.health] || HEALTH.starting;
    const item = el('li', 'src');

    const status = el('span', `status ${health.cls}`);
    status.innerHTML = `${svgIcon(health.icon)}<span>${health.label}</span>`;
    status.title = source.last_error || health.label;

    const query = el('span', 'src__q');
    query.textContent = source.query;
    query.title = source.query;

    if (source.split_from) {
      const from = el('span', 'src__from');
      from.innerHTML = svgIcon('#i-split');
      from.title = `Requête précise créée à la place de « ${source.split_from} », ` +
                   `qui consommait du budget sans rien rapporter`;
      item.appendChild(from);
    }

    const numbers = el('span', 'src__n');
    numbers.textContent = `${nf.format(source.hits)} ★ · ${source.interval}s`;
    const perMille = (source.yield_ratio || 0) * 1000;
    numbers.title =
      `${nf.format(source.polls)} requêtes · ${nf.format(source.new_items)} annonces neuves` +
      ` · rendement ${perMille.toFixed(1)} ‰` +
      ` · ${source.last_duration_ms} ms` +
      (source.gaps ? ` · ${source.gaps} trou(s) rattrapé(s)` : '');

    // Échelle plafonnée à 5 % : au-delà, une requête est déjà excellente,
    // et une échelle linéaire jusqu'à 100 % écraserait tout le reste à zéro.
    const bar = el('span', 'src__yield');
    const filled = Math.min(1, (source.yield_ratio || 0) / 0.05);
    const fill = el('i');
    fill.style.width = `${Math.max(source.items_seen ? 2 : 0, filled * 100)}%`;
    bar.appendChild(fill);
    bar.title = numbers.title;

    item.append(query, bar, numbers, status);
    list.appendChild(item);
  }
}

/* ── Couverture ─────────────────────────────────────────────────────── */
function renderCoverage() {
  const panel = $('coveragePanel');
  const body = $('coverageBody');
  const c = state.coverage;
  if (!c) { panel.hidden = true; return; }

  const problems = [];
  if (c.saturated) {
    problems.push(
      `${c.sources} sources pour un budget de ${c.budget_per_second} req/s : ` +
      `chaque mot-clé n'est réellement revisité que toutes les ` +
      `${c.effective_interval}s au lieu des ${c.target_interval}s visées. ` +
      `Augmente poll.global_rate_limit dans config.yaml, ou retire des mots-clés.`
    );
  }
  if (c.low_yield && c.low_yield.length) {
    problems.push(
      `Requête(s) trop large(s), beaucoup d'annonces examinées pour presque ` +
      `aucune trouvaille : ${c.low_yield.join(', ')}. Elles seront remplacées ` +
      `automatiquement par des requêtes précises.`
    );
  }
  if (c.uncovered_keywords && c.uncovered_keywords.length) {
    problems.push(
      `Aucune source ne couvre : ${c.uncovered_keywords.slice(0, 5).join(', ')}` +
      (c.uncovered_keywords.length > 5 ? ` (+${c.uncovered_keywords.length - 5})` : '')
    );
  }

  if (!problems.length) { panel.hidden = true; return; }

  panel.hidden = false;
  body.textContent = '';
  for (const text of problems) {
    const warn = el('div', 'coverage-warn');
    warn.innerHTML = svgIcon('#i-alert');
    const span = el('span');
    span.textContent = text;
    warn.appendChild(span);
    body.appendChild(warn);
  }
}

/* ── Filtrage du bruit ──────────────────────────────────────────────── */
function renderFilters() {
  const f = state.filters;
  const groups = $('filterGroups');
  const chips = $('excludeList');
  groups.textContent = '';
  chips.textContent = '';
  if (!f) return;

  $('filterCount').textContent = f.active_terms || 0;

  for (const group of f.available || []) {
    const row = el('li');
    const label = el('label', 'filter-row');

    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = (f.noise_groups || []).includes(group.name);
    box.addEventListener('change', () => {
      const wanted = new Set(state.filters.noise_groups || []);
      box.checked ? wanted.add(group.name) : wanted.delete(group.name);
      saveFilters({ noise_groups: [...wanted] });
    });

    const text = el('span', 'filter-row__text');
    const title = el('span', 'filter-row__label');
    title.textContent = `${group.label} (${group.terms})`;
    const sample = el('span', 'filter-row__sample');
    sample.textContent = (group.sample || []).join(' · ');
    text.append(title, sample);

    label.append(box, text);
    row.appendChild(label);
    groups.appendChild(row);
  }

  for (const word of f.exclude_words || []) {
    const chip = el('li', 'chip');
    const text = el('span');
    text.textContent = word;
    const remove = el('button', 'chip__x');
    remove.type = 'button';
    remove.innerHTML = svgIcon('#i-close');
    remove.title = `Ne plus exclure « ${word} »`;
    remove.addEventListener('click', () => {
      saveFilters({
        exclude_words: (state.filters.exclude_words || []).filter((w) => w !== word),
      });
    });
    chip.append(text, remove);
    chips.appendChild(chip);
  }
}

async function saveFilters(patch) {
  try {
    const response = await fetch('/api/filters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!response.ok) throw new Error(await response.text());
    state.filters = await response.json();
    renderFilters();
    toast('Filtrage mis à jour', `${state.filters.active_terms} termes écartés`, 'ok');
  } catch (error) {
    toast('Filtrage non enregistré', String(error.message || error), 'error');
  }
}

/* ── Flux ───────────────────────────────────────────────────────────── */
function cardFor(item) {
  const tier = TIERS[item.rarity] || TIERS.PREMIUM;
  const card = el('article', 'card');

  if (item.image) {
    const img = el('img', 'card__thumb');
    img.src = item.image;
    img.alt = '';
    img.loading = 'lazy';
    img.addEventListener('error', () => img.replaceWith(placeholder()));
    card.appendChild(img);
  } else {
    card.appendChild(placeholder());
  }

  const body = el('div', 'card__body');

  const top = el('div', 'card__top');
  const badge = el('span', `tier ${tier.cls}`);
  badge.innerHTML = `${svgIcon(tier.icon)}<span>${tier.label}</span>`;
  top.appendChild(badge);

  if (item.latency_ms) {
    const lat = el('span', 'latency' + (item.latency_ms < 15000 ? ' latency--fast' : ''));
    lat.innerHTML = `${svgIcon('#i-clock')}<span>${seconds(item.latency_ms)}</span>`;
    lat.title = 'Délai entre la publication par le vendeur et la détection';
    top.appendChild(lat);
  }

  const price = el('span', 'card__price');
  price.textContent = yen(item.price);
  top.appendChild(price);
  body.appendChild(top);

  const title = el('h3', 'card__title');
  const link = el('a');
  link.href = item.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = item.title;
  title.appendChild(link);
  body.appendChild(title);

  const meta = el('div', 'card__meta');
  if (item.backfill) {
    const chip = el('span', 'chip chip--backfill');
    chip.textContent = 'rattrapage';
    chip.title = 'Trouvée en repassant les annonces déjà scannées';
    meta.appendChild(chip);
  }
  for (const keyword of (item.matched || []).slice(0, 3)) {
    const chip = el('span', 'chip');
    chip.textContent = keyword;
    meta.appendChild(chip);
  }
  if ((item.matched || []).length > 3) {
    const chip = el('span', 'chip');
    chip.textContent = `+${item.matched.length - 3}`;
    meta.appendChild(chip);
  }

  const time = el('span', 'card__time');
  time.dataset.created = item.created || '';
  time.textContent = ago(item.created);
  meta.appendChild(time);

  const out = el('a', 'card__link');
  out.href = item.url;
  out.target = '_blank';
  out.rel = 'noopener noreferrer';
  out.innerHTML = svgIcon('#i-external');
  out.title = 'Ouvrir sur Mercari';
  meta.appendChild(out);

  if (item.buyee_url) {
    const order = el('a', 'buy');
    order.href = item.buyee_url;
    order.target = '_blank';
    order.rel = 'noopener noreferrer';
    order.innerHTML = `${svgIcon('#i-cart')}<span>Commander</span>`;
    order.title = 'Commander via Buyee, qui achète sur Mercari et réexpédie';
    meta.appendChild(order);
  }

  body.appendChild(meta);
  card.appendChild(body);
  return card;
}

function placeholder() {
  const node = el('div', 'card__thumb card__thumb--ph');
  node.textContent = '—';
  return node;
}

function visibleItems() {
  const needle = state.search.toLowerCase();
  return state.feed.filter((item) => {
    if (state.rarity && item.rarity !== state.rarity) return false;
    if (needle && !(item.title || '').toLowerCase().includes(needle)) return false;
    return true;
  });
}

function renderFeed() {
  const items = visibleItems();
  const feed = $('feed');
  feed.textContent = '';
  for (const item of items) feed.appendChild(cardFor(item));

  $('feedCount').textContent = state.feed.length
    ? `${nf.format(items.length)} / ${nf.format(state.feed.length)}`
    : '';

  const empty = $('feedEmpty');
  empty.hidden = items.length > 0;
  if (!items.length) {
    const noKeywords = state.keywords.length === 0;
    const filtered = state.feed.length > 0;
    $('emptyTitle').textContent = noKeywords
      ? 'Ajoute ton premier mot-clé'
      : filtered ? 'Aucun résultat pour ce filtre' : "En attente d'annonces";
    $('emptyText').textContent = noKeywords
      ? "Saisis un mot-clé dans le panneau de gauche. Le bot crée automatiquement "
        + "la requête correspondante et remonte aussitôt ce qui a déjà été scanné."
      : filtered ? 'Modifie la recherche ou choisis une autre rareté.'
      : 'Le bot scanne en continu. Les nouvelles annonces apparaîtront ici dès leur publication.';
  }
}

/* Rafraîchit les durées relatives sans reconstruire tout le flux. */
setInterval(() => {
  for (const node of document.querySelectorAll('.card__time')) {
    const created = Number(node.dataset.created);
    if (created) node.textContent = ago(created);
  }
}, 15000);

/* ── Application d'un instantané ────────────────────────────────────── */
function applySnapshot(data) {
  state.stats = data.stats || {};
  state.sources = data.sources || [];
  state.keywords = data.keywords || [];
  state.coverage = data.coverage || null;
  state.activity = data.activity || [];
  state.feed = data.feed || [];
  state.backend = data.backend || '';
  state.discord = !!data.discord;
  state.filters = data.filters || state.filters;
  renderKpis(); renderChart(); renderKeywords(); renderSources();
  renderCoverage(); renderFilters(); renderFeed();
}

/* ── Actions ────────────────────────────────────────────────────────── */
async function addKeyword(keyword) {
  const response = await fetch('/api/keywords', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'échec');

  if (data.added) {
    state.keywords = data.keywords;
    state.sources = data.sources;
    state.coverage = data.coverage;
    renderKeywords(); renderSources(); renderCoverage(); renderFeed();

    const details = [];
    if (data.source_created) details.push(`source « ${data.source_created} » créée`);
    if (data.backfilled) details.push(`${data.backfilled} annonce(s) rattrapée(s)`);
    toast(`Mot-clé ajouté : ${keyword}`, details.join(' · ') || 'recherche en cours');
  } else {
    toast('Mot-clé non ajouté', data.reason || 'déjà présent', 'warn');
  }
}

async function removeKeyword(keyword) {
  const response = await fetch(`/api/keywords/${encodeURIComponent(keyword)}`, {
    method: 'DELETE',
  });
  const data = await response.json();
  state.keywords = data.keywords;
  state.sources = data.sources;
  state.coverage = data.coverage;
  renderKeywords(); renderSources(); renderCoverage(); renderFeed();
  toast(`Mot-clé retiré : ${keyword}`);
}

/* ── WebSocket ──────────────────────────────────────────────────────── */
let socket = null;
let retryDelay = 500;

function setConnection(kind, label) {
  const node = $('conn');
  node.classList.toggle('is-live', kind === 'live');
  node.classList.toggle('is-down', kind === 'down');
  $('connLabel').textContent = label;
}

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws`);

  socket.addEventListener('open', () => {
    retryDelay = 500;
    setConnection('live', 'En direct');
  });

  socket.addEventListener('close', () => {
    setConnection('down', 'Reconnexion…');
    // Backoff exponentiel plafonné : ne martèle pas un serveur arrêté.
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  });

  socket.addEventListener('error', () => socket.close());

  socket.addEventListener('message', (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }

    switch (message.type) {
      case 'snapshot':
        applySnapshot(message.data);
        break;

      case 'listing': {
        if (state.frozen) break;
        state.feed.unshift(message.data);
        if (state.feed.length > MAX_FEED) state.feed.length = MAX_FEED;
        // Le graphe est agrégé par minute côté serveur ; on incrémente la
        // minute courante localement pour qu'il réagisse sans attendre.
        if (state.activity.length) {
          state.activity[state.activity.length - 1] += 1;
          renderChart();
        }
        renderFeed();
        if (!message.data.backfill) {
          beep();
          const tier = TIERS[message.data.rarity] || TIERS.PREMIUM;
          toast(`${tier.label} · ${yen(message.data.price)}`,
                message.data.title.slice(0, 70));
        }
        break;
      }

      case 'stats':
        state.stats = { ...state.stats, ...message.data };
        renderKpis();
        break;

      case 'keywords':
        state.keywords = message.data;
        renderKeywords();
        break;

      case 'filters':
        state.filters = message.data;
        renderFilters();
        break;

      case 'source_split':
        // Remplacement automatique d'une requête trop large : c'est une
        // amélioration silencieuse, mais l'utilisateur doit savoir pourquoi
        // la liste de ses sources vient de changer sous ses yeux.
        toast(
          'Requête remplacée',
          `« ${message.data.query} » examinait ${nf.format(message.data.items_seen)} ` +
          `annonces pour presque rien — remplacée par ${message.data.into.length} ` +
          `requête(s) précise(s).`,
          'ok'
        );
        break;

      case 'source_error':
        toast('Source en erreur', `${message.data.query} — ${message.data.error}`, 'error');
        break;
    }
  });
}

/* Les compteurs agrégés (activité par minute, état des sources, hits par
   mot-clé) ne sont pas poussés à chaque annonce : on les rafraîchit
   régulièrement. Le serveur est local, l'appel est négligeable. */
async function refreshAggregates() {
  try {
    const response = await fetch('/api/state');
    if (!response.ok) return;
    const data = await response.json();
    state.activity = data.activity || [];
    state.sources = data.sources || [];
    state.keywords = data.keywords || [];
    state.coverage = data.coverage || null;
    state.filters = data.filters || state.filters;
    state.stats = { ...state.stats, ...(data.stats || {}) };
    renderChart(); renderSources(); renderKeywords();
    renderCoverage(); renderFilters(); renderKpis();
  } catch { /* hors ligne : la reconnexion WebSocket s'en chargera */ }
}
setInterval(refreshAggregates, 5000);

/* ── Thème ──────────────────────────────────────────────────────────── */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === 'dark'
    || (theme === '' && matchMedia('(prefers-color-scheme: dark)').matches);
  $('themeBtn').querySelector('use').setAttribute('href', dark ? '#i-sun' : '#i-moon');
}

/* ── Câblage ────────────────────────────────────────────────────────── */
function init() {
  applyTheme(localStorage.getItem('sniper-theme') || '');
  bindChartHover();

  $('kwForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('kwInput');
    const keyword = input.value.trim();
    if (!keyword) return;
    input.value = '';
    try {
      await addKeyword(keyword);
    } catch (error) {
      toast("Échec de l'ajout", String(error.message || error), 'error');
    }
  });

  $('excludeForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('excludeInput');
    const word = input.value.trim();
    if (!word) return;
    input.value = '';
    const words = state.filters?.exclude_words || [];
    if (words.includes(word)) return;
    saveFilters({ exclude_words: [...words, word] });
  });

  $('searchInput').addEventListener('input', (event) => {
    state.search = event.target.value.trim();
    renderFeed();
  });
  $('searchInput').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.target.value = ''; state.search = ''; renderFeed(); }
  });

  for (const button of document.querySelectorAll('.seg')) {
    button.addEventListener('click', () => {
      for (const other of document.querySelectorAll('.seg')) {
        other.classList.toggle('is-active', other === button);
      }
      state.rarity = button.dataset.rarity;
      renderFeed();
    });
  }

  $('clearBtn').addEventListener('click', () => { state.feed = []; renderFeed(); });

  $('freezeBtn').addEventListener('click', (event) => {
    state.frozen = !state.frozen;
    const button = event.currentTarget;
    button.setAttribute('aria-pressed', String(state.frozen));
    button.querySelector('use').setAttribute('href', state.frozen ? '#i-play' : '#i-pause');
    button.title = state.frozen ? 'Reprendre le fil' : 'Figer le fil';
  });

  $('soundBtn').addEventListener('click', (event) => {
    state.sound = !state.sound;
    const button = event.currentTarget;
    button.setAttribute('aria-pressed', String(state.sound));
    button.querySelector('use').setAttribute('href', state.sound ? '#i-bell' : '#i-bell-off');
    if (state.sound) beep();
  });

  $('themeBtn').addEventListener('click', () => {
    const current = document.documentElement.dataset.theme;
    const prefersDark = matchMedia('(prefers-color-scheme: dark)').matches;
    const next = current === '' ? (prefersDark ? 'light' : 'dark')
               : current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('sniper-theme', next);
    applyTheme(next);
  });

  connect();
  renderKpis();
  renderFeed();
}

document.addEventListener('DOMContentLoaded', init);

/* Installation comme application (PWA). Sans service worker, Android ne
   propose pas « Ajouter à l'écran d'accueil ». */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* Contexte non sécurisé (http:// sur une IP distante) : l'installation
         PWA n'est pas proposée, mais le dashboard fonctionne normalement. */
    });
  });
}
