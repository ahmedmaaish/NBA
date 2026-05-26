'use strict';

// ── Storage keys ────────────────────────────────────────────────────────────
const S = {
  settings: 'nba_settings',
  bets:     'nba_bets',
  paper:    'nba_paper_bets',
};

// ── Default settings ─────────────────────────────────────────────────────────
const DEFAULTS = {
  bankroll:    500,
  stake_pct:   0.02,
  stake_min:   2,
  profit_cap:  0.20,
  loss_cap:    0.10,
};

// ── State ────────────────────────────────────────────────────────────────────
let DATA      = null;   // signals.json payload
let SETTINGS  = loadSettings();
let BETS      = loadBets('real');
let PAPER     = loadBets('paper');
let CUR_BOOK  = 'real';
let AUTO_ON   = true;
let REFRESH_T = null;
let LAST_FETCH = 0;

// ── Helpers ──────────────────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function fmtMoney(x) { return '$' + (Number(x) || 0).toFixed(2); }
function fmtPct(x, signed = true) {
  const v = (Number(x) || 0) * 100;
  const s = signed && v > 0 ? '+' : '';
  return s + v.toFixed(1) + '%';
}
function fmtOdd(x) { return x != null ? Number(x).toFixed(2) : '--'; }
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tmr);
  t._tmr = setTimeout(() => t.classList.remove('show'), 2800);
}
function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function loadSettings() {
  try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(S.settings) || '{}')); }
  catch { return Object.assign({}, DEFAULTS); }
}
function saveSettings() { localStorage.setItem(S.settings, JSON.stringify(SETTINGS)); }

function loadBets(book) {
  try { return JSON.parse(localStorage.getItem(S[book === 'paper' ? 'paper' : 'bets']) || '[]'); }
  catch { return []; }
}
function saveBets(book) {
  localStorage.setItem(book === 'paper' ? S.paper : S.bets,
                       JSON.stringify(book === 'paper' ? PAPER : BETS));
}

function stake() {
  const s = SETTINGS.bankroll * SETTINGS.stake_pct;
  return Math.max(SETTINGS.stake_min, Math.round(s * 100) / 100);
}

