// ── Login guard ─────────────────────────────────────────────────────────────
// Shared by any action that requires an account (watchlist, thumbs feedback).
// Without this, a guest clicking these buttons previously got a silent
// failure (the request redirects to /login, but nothing told the user why
// nothing happened).
function requireLogin(message) {
  if (window.IS_LOGGED_IN) return true;
  showToast(message || 'Please log in to use this feature');
  setTimeout(() => {
    window.location.href = window.LOGIN_URL + '?next=' + encodeURIComponent(window.location.pathname);
  }, 900);
  return false;
}

// ── Watchlist (real per-user backend data, not localStorage) ────────────────
// Previously this lived entirely in localStorage under one single, unscoped
// key -- meaning it wasn't tied to any account, and two different users
// sharing one browser would see each other's saved movies. It's now backed
// by a per-user database table (see app/models/interactions.py).
let _wlCache = [];

async function fetchWatchlist() {
  if (!window.IS_LOGGED_IN) { _wlCache = []; return _wlCache; }
  try {
    const r = await fetch('/user/watchlist');
    if (!r.ok) { _wlCache = []; return _wlCache; }
    const data = await r.json();
    _wlCache = data.movies || [];
  } catch {
    _wlCache = [];
  }
  return _wlCache;
}

async function toggleWatchlist(id, title, poster) {
  if (!requireLogin('Please log in to save movies to your watchlist')) return;
  try {
    const r = await fetch('/user/watchlist/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: id }),
    });
    if (!r.ok) { showToast('Something went wrong, please try again'); return; }
    const data = await r.json();
    showToast(data.action === 'added' ? `Added "${title}" to watchlist` : `Removed "${title}" from watchlist`);
    await refreshWatchlistState();
  } catch {
    showToast('Network error, please try again');
  }
}

async function refreshWatchlistState() {
  await fetchWatchlist();
  updateWlBadge();
  syncWatchlistButtons();
  renderWatchlistDrawer();
}

function updateWlBadge() {
  const count = _wlCache.length;
  const badge = document.getElementById('navWlBadge');
  if (!badge) return;
  badge.textContent = count;
  badge.style.display = count > 0 ? 'inline-flex' : 'none';
}

function syncWatchlistButtons() {
  const ids = new Set(_wlCache.map(w => String(w.id)));
  document.querySelectorAll('.wl-btn').forEach(btn => {
    const id = btn.id?.replace('wl-', '');
    if (id) btn.classList.toggle('active', ids.has(String(id)));
  });
  // The big button on a movie's own detail page isn't part of the .wl-btn
  // grid-card set above (it has its own id and shows text, not just an icon).
  const detailBtn = document.getElementById('wlDetailBtn');
  if (detailBtn && detailBtn.dataset.movieId) {
    const inWl = ids.has(String(detailBtn.dataset.movieId));
    detailBtn.classList.toggle('active', inWl);
    detailBtn.textContent = inWl ? '🔖 In Watchlist' : '🔖 Add to Watchlist';
  }
}

function openWatchlist() {
  if (!requireLogin('Please log in to see your watchlist')) return;
  renderWatchlistDrawer();
  document.getElementById('drawerOverlay').classList.add('active');
}
function closeWatchlist() {
  document.getElementById('drawerOverlay').classList.remove('active');
}

function renderWatchlistDrawer() {
  const body = document.getElementById('watchlistBody');
  if (!body) return;
  if (!window.IS_LOGGED_IN) {
    body.innerHTML = '<p class="empty-msg">Log in to save movies to your watchlist.</p>';
    return;
  }
  if (!_wlCache.length) {
    body.innerHTML = '<p class="empty-msg">Your watchlist is empty.<br>Click 🔖 on any movie!</p>';
    return;
  }
  body.innerHTML = _wlCache.map(w => `
    <div class="wl-item">
      <img src="${w.poster_url || ''}" alt="${w.title}" onerror="this.style.display='none'">
      <div class="wl-item-info">
        <strong>${w.title}</strong>
        <a href="/movie/${w.id}">View →</a>
      </div>
      <button class="wl-remove" onclick="toggleWatchlist(${w.id},'${String(w.title).replace(/'/g, "\\'")}','')">✕</button>
    </div>
  `).join('');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  let t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── Flash auto-dismiss ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash').forEach(f => {
    setTimeout(() => { f.style.opacity='0'; setTimeout(()=>f.remove(),400); }, 4000);
  });
  refreshWatchlistState();
});

function togglePw(id, btn) {
    const input = document.getElementById(id);
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '🙈';
    } else {
        input.type = 'password';
        btn.textContent = '👁';
    }
}
