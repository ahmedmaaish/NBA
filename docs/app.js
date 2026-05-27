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

// ── 22bet market terminology — match what the user actually sees on 22bet ──
// 22bet calls "moneyline" -> "Team Wins" (with overtime)
//        and "spread"    -> "Handicap"
//        and "total"     -> "Total" (same)
// Always show users 22bet's exact labels so they can find the right market.
const MKT_22BET = {
  moneyline:        'Team Wins',
  spread:           'Handicap',
  over:             'Total — Over',
  under:            'Total — Under',
  total:            'Total',
  ht_total:         'Total 1st Half',
};
function marketLabel(internal) { return MKT_22BET[internal] || internal; }

// ── Series detection — find other games in DATA.games involving the same
// two teams within 14 days (typical playoff series window). Returns a list of
// {gameId, role, date} excluding the current game.
function findSeriesGames(g) {
  if (!DATA || !DATA.games) return [];
  const myDate  = new Date(g.date_utc);
  const myTeams = new Set([g.home.id, g.away.id]);
  const out = [];
  for (const other of DATA.games) {
    if (other.id === g.id) continue;
    const t2 = new Set([other.home.id, other.away.id]);
    if (t2.size !== 2) continue;
    // Same two team IDs?
    const overlap = [...myTeams].filter(x => t2.has(x)).length;
    if (overlap !== 2) continue;
    const d2 = new Date(other.date_utc);
    const diffDays = Math.abs((d2 - myDate) / 86400000);
    if (diffDays > 14) continue;
    out.push({
      id:      other.id,
      date:    other.date_utc,
      home:    other.home.name,
      away:    other.away.name,
      top:     other.top_signal,
      isLater: d2 > myDate,
    });
  }
  return out.sort((a, b) => new Date(a.date) - new Date(b.date));
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
// All times shown in local 24-hour format (e.g. "17:30")
function fmtGameTime(date_utc, state) {
  if (!date_utc) return '';
  const d = new Date(date_utc);
  if (isNaN(d)) return '';
  if (state === 'post') return 'Final';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtDate(date_utc) {
  if (!date_utc) return '';
  const d = new Date(date_utc);
  if (isNaN(d)) return '';
  // Compare on calendar-day basis using local zone
  const today = new Date(); today.setHours(0,0,0,0);
  const dDay  = new Date(d); dDay.setHours(0,0,0,0);
  const diff  = Math.round((dDay - today) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

// Returns "in 2h 35m" / "starts in 1d 4h" / "LIVE" / "Final" / "in 12m"
function fmtCountdown(date_utc, state) {
  if (state === 'post') return 'Final';
  if (state === 'in')   return 'LIVE NOW';
  if (!date_utc) return '';
  const d = new Date(date_utc);
  if (isNaN(d)) return '';
  let ms = d - new Date();
  if (ms <= 0)        return 'starting soon…';
  const days = Math.floor(ms / 86400000); ms -= days * 86400000;
  const hrs  = Math.floor(ms / 3600000);  ms -= hrs * 3600000;
  const mins = Math.floor(ms / 60000);
  if (days >= 1)  return `in ${days}d ${hrs}h ${mins}m`;
  if (hrs  >= 1)  return `in ${hrs}h ${mins}m`;
  if (mins >= 1)  return `in ${mins}m`;
  return 'starting soon…';
}

function sigChipColor(conf) {
  return { very_high: 'green', high: 'green', medium: 'amber', low: 'grey' }[conf] || 'grey';
}

// ── Upcoming games widget ────────────────────────────────────────────────────
function renderNextUp() {
  if (!DATA || !DATA.games) return;
  const upcoming = DATA.games
    .filter(g => g.state === 'pre' && g.date_utc)
    .sort((a, b) => new Date(a.date_utc) - new Date(b.date_utc));

  const wrap = $('next-up');
  const list = $('next-up-list');
  if (!wrap || !list) return;
  if (upcoming.length === 0) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';

  list.innerHTML = upcoming.map(g => {
    const h    = g.home || {};
    const a    = g.away || {};
    const top  = g.top_signal;
    const cd   = fmtCountdown(g.date_utc, g.state);
    const t24  = fmtGameTime(g.date_utc, g.state);
    const date = fmtDate(g.date_utc);
    const pickText = top
      ? `<span class="nu-pick ${top.realistic_edge ? 'pick-money' : 'pick-spread'}">→ ${top.bet === 'away' ? a.abbr : (top.bet === 'home' ? h.abbr : '?')} ${marketLabel(top.realistic_edge ? 'moneyline' : 'spread')}</span>`
      : '<span class="nu-pick pick-none">No signal</span>';
    return `
      <div class="nu-row" data-gid="${escHtml(g.id)}">
        <div class="nu-time">
          <div class="nu-when">${escHtml(date)} <strong>${escHtml(t24)}</strong></div>
          <div class="nu-cd"   data-date="${escHtml(g.date_utc)}">${escHtml(cd)}</div>
        </div>
        <div class="nu-match">
          <div class="nu-teams">${escHtml(a.abbr || '?')} @ ${escHtml(h.abbr || '?')}</div>
          ${pickText}
        </div>
      </div>`;
  }).join('');

  // Tap a row to open the same modal as on the card
  list.querySelectorAll('.nu-row').forEach(row => {
    row.addEventListener('click', () => {
      const gid = row.dataset.gid;
      const game = DATA.games.find(x => String(x.id) === String(gid));
      if (game) openModal(game);
    });
  });
}

// Live tick — updates countdowns every 30s and the wall-clock every second
function startLiveTicker() {
  // Wall clock (1s)
  const clockEl = $('next-up-clock');
  setInterval(() => {
    if (clockEl) {
      clockEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    }
  }, 1000);
  // Per-row countdown (30s — finer would just churn)
  setInterval(() => {
    document.querySelectorAll('.nu-cd').forEach(el => {
      const d = el.dataset.date;
      if (d) el.textContent = fmtCountdown(d, 'pre');
    });
    // Also re-render the on-card time labels so countdown there refreshes
    document.querySelectorAll('.gc-time-cd').forEach(el => {
      const d = el.dataset.date;
      if (d) el.textContent = fmtCountdown(d, el.dataset.state || 'pre');
    });
  }, 30000);
}

function renderScan() {
  renderNextUp();
  const status = $('scan-status');
  if (!DATA) { status.textContent = 'Loading...'; return; }

  const meta  = DATA.meta || {};
  const games = DATA.games || [];
  const updAt = DATA.updated_utc
    ? new Date(DATA.updated_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
    : '?';

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
    const seriesOther = findSeriesGames(g);
    const isSeries = seriesOther.length > 0;
    // Index in series (1-based)
    const seriesIndex = isSeries
      ? [...seriesOther, {date: g.date_utc, isCurrent: true}]
          .sort((x, y) => new Date(x.date) - new Date(y.date))
          .findIndex(x => x.isCurrent) + 1
      : 0;
    const seriesTotal = isSeries ? seriesOther.length + 1 : 0;

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

    // Odds (using 22bet's market names so it matches what the user sees there)
    let oddsHtml = '<span class="no-odds">22bet odds not available</span>';
    if (odds.found) {
      const ml  = odds.moneyline  || {};
      const sp  = odds.spread     || {};
      const tot = odds.total      || {};
      oddsHtml = `
        <div class="odds-box">
          <div class="odds-lbl">Team Wins</div>
          <div class="odds-val">${fmtOdd(ml.home)}</div>
          <div class="odds-sub">${escHtml(h.abbr || '')}</div>
        </div>
        <div class="odds-box">
          <div class="odds-lbl">Team Wins</div>
          <div class="odds-val">${fmtOdd(ml.away)}</div>
          <div class="odds-sub">${escHtml(a.abbr || '')}</div>
        </div>
        ${sp.found ? `
        <div class="odds-box">
          <div class="odds-lbl">Handicap</div>
          <div class="odds-val">${sp.line > 0 ? '+' : ''}${sp.line}</div>
          <div class="odds-sub">${fmtOdd(sp.home_odd)} / ${fmtOdd(sp.away_odd)}</div>
        </div>` : ''}
        ${tot.found ? `
        <div class="odds-box">
          <div class="odds-lbl">Total ${tot.line}</div>
          <div class="odds-val">O ${fmtOdd(tot.over)}</div>
          <div class="odds-sub">U ${fmtOdd(tot.under)}</div>
        </div>` : ''}`;
    }

    // Score line
    const scoreLine = (g.state === 'in' || g.state === 'post')
      ? `<div class="gc-score">${h.score ?? '--'} - ${a.score ?? '--'}</div>`
      : '';

    const seriesBadge = isSeries
      ? `<span class="series-badge">Game ${seriesIndex} of ${seriesTotal} in series</span>`
      : '';

    const leagueName = g.league || 'NBA';
    const leagueBadge = `<span class="league-badge league-${escHtml(leagueName.toLowerCase().replace(/\s+/g, '-'))}">${escHtml(leagueName)}</span>`;

    const cdStr = fmtCountdown(g.date_utc, g.state);
    const html = `
      <div class="game-card ${color}" data-gid="${escHtml(g.id)}">
        <div class="gc-top">
          <span class="${statusClass}">${escHtml(statusLabel)}</span>
          ${leagueBadge}
          <span class="gc-time">
            ${escHtml(fmtDate(g.date_utc))}
            <span class="gc-time-cd" data-date="${escHtml(g.date_utc)}" data-state="${escHtml(g.state)}">${escHtml(cdStr)}</span>
          </span>
        </div>
        ${seriesBadge}
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
  const top   = g.top_signal;
  const odds  = g.odds_22bet || {};
  const ml    = odds.moneyline || {};
  const sp    = odds.spread    || {};
  const tot   = odds.total     || {};

  // ── Build the "Recommended bet" card based on the top signal ──────────────
  let recHtml = '';
  if (top) {
    const isAway = top.bet === 'away';
    const isFav  = top.bet === 'favourite';
    let pickSide, pickTeamName, pickOdd, internalType, marketName, marketSubtext;

    if (isFav) {
      const hBetter = (h.r10_win_rate || 0) >= (a.r10_win_rate || 0);
      pickSide     = hBetter ? 'home' : 'away';
      pickTeamName = hBetter ? h.name : a.name;
    } else {
      pickSide     = isAway ? 'away' : 'home';
      pickTeamName = isAway ? a.name : h.name;
    }

    // Decide which 22bet market the user should click.
    // CRITICAL: an ATS (Against The Spread) safety check overrides the old
    // "realistic_edge" heuristic. A strategy with great outright WR but poor
    // ATS WR will silently lose money on handicap — we should always
    // recommend Team Wins (moneyline) for those.
    const atsSafe   = top.league_ats_safe;
    const atsWR     = top.league_ats_wr;
    const recCode   = top.bet_recommendation || (top.realistic_edge ? 'moneyline' : 'handicap');

    if (recCode === 'moneyline_only' || recCode === 'moneyline' ||
        (recCode === 'handicap_or_moneyline' && top.realistic_edge)) {
      internalType  = 'moneyline';
      marketName    = 'Team Wins';
      marketSubtext = '(pick this team to win — including overtime)';
      pickOdd       = pickSide === 'home' ? ml.home : ml.away;
    } else {
      // Recommend Handicap only when ATS data confirms it's safe.
      internalType  = 'spread';
      marketName    = 'Handicap';
      const lineStr = (sp.found && sp.line != null)
        ? (pickSide === 'home'
            ? `${sp.line > 0 ? '+' : ''}${sp.line}`
            : `${-sp.line > 0 ? '+' : ''}${(-sp.line)}`)
        : '';
      marketSubtext = lineStr ? `(pick this team to cover ${lineStr})` : '(point-spread bet)';
      pickOdd       = pickSide === 'home' ? sp.home_odd : sp.away_odd;
    }

    const oddStr      = pickOdd != null ? Number(pickOdd).toFixed(2) : 'check 22bet';
    const stakeAmt    = stake();
    const profitIfWin = pickOdd != null ? ((Number(pickOdd) - 1) * stakeAmt).toFixed(2) : '?';

    // 22bet usually shows team name without city for the inner team chips.
    // We give the full name in instructions to make it unambiguous.
    const homeShort = (h.name || '').split(' ').pop();
    const awayShort = (a.name || '').split(' ').pop();

    recHtml = `
      <div class="rec-card">
        <div class="rec-banner ${top.realistic_edge ? 'banner-money' : 'banner-spread'}">
          ${top.realistic_edge ? 'RECOMMENDED BET' : 'SPREAD / HANDICAP PLAY (heavy favourite)'}
        </div>
        <div class="rec-big">
          <div class="rec-pick">
            <div class="rec-pick-team">${escHtml(pickTeamName || '?')}</div>
            <div class="rec-pick-type">${marketName} <span style="color:#64748b;font-size:11px">${marketSubtext}</span></div>
          </div>
          <div class="rec-pick-odd">@ ${oddStr}</div>
        </div>
        <div class="rec-stake">
          Stake <strong>${fmtMoney(stakeAmt)}</strong> &middot; Win returns <strong>${fmtMoney(profitIfWin)}</strong> profit
        </div>
        <div class="rec-why">
          <strong>Why:</strong> ${escHtml(top.name)}<br>
          ${top.league_data_status === 'verified' ? `
            <span class="bt-verified">✓ Backtested in this league:</span>
            <strong>${top.league_win_rate}% outright WR</strong> / +${top.league_roi_pct}% ROI (${top.league_bets} bets)<br>
            <span style="color:#94a3b8;font-size:11px">NBA benchmark: ${top.win_rate}% WR / +${top.roi_pct}% ROI</span><br>
          ` : `
            <span class="bt-warning">⚠ NBA-backtested only:</span> ${top.win_rate}% WR / +${top.roi_pct}% ROI<br>
            <span style="color:#94a3b8;font-size:11px">Not yet validated on this specific league's data</span><br>
          `}
          ${atsWR != null ? `
            <span class="${atsSafe ? 'bt-verified' : 'bt-danger'}">
              ${atsSafe ? '✓' : '⚠'} Spread (Handicap) cover rate:
            </span>
            <strong>${atsWR}% ATS</strong>
            ${atsSafe
              ? '— safe to bet handicap'
              : '— LOSES money on handicap. Use Team Wins instead.'}<br>
          ` : ''}
          <span style="color:#cbd5e1;font-size:12px">${escHtml(top.note || '')}</span>
        </div>

        <div class="rec-steps">
          <strong>How to place this on 22bet:</strong>
          <ol>
            <li>In the 22bet app, go to <em>Basketball → NBA</em> and tap the match
                <strong>${escHtml(homeShort)} vs ${escHtml(awayShort)}</strong>
                (or <strong>${escHtml(awayShort)} vs ${escHtml(homeShort)}</strong> — same game)</li>
            <li>Make sure you're on the <em>"Including Overtime"</em> tab (the green one at the top of the markets list)</li>
            <li>Find the market called <strong style="color:#fbbf24">${marketName}</strong> in the list
                ${internalType === 'moneyline'
                  ? '(it shows just 2 options — home or away to win)'
                  : '(it shows many handicap lines — tap the down-arrow to expand)'}</li>
            ${internalType === 'spread' ? `
            <li>Inside <em>Handicap</em>, find the line near <strong>${sp.line != null ? (pickSide === 'home' ? (sp.line > 0 ? '+' : '') + sp.line : (-sp.line > 0 ? '+' : '') + (-sp.line)) : '?'}</strong>
                and tap the <strong>${escHtml(pickTeamName)}</strong> side of that row</li>` : ''}
            ${internalType === 'moneyline' ? `
            <li>Tap the <strong>${escHtml(pickTeamName)}</strong> box (you'll see the odd next to it)</li>` : ''}
            <li>Confirm the odd is around <strong>${oddStr}</strong> on your bet slip (within ±0.05 is fine — if it moved a lot, recheck the signal)</li>
            <li>Enter <strong>${fmtMoney(stakeAmt)}</strong> as your stake and tap <em>"Place bet"</em></li>
            <li>Come back here and tap <em>"✓ I placed this bet"</em> to log it</li>
          </ol>
        </div>

        <button class="primary rec-action" id="rb-quickbet"
                data-side="${pickSide}" data-type="${internalType}"
                data-odd="${pickOdd != null ? Number(pickOdd).toFixed(2) : ''}">
          ✓ I placed this bet ($${stakeAmt})
        </button>
        <button class="secondary rec-skip" id="rb-paperbet">
          Track as paper bet (no real money)
        </button>
      </div>`;
  } else {
    recHtml = `
      <div class="rec-card no-rec">
        <div class="rec-banner banner-none">NO BET RECOMMENDED</div>
        <p>No backtested strategy fires on this matchup. No clear edge — <strong>skip this game</strong>.</p>
        <p style="font-size:12px;color:#94a3b8">You can still manually record a bet below if you want to track a hunch as a paper bet.</p>
      </div>`;
  }

  // ── All-signals list (collapsed, for the curious) ─────────────────────────
  const sigsHtml = sigs.length
    ? sigs.map(s => `
        <div class="modal-row">
          <span class="modal-label">${escHtml(s.name)}</span>
          <span class="modal-val">${s.win_rate}% WR &middot; +${s.roi_pct}% ROI</span>
        </div>
        <div style="font-size:11px;color:#64748b;padding:2px 0 6px">${escHtml(s.note || '')}</div>`
      ).join('')
    : '';

  // ── Odds table (using 22bet's own market names) ──────────────────────────
  const oddsSection = odds.found ? `
    <div class="modal-section">
      <h4>22bet odds (uses 22bet's names)</h4>
      <div class="modal-row"><span class="modal-label">Team Wins — ${escHtml(h.name||'')} (Home)</span><span class="modal-val">${fmtOdd(ml.home)}</span></div>
      <div class="modal-row"><span class="modal-label">Team Wins — ${escHtml(a.name||'')} (Away)</span><span class="modal-val">${fmtOdd(ml.away)}</span></div>
      ${sp.found  ? `<div class="modal-row"><span class="modal-label">Handicap — ${escHtml(h.abbr||'')} ${sp.line > 0 ? '+' : ''}${sp.line} / ${escHtml(a.abbr||'')} ${-sp.line > 0 ? '+' : ''}${-sp.line}</span><span class="modal-val">${fmtOdd(sp.home_odd)} / ${fmtOdd(sp.away_odd)}</span></div>` : ''}
      ${tot.found ? `<div class="modal-row"><span class="modal-label">Total — Over / Under ${tot.line}</span><span class="modal-val">O ${fmtOdd(tot.over)} / U ${fmtOdd(tot.under)}</span></div>` : ''}
    </div>` : `<div class="modal-section"><div class="no-odds">22bet odds not yet available for this game (odds usually appear 24-48h before tipoff).</div></div>`;

  const betDisabled = g.state === 'post' ? 'disabled' : '';

  // ── Series-of-games warning (e.g. NBA Finals back-to-backs) ───────────────
  const seriesGames = findSeriesGames(g);
  let seriesHtml = '';
  if (seriesGames.length > 0) {
    // Determine current game number in the series
    const allDates = [...seriesGames, {date: g.date_utc, isCurrent: true}].sort(
      (a, b) => new Date(a.date) - new Date(b.date)
    );
    const myIndex = allDates.findIndex(x => x.isCurrent) + 1;
    const totalSeries = allDates.length;

    const sameTeamsHomeFlipped = seriesGames.some(s => {
      // If in the other game the same team that's home here is now AWAY, sides flipped
      return s.home !== h.name;
    });

    const otherList = seriesGames.map(s => {
      const sDate = new Date(s.date);
      const dStr = sDate.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
      const pick = s.top ? (s.top.bet === 'away' ? s.away : s.home) : null;
      return `<li>${dStr} — <strong>${escHtml(s.away.split(' ').pop())} @ ${escHtml(s.home.split(' ').pop())}</strong>${pick ? ` &middot; signal picks <em>${escHtml(pick)}</em>` : ' &middot; no signal'}</li>`;
    }).join('');

    seriesHtml = `
      <div class="series-warning">
        <div class="series-banner">⚠️ THIS IS GAME ${myIndex} OF A ${totalSeries}-GAME SERIES</div>
        <p style="font-size:13px;margin-bottom:8px">
          ${escHtml(h.name)} and ${escHtml(a.name)} play multiple times in the next 14 days.
          ${sameTeamsHomeFlipped ? `
          <strong>The home team flips between games</strong>, so the recommendation may flip too.
          That's normal — home court advantage in the NBA is statistically real (~58–60% home win rate over 30 years).` : ''}
        </p>
        <div style="font-size:12px;color:#94a3b8;margin-bottom:6px">Other games in this series:</div>
        <ul class="series-list">${otherList}</ul>
        <p style="font-size:12px;color:#cbd5e1;margin-top:8px;line-height:1.5">
          <strong style="color:#fbbf24">How to handle this:</strong>
          Each game is an <em>independent bet</em>. You can:<br>
          • Bet only this one (skip the others)<br>
          • Bet all of them (each one is a separate signal — they don't conflict)<br>
          • <strong>Or: skip the series entirely</strong> if you're not comfortable — when two top teams play repeatedly, the books price the lines tightly
        </p>
      </div>`;
  }

  const leagueLabel = g.league || 'NBA';
  $('modal-body').innerHTML = `
    <div class="modal-title">${escHtml(a.name||'?')} @ ${escHtml(h.name||'?')}</div>
    <div class="modal-subtitle">
      <span class="league-badge league-${escHtml(leagueLabel.toLowerCase().replace(/\s+/g, '-'))}">${escHtml(leagueLabel)}</span>
      &middot; ${escHtml(fmtDate(g.date_utc))} &middot; ${escHtml(fmtGameTime(g.date_utc, g.state))}
    </div>

    ${seriesHtml}

    ${recHtml}

    <div class="modal-section">
      <h4>Team form (last 10 games)</h4>
      <div class="modal-row">
        <span class="modal-label">${escHtml(h.name||'?')} (Home)</span>
        <span class="modal-val">${h.wins}-${h.losses} &middot; ${h.rest ?? '?'}d rest &middot; ${h.r10_win_rate != null ? Math.round(h.r10_win_rate*100)+'% WR' : '--'} &middot; ${h.r10_diff != null ? (h.r10_diff>0?'+':'')+h.r10_diff.toFixed(1)+'pt' : '--'}</span>
      </div>
      <div class="modal-row">
        <span class="modal-label">${escHtml(a.name||'?')} (Away)</span>
        <span class="modal-val">${a.wins}-${a.losses} &middot; ${a.rest ?? '?'}d rest &middot; ${a.r10_win_rate != null ? Math.round(a.r10_win_rate*100)+'% WR' : '--'} &middot; ${a.r10_diff != null ? (a.r10_diff>0?'+':'')+a.r10_diff.toFixed(1)+'pt' : '--'}</span>
      </div>
    </div>

    ${sigs.length > 1 ? `
    <details class="modal-section">
      <summary><h4 style="display:inline">All signals firing on this game (${sigs.length})</h4></summary>
      ${sigsHtml}
    </details>` : ''}

    ${oddsSection}

    <details class="modal-section">
      <summary><h4 style="display:inline">Custom bet (manual entry)</h4></summary>
      <div class="rec-form">
        <label>Bet on</label>
        <select id="rb-side">
          <option value="home">Home — ${escHtml(h.name||'?')}</option>
          <option value="away">Away — ${escHtml(a.name||'?')}</option>
        </select>
        <label>Market (as shown on 22bet)</label>
        <select id="rb-type">
          <option value="moneyline">Team Wins (Moneyline)</option>
          <option value="spread">Handicap (Spread)</option>
          <option value="over">Total — Over ${tot.line || '--'}</option>
          <option value="under">Total — Under ${tot.line || '--'}</option>
        </select>
        <label>Odds (decimal — what 22bet shows)</label>
        <input type="number" id="rb-odds" step="0.01" value="${fmtOdd(ml.home)}" min="1.01">
        <label>Stake ($)</label>
        <input type="number" id="rb-stake" step="0.5" value="${stake()}">
        <label>Book (Real or Paper)</label>
        <select id="rb-book">
          <option value="real">Real</option>
          <option value="paper">Paper</option>
        </select>
        <button class="primary" id="rb-submit" ${betDisabled}>Record Bet</button>
      </div>
    </details>`;

  // Quick-bet from the "I placed this bet" button (uses top signal's pick)
  function recordQuickBet(book) {
    const btn = $('rb-quickbet');
    if (!btn) return;
    const side  = btn.dataset.side;
    const type  = btn.dataset.type;
    const oddV  = parseFloat(btn.dataset.odd) || 1.90;
    const stakeV = stake();
    const teamName = side === 'home' ? h.name : a.name;

    const bet = {
      id:      Date.now().toString(),
      date:    todayKey(),
      match:   `${a.name} @ ${h.name}`,
      game_id: g.id,
      side,
      team:    teamName,
      type,
      odds:    oddV,
      stake:   stakeV,
      pnl_win: Math.round((oddV - 1) * stakeV * 100) / 100,
      outcome: 'pending',
      signals: (g.signals || []).map(s => s.id || s.name).join(', '),
    };
    if (book === 'paper') { PAPER.push(bet); saveBets('paper'); }
    else                  { BETS.push(bet);  saveBets('real');  }
    closeModal();
    renderHeader();
    renderBets();
    toast(`✓ Logged: ${teamName} ${marketLabel(type)} @ ${oddV.toFixed(2)} (${book})`);
  }

  if ($('rb-quickbet')) $('rb-quickbet').addEventListener('click', () => recordQuickBet('real'));
  if ($('rb-paperbet')) $('rb-paperbet').addEventListener('click', () => recordQuickBet('paper'));

  // Pre-fill odds when side/type changes (manual mode)
  function updateOddsField() {
    if (!$('rb-side')) return;
    const side = $('rb-side').value;
    const type = $('rb-type').value;
    let odd = null;
    if (type === 'moneyline') odd = side === 'home' ? ml.home : ml.away;
    else if (type === 'spread') odd = side === 'home' ? sp.home_odd : sp.away_odd;
    else if (type === 'over')  odd = tot.over;
    else if (type === 'under') odd = tot.under;
    if (odd != null) $('rb-odds').value = Number(odd).toFixed(2);
  }
  if ($('rb-side')) $('rb-side').addEventListener('change', updateOddsField);
  if ($('rb-type')) $('rb-type').addEventListener('change', updateOddsField);

  if ($('rb-submit')) $('rb-submit').addEventListener('click', () => {
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
      signals: (g.signals || []).map(s => s.name).join(', '),
    };
    if (book === 'paper') { PAPER.push(bet); saveBets('paper'); }
    else                  { BETS.push(bet);  saveBets('real');  }
    closeModal();
    renderHeader();
    renderBets();
    toast(`Recorded: ${teamName} ${marketLabel(type)} @ ${oddsV} (${book})`);
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
        ${escHtml(b.team || '?')} &middot; ${escHtml(marketLabel(b.type) || 'Team Wins')} &middot;
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
  if (name === 'strat')    renderExamples();
}

// ── Historical examples (shown on Strategies tab) ───────────────────────────
let EXAMPLES = null;
async function renderExamples() {
  if (!EXAMPLES) {
    try {
      const r = await fetch('data/examples.json?t=' + Date.now());
      if (r.ok) EXAMPLES = await r.json();
    } catch (e) {}
  }
  if (!EXAMPLES || !EXAMPLES.examples?.length) return;
  const sec = $('examples-section');
  const list = $('examples-list');
  sec.style.display = 'block';
  list.innerHTML = EXAMPLES.examples.slice(0, 8).map(e => `
    <div class="ex-card">
      <div class="ex-top">
        <span class="ex-date">${e.date}</span>
        <span class="ex-result ${e.result === 'WON' ? 'ex-won' : 'ex-lost'}">${e.result === 'WON' ? '✓ WON' : '✗ LOST'}</span>
      </div>
      <div class="ex-matchup">${escHtml(e.matchup)}</div>
      <div class="ex-detail">
        Final: <strong>${e.final_score}</strong> &middot;
        Signal: <strong>${escHtml(e.strategy_id)} ${escHtml(e.strategy_name)}</strong>
      </div>
      <div class="ex-bet">
        Bet: <strong>${e.bet_side === 'home' ? e.h_team : e.a_team}</strong> (${e.bet_side})
      </div>
    </div>
  `).join('');
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
  startLiveTicker();
  refresh().then(scheduleAutoRefresh);
});