// ── Fetch signals.json ───────────────────────────────────────────────────────
async function fetchData() {
  try {
    const r = await fetch('data/signals.json?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    DATA = await r.json();
    LAST_FETCH = Date.now();
  } catch (e) {
    console.warn('fetch failed:', e);
  }
}

// ── Daily P&L ────────────────────────────────────────────────────────────────
function todayKey() { return new Date().toISOString().slice(0, 10); }

function dayPL(bets) {
  const today = todayKey();
  return bets
    .filter(b => b.date === today)
    .reduce((sum, b) => {
      if (b.outcome === 'win')  return sum + Number(b.pnl_win  || 0);
      if (b.outcome === 'loss') return sum - Number(b.stake    || 0);
      return sum;
    }, 0);
}

// ── Render: header ────────────────────────────────────────────────────────────
function renderHeader() {
  const pl = dayPL(BETS);
  const br = SETTINGS.bankroll;
  const plPct = br > 0 ? pl / br : 0;

  $('mm-bankroll').textContent = fmtMoney(br);
  const today = $('mm-today');
  today.textContent = fmtMoney(pl) + ' (' + fmtPct(plPct) + ')';
  today.style.color = pl > 0 ? '#4ade80' : pl < 0 ? '#f87171' : '#e2e8f0';
  $('mm-stake').textContent = fmtMoney(stake());

  const caps = $('mm-caps');
  const room_p = (SETTINGS.profit_cap - plPct) * 100;
  const room_l = (SETTINGS.loss_cap   + plPct) * 100;
  if (plPct >= SETTINGS.profit_cap) {
    caps.className = 'cap-warn';
    caps.textContent = 'DAILY PROFIT CAP HIT — STOP BETTING TODAY';
  } else if (plPct <= -SETTINGS.loss_cap) {
    caps.className = 'cap-warn';
    caps.textContent = 'DAILY LOSS CAP HIT — STOP BETTING TODAY';
  } else {
    caps.className = '';
    caps.textContent = `Room: +${room_p.toFixed(1)}% / -${room_l.toFixed(1)}% before daily stop`;
  }
}

// ── Render: scan tab ──────────────────────────────────────────────────────────
function fmtGameTime(date_utc, state) {
  if (!date_utc) return '';
  const d = new Date(date_utc);
  if (isNaN(d)) return '';
  if (state === 'post') return 'Final';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtDate(date_utc) {
  if (!date_utc) return '';
  const d = new Date(date_utc);
  if (isNaN(d)) return '';
  const today = new Date();
  const diff  = Math.round((d - today) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

function sigChipColor(conf) {
  return { very_high: 'green', high: 'green', medium: 'amber', low: 'grey' }[conf] || 'grey';
}

function renderScan() {
  const status = $('scan-status');
  if (!DATA) { status.textContent = 'Loading...'; return; }

  const meta  = DATA.meta || {};
  const games = DATA.games || [];
  const updAt = DATA.updated_utc ? new Date(DATA.updated_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '?';

  status.textContent =
    `${games.length} games  |  ${meta.total_signals || 0} signals  |  ` +
    `22bet: ${meta.bet22_events || 0} events  |  Updated ${updAt}  |  ` +
    (meta.season || '');

  const list  = $('game-list');
  const empty = $('empty-state');
  list.innerHTML = '';

  if (!games.length) {
    empty.style.display = 'block';
    empty.textContent   = 'No NBA games in the next 3 days.\n\nCheck back during the regular season (Oct–Apr) or playoffs (Apr–Jun).';
    return;
  }
  empty.style.display = 'none';

  for (const g of games) {
    const sigs  = g.signals || [];
    const top   = g.top_signal;
    const odds  = g.odds_22bet || {};
    const color = g.card_color || 'grey';
    const h     = g.home || {};
    const a     = g.away || {};
    const isLive = g.state === 'in';

    // Status chip
    const statusLabel = isLive
      ? `LIVE Q${g.period || '?'} ${g.clock || ''}`
      : g.state === 'post'
        ? 'Final'
        : fmtGameTime(g.date_utc, g.state);
    const statusClass = isLive ? 'gc-status live' : 'gc-status';

    // Rest indicators
    const hRestWarn = h.rest === 1 ? ' rest-warn' : '';
    const aRestWarn = a.rest === 1 ? ' rest-warn' : '';

    // Signal chips
    const sigHtml = sigs.length
      ? sigs.slice(0, 3).map(s =>
          `<span class="sig-chip ${sigChipColor(s.confidence)}">${escHtml(s.name)}</span>`
        ).join('')
      : '<span class="sig-chip grey">No signal</span>';

    // Odds
    let oddsHtml = '<span class="no-odds">22bet odds not available</span>';
    if (odds.found) {
      const ml  = odds.moneyline  || {};
      const sp  = odds.spread     || {};
      const tot = odds.total      || {};
      oddsHtml = `
        <div class="odds-box">
          <div class="odds-lbl">Home ML</div>
          <div class="odds-val">${fmtOdd(ml.home)}</div>
          <div class="odds-sub">${escHtml(h.abbr || '')}</div>
        </div>
        <div class="odds-box">
          <div class="odds-lbl">Away ML</div>
          <div class="odds-val">${fmtOdd(ml.away)}</div>
          <div class="odds-sub">${escHtml(a.abbr || '')}</div>
        </div>
        ${sp.found ? `
        <div class="odds-box">
          <div class="odds-lbl">Spread</div>
          <div class="odds-val">${sp.line > 0 ? '+' : ''}${sp.line}</div>
          <div class="odds-sub">${fmtOdd(sp.home_odd)} / ${fmtOdd(sp.away_odd)}</div>
        </div>` : ''}
        ${tot.found ? `
        <div class="odds-box">
          <div class="odds-lbl">O/U ${tot.line}</div>
          <div class="odds-val">O ${fmtOdd(tot.over)}</div>
          <div class="odds-sub">U ${fmtOdd(tot.under)}</div>
        </div>` : ''}`;
    }

    // Score line
    const scoreLine = (g.state === 'in' || g.state === 'post')
      ? `<div class="gc-score">${h.score ?? '--'} - ${a.score ?? '--'}</div>`
      : '';

    const html = `
      <div class="game-card ${color}" data-gid="${escHtml(g.id)}">
        <div class="gc-top">
          <span class="${statusClass}">${escHtml(statusLabel)}</span>
          <span class="gc-time">${escHtml(fmtDate(g.date_utc))}</span>
        </div>
        <div class="gc-matchup">${escHtml(a.name || '?')} @ ${escHtml(h.name || '?')}</div>
        ${scoreLine}
        <div class="gc-stats">
          <span class="gc-stat${hRestWarn}">${escHtml(h.abbr||'?')}: ${h.rest ?? '?'}d rest, ${h.r10_win_rate != null ? Math.round(h.r10_win_rate*100) + '% WR' : '--'}, ${h.r10_diff != null ? (h.r10_diff > 0 ? '+' : '') + h.r10_diff.toFixed(1) + 'pt' : '--'}</span>
          <span class="gc-stat${aRestWarn}">${escHtml(a.abbr||'?')}: ${a.rest ?? '?'}d rest, ${a.r10_win_rate != null ? Math.round(a.r10_win_rate*100) + '% WR' : '--'}, ${a.r10_diff != null ? (a.r10_diff > 0 ? '+' : '') + a.r10_diff.toFixed(1) + 'pt' : '--'}</span>
          <span class="gc-stat">${escHtml(h.abbr||'?')} ${h.wins ?? 0}-${h.losses ?? 0} / ${escHtml(a.abbr||'?')} ${a.wins ?? 0}-${a.losses ?? 0}</span>
        </div>
        <div class="gc-signals">${sigHtml}</div>
        <div class="gc-odds">${oddsHtml}</div>
      </div>`;

    const div = document.createElement('div');
    div.innerHTML = html;
    const card = div.firstElementChild;
    card.addEventListener('click', () => openModal(g));
    list.appendChild(card);
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(g) {
  const h     = g.home || {};
  const a     = g.away || {};
  const sigs  = g.signals || [];
  const odds  = g.odds_22bet || {};
  const ml    = odds.moneyline || {};
  const sp    = odds.spread    || {};
  const tot   = odds.total     || {};

  const sigsHtml = sigs.length
    ? sigs.map(s => `
        <div class="modal-row">
          <span class="modal-label">${escHtml(s.name)}</span>
          <span class="modal-val">${s.win_rate}% WR &middot; +${s.roi_pct}% ROI</span>
        </div>
        <div style="font-size:11px;color:#64748b;padding:2px 0 6px">${escHtml(s.note || '')}</div>`
      ).join('')
    : '<div style="color:#64748b;font-size:13px">No strategy signal for this game.</div>';

  const oddsSection = odds.found ? `
    <div class="modal-section">
      <h4>22bet Odds</h4>
      <div class="modal-row"><span class="modal-label">Home ML (${escHtml(h.abbr||'')})</span><span class="modal-val">${fmtOdd(ml.home)}</span></div>
      <div class="modal-row"><span class="modal-label">Away ML (${escHtml(a.abbr||'')})</span><span class="modal-val">${fmtOdd(ml.away)}</span></div>
      ${sp.found  ? `<div class="modal-row"><span class="modal-label">Spread (home ${sp.line > 0 ? '+' : ''}${sp.line})</span><span class="modal-val">${fmtOdd(sp.home_odd)} / ${fmtOdd(sp.away_odd)}</span></div>` : ''}
      ${tot.found ? `<div class="modal-row"><span class="modal-label">Total O/U ${tot.line}</span><span class="modal-val">O ${fmtOdd(tot.over)} / U ${fmtOdd(tot.under)}</span></div>` : ''}
    </div>` : `<div class="modal-section"><div class="no-odds">No 22bet odds found for this game.</div></div>`;

  const betDisabled = g.state === 'post' ? 'disabled' : '';

  $('modal-body').innerHTML = `
    <div class="modal-title">${escHtml(a.name||'?')} @ ${escHtml(h.name||'?')}</div>

    <div class="modal-section">
      <h4>Team Stats (last 10 games)</h4>
      <div class="modal-row">
        <span class="modal-label">${escHtml(h.name||'?')} (Home)</span>
        <span class="modal-val">${h.wins}-${h.losses} &middot; ${h.rest ?? '?'}d rest &middot; ${h.r10_win_rate != null ? Math.round(h.r10_win_rate*100)+'% WR' : '--'} &middot; ${h.r10_diff != null ? (h.r10_diff>0?'+':'')+h.r10_diff.toFixed(1)+'pt' : '--'}</span>
      </div>
      <div class="modal-row">
        <span class="modal-label">${escHtml(a.name||'?')} (Away)</span>
        <span class="modal-val">${a.wins}-${a.losses} &middot; ${a.rest ?? '?'}d rest &middot; ${a.r10_win_rate != null ? Math.round(a.r10_win_rate*100)+'% WR' : '--'} &middot; ${a.r10_diff != null ? (a.r10_diff>0?'+':'')+a.r10_diff.toFixed(1)+'pt' : '--'}</span>
      </div>
    </div>

    <div class="modal-section">
      <h4>Strategy Signals</h4>
      ${sigsHtml}
    </div>

    ${oddsSection}

    <div class="rec-form modal-section">
      <h4>Record a Bet</h4>
      <label>Bet on</label>
      <select id="rb-side">
        <option value="home">Home — ${escHtml(h.name||'?')}</option>
        <option value="away">Away — ${escHtml(a.name||'?')}</option>
      </select>
      <label>Bet type</label>
      <select id="rb-type">
        <option value="moneyline">Moneyline</option>
        <option value="spread">Spread</option>
        <option value="over">Over ${tot.line || '--'}</option>
        <option value="under">Under ${tot.line || '--'}</option>
      </select>
      <label>Odds (decimal)</label>
      <input type="number" id="rb-odds" step="0.01" value="${fmtOdd(ml.home)}" min="1.01">
      <label>Stake ($)</label>
      <input type="number" id="rb-stake" step="0.5" value="${stake()}">
      <label>Book (Real or Paper)</label>
      <select id="rb-book">
        <option value="real">Real</option>
        <option value="paper">Paper</option>
      </select>
      <button class="primary" id="rb-submit" ${betDisabled}>Record Bet</button>
    </div>`;

  // Pre-fill odds when side/type changes
  function updateOddsField() {
    const side = $('rb-side').value;
    const type = $('rb-type').value;
    let odd = null;
    if (type === 'moneyline') odd = side === 'home' ? ml.home : ml.away;
    else if (type === 'spread') odd = side === 'home' ? sp.home_odd : sp.away_odd;
    else if (type === 'over')  odd = tot.over;
    else if (type === 'under') odd = tot.under;
    if (odd != null) $('rb-odds').value = Number(odd).toFixed(2);
  }
  $('rb-side').addEventListener('change', updateOddsField);
  $('rb-type').addEventListener('change', updateOddsField);

  $('rb-submit').addEventListener('click', () => {
    const side   = $('rb-side').value;
    const type   = $('rb-type').value;
    const oddsV  = parseFloat($('rb-odds').value) || 1.90;
    const stakeV = parseFloat($('rb-stake').value) || stake();
    const book   = $('rb-book').value;

    const teamName = side === 'home' ? h.name : a.name;
    const bet = {
      id:      Date.now().toString(),
      date:    todayKey(),
      match:   `${a.name} @ ${h.name}`,
      game_id: g.id,
      side,
      team:    teamName,
      type,
      odds:    oddsV,
      stake:   stakeV,
      pnl_win: Math.round((oddsV - 1) * stakeV * 100) / 100,
      outcome: 'pending',
      signals: (g.signals || []).map(s => s.id).join(', '),
    };

    if (book === 'paper') {
      PAPER.push(bet);
      saveBets('paper');
    } else {
      BETS.push(bet);
      saveBets('real');
    }
    closeModal();
    renderHeader();
    renderBets();
    toast(`Recorded: ${teamName} ${type} @ ${oddsV} (${book})`);
  });

  $('modal').style.display = 'flex';
}

function closeModal() {
  $('modal').style.display = 'none';
}

// ── Render: bets tab ──────────────────────────────────────────────────────────
function renderBets() {
  const list = $('bet-list');
  const bets = CUR_BOOK === 'paper' ? PAPER : BETS;
  list.innerHTML = '';

  if (!bets.length) {
    list.innerHTML = '<div class="no-bets">No bets recorded yet.</div>';
    return;
  }

  // Newest first
  for (const b of [...bets].reverse()) {
    const oc  = b.outcome || 'pending';
    const cls = oc === 'win' ? 'win' : oc === 'loss' ? 'loss' : 'pend';
    const pnl = oc === 'win'
      ? '+' + fmtMoney(b.pnl_win)
      : oc === 'loss'
        ? '-' + fmtMoney(b.stake)
        : 'Pending';

    const actBtns = oc === 'pending' ? `
      <button class="btn-win"  data-bid="${b.id}" data-book="${CUR_BOOK}" data-oc="win">Won</button>
      <button class="btn-loss" data-bid="${b.id}" data-book="${CUR_BOOK}" data-oc="loss">Lost</button>` : '';

    const card = document.createElement('div');
    card.className = 'bet-card';
    card.innerHTML = `
      <div class="bet-top">
        <span class="bet-match">${escHtml(b.match || '?')}</span>
        <span class="bet-outcome ${cls}">${pnl}</span>
      </div>
      <div class="bet-meta">
        ${escHtml(b.team || '?')} &middot; ${escHtml(b.type || 'ML')} &middot;
        @ ${b.odds} &middot; Stake: ${fmtMoney(b.stake)} &middot; ${b.date || ''}
        ${b.signals ? ' &middot; ' + escHtml(b.signals) : ''}
      </div>
      <div class="bet-actions">${actBtns}</div>`;

    list.appendChild(card);
  }

  // Outcome button handlers
  list.querySelectorAll('.btn-win, .btn-loss').forEach(btn => {
    btn.addEventListener('click', e => {
      const { bid, book, oc } = e.currentTarget.dataset;
      const arr = book === 'paper' ? PAPER : BETS;
      const bet = arr.find(b => b.id === bid);
      if (bet) { bet.outcome = oc; saveBets(book); renderHeader(); renderBets(); }
    });
  });
}

// ── Render: settings tab ──────────────────────────────────────────────────────
function renderSettings() {
  $('set-bankroll').value   = SETTINGS.bankroll;
  $('set-stake_pct').value  = SETTINGS.stake_pct;
  $('set-stake_min').value  = SETTINGS.stake_min;
  $('set-profit_cap').value = SETTINGS.profit_cap;
  $('set-loss_cap').value   = SETTINGS.loss_cap;
  updateStakePreview();
}

function updateStakePreview() {
  const br  = parseFloat($('set-bankroll').value)  || 0;
  const pct = parseFloat($('set-stake_pct').value) || 0;
  const min = parseFloat($('set-stake_min').value) || 0;
  const s   = Math.max(min, Math.round(br * pct * 100) / 100);
  $('stake-preview').textContent = `Stake per bet: $${s.toFixed(2)}`;
}

// ── Full refresh ──────────────────────────────────────────────────────────────
async function refresh() {
  $('scan-btn').textContent = 'Refreshing...';
  $('scan-btn').disabled    = true;
  await fetchData();
  renderHeader();
  renderScan();
  $('scan-btn').textContent = 'Refresh';
  $('scan-btn').disabled    = false;
}

function scheduleAutoRefresh() {
  clearTimeout(REFRESH_T);
  if (!AUTO_ON) return;
  // Refresh every 60 min (data updates hourly via Actions)
  REFRESH_T = setTimeout(() => { refresh().then(scheduleAutoRefresh); }, 60 * 60 * 1000);
}

// ── Nav ───────────────────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  $('tab-' + name).classList.add('active');
  document.querySelector(`.nav-btn[data-tab="${name}"]`).classList.add('active');
  if (name === 'bets')     renderBets();
  if (name === 'settings') renderSettings();
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // Nav
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  // Bet book tabs
  document.querySelectorAll('.bets-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.bets-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      CUR_BOOK = btn.dataset.book;
      renderBets();
    });
  });

  // Modal close
  $('modal-close').addEventListener('click', closeModal);
  $('modal').addEventListener('click', e => { if (e.target === $('modal')) closeModal(); });

  // Scan / auto toggle
  $('scan-btn').addEventListener('click', () => refresh().then(scheduleAutoRefresh));
  $('auto-toggle').addEventListener('change', e => {
    AUTO_ON = e.target.checked;
    scheduleAutoRefresh();
  });

  // Settings
  ['set-bankroll','set-stake_pct','set-stake_min'].forEach(id => {
    $(id).addEventListener('input', updateStakePreview);
  });
  $('save-settings').addEventListener('click', () => {
    SETTINGS.bankroll    = parseFloat($('set-bankroll').value)  || DEFAULTS.bankroll;
    SETTINGS.stake_pct   = parseFloat($('set-stake_pct').value) || DEFAULTS.stake_pct;
    SETTINGS.stake_min   = parseFloat($('set-stake_min').value) || DEFAULTS.stake_min;
    SETTINGS.profit_cap  = parseFloat($('set-profit_cap').value)|| DEFAULTS.profit_cap;
    SETTINGS.loss_cap    = parseFloat($('set-loss_cap').value)  || DEFAULTS.loss_cap;
    saveSettings();
    renderHeader();
    toast('Settings saved');
  });

  // Initial load
  renderHeader();
  refresh().then(scheduleAutoRefresh);
});
