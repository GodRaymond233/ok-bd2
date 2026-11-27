const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const VERSION_RE = /^\d+\.\d+\.\d+(?:[.-][0-9A-Za-z]+)*$/;
const SHELL_VER_RE = /^[0-9A-Za-z._-]{0,32}$/;

const VALID_EVENTS = new Set(["install", "upgrade", "active"]);
const VALID_UPGRADE_VIA = new Set([
  "none",
  "new_install",
  "legacy_migration",
  "hot_update",
  "installer_upgrade",
  "rollback",
]);
const VALID_CHANNELS = new Set(["china", "global", "unknown"]);
const VALID_OS = new Set(["win10", "win11", "win", "unknown"]);

const MAX_BODY_BYTES = 2048;
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX_REQUESTS = 20;

// In-memory per-isolate rate limiter; client IPs are never written to D1.
const rateBuckets = new Map();

export function resetRateLimiter() {
  rateBuckets.clear();
}

export function checkRateLimit(key, nowMs = Date.now()) {
  if (!key) return true;
  const bucket = rateBuckets.get(key);
  if (!bucket || nowMs >= bucket.resetAt) {
    if (rateBuckets.size > 5000) {
      for (const [k, v] of rateBuckets) {
        if (nowMs >= v.resetAt) rateBuckets.delete(k);
      }
    }
    rateBuckets.set(key, { count: 1, resetAt: nowMs + RATE_LIMIT_WINDOW_MS });
    return true;
  }
  if (bucket.count >= RATE_LIMIT_MAX_REQUESTS) {
    return false;
  }
  bucket.count += 1;
  return true;
}

export function beijingDate(now = new Date(), offsetDays = 0) {
  const shifted = new Date(now.getTime() + (8 * 60 + offsetDays * 1440) * 60_000);
  return shifted.toISOString().slice(0, 10);
}

export function validatePayload(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "Payload must be a JSON object." };
  }
  const cid = typeof raw.cid === "string" ? raw.cid.trim().toLowerCase() : "";
  if (!UUID_V4_RE.test(cid)) {
    return { ok: false, error: "Invalid cid (expected UUIDv4)." };
  }
  const event = typeof raw.event === "string" ? raw.event.trim() : "";
  if (!VALID_EVENTS.has(event)) {
    return { ok: false, error: "Invalid event." };
  }
  const ver = typeof raw.ver === "string" ? raw.ver.trim().replace(/^v/i, "") : "";
  if (!ver || ver.length > 32 || !VERSION_RE.test(ver)) {
    return { ok: false, error: "Invalid ver." };
  }
  const prevVerRaw = typeof raw.prev_ver === "string" ? raw.prev_ver.trim().replace(/^v/i, "") : "";
  if (prevVerRaw && (prevVerRaw.length > 32 || !VERSION_RE.test(prevVerRaw))) {
    return { ok: false, error: "Invalid prev_ver." };
  }
  const upgradeVia = typeof raw.upgrade_via === "string" ? raw.upgrade_via.trim() : "none";
  if (!VALID_UPGRADE_VIA.has(upgradeVia)) {
    return { ok: false, error: "Invalid upgrade_via." };
  }
  const channel = typeof raw.channel === "string" ? raw.channel.trim().toLowerCase() : "unknown";
  if (!VALID_CHANNELS.has(channel)) {
    return { ok: false, error: "Invalid channel." };
  }
  const os = typeof raw.os === "string" ? raw.os.trim().toLowerCase() : "unknown";
  if (!VALID_OS.has(os)) {
    return { ok: false, error: "Invalid os." };
  }
  const shellVer = typeof raw.shell_ver === "string" ? raw.shell_ver.trim() : "";
  if (!SHELL_VER_RE.test(shellVer)) {
    return { ok: false, error: "Invalid shell_ver." };
  }
  return {
    ok: true,
    data: {
      cid,
      event,
      ver,
      prev_ver: prevVerRaw,
      upgrade_via: upgradeVia,
      channel,
      os,
      shell_ver: shellVer,
    },
  };
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const enc = new TextEncoder();
  const ba = enc.encode(a);
  const bb = enc.encode(b);
  if (ba.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ba.length; i += 1) {
    diff |= ba[i] ^ bb[i];
  }
  return diff === 0;
}

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extraHeaders,
    },
  });
}

function isAuthorized(request, env) {
  const token = typeof env.ADMIN_TOKEN === "string" ? env.ADMIN_TOKEN.trim() : "";
  if (!token) return false;
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  if (!match) return false;
  return constantTimeEqual(match[1].trim(), token);
}

async function handlePing(request, env, now = new Date()) {
  const clientIp =
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "";
  if (!checkRateLimit(clientIp, now.getTime())) {
    return jsonResponse({ error: "Rate limit exceeded." }, 429);
  }

  const contentLength = Number(request.headers.get("content-length") || "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_BODY_BYTES) {
    return jsonResponse({ error: "Payload too large." }, 413);
  }

  const rawText = await request.text();
  if (rawText.length > MAX_BODY_BYTES) {
    return jsonResponse({ error: "Payload too large." }, 413);
  }

  let parsed;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    return jsonResponse({ error: "Invalid JSON." }, 400);
  }

  const checked = validatePayload(parsed);
  if (!checked.ok) {
    return jsonResponse({ error: checked.error }, 400);
  }

  const d = checked.data;
  const day = beijingDate(now, 0);
  const isoNow = now.toISOString();
  const firstVia = d.event === "install" ? d.upgrade_via : "unknown";

  const upsertUser = env.DB.prepare(
    `INSERT INTO users (
       cid, first_seen_date, last_seen_date, current_ver, channel, first_via, os, shell_ver, updated_at
     ) VALUES (?1, ?2, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
     ON CONFLICT(cid) DO UPDATE SET
       last_seen_date = CASE WHEN excluded.last_seen_date > users.last_seen_date THEN excluded.last_seen_date ELSE users.last_seen_date END,
       current_ver = excluded.current_ver,
       channel = CASE WHEN excluded.channel != 'unknown' THEN excluded.channel ELSE users.channel END,
       os = CASE WHEN excluded.os != 'unknown' THEN excluded.os ELSE users.os END,
       shell_ver = CASE WHEN excluded.shell_ver != '' THEN excluded.shell_ver ELSE users.shell_ver END,
       updated_at = excluded.updated_at`
  ).bind(d.cid, day, d.ver, d.channel, firstVia, d.os, d.shell_ver, isoNow);

  const insertEvent = env.DB.prepare(
    `INSERT OR IGNORE INTO daily_events (
       day, cid, event, ver, prev_ver, upgrade_via, channel, shell_ver, os, created_at
     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)`
  ).bind(day, d.cid, d.event, d.ver, d.prev_ver, d.upgrade_via, d.channel, d.shell_ver, d.os, isoNow);

  await env.DB.batch([upsertUser, insertEvent]);
  return new Response(null, { status: 204, headers: { "cache-control": "no-store" } });
}

async function handleStats(request, env, now = new Date()) {
  if (!isAuthorized(request, env)) {
    return jsonResponse({ error: "Unauthorized." }, 401);
  }

  const url = new URL(request.url);
  const rawDays = Number.parseInt(url.searchParams.get("days") || "30", 10);
  const days = Number.isFinite(rawDays) ? Math.min(Math.max(rawDays, 1), 365) : 30;

  const today = beijingDate(now, 0);
  const wauStart = beijingDate(now, -6);
  const mauStart = beijingDate(now, -29);
  const windowStart = beijingDate(now, -(days - 1));

  const [overviewRes, versionsRes, matrixRes, channelsRes, trendRes] = await env.DB.batch([
    env.DB.prepare(
      `SELECT
         (SELECT COUNT(*) FROM users) AS total_users,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day = ?1) AS dau,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day >= ?2) AS wau,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day >= ?3) AS mau,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day = ?1 AND event = 'install' AND upgrade_via = 'new_install') AS today_new_installs,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day = ?1 AND event = 'upgrade' AND upgrade_via = 'hot_update') AS today_hot_updates,
         (SELECT COUNT(DISTINCT cid) FROM daily_events WHERE day = ?1 AND event = 'upgrade' AND upgrade_via = 'rollback') AS today_rollbacks`
    ).bind(today, wauStart, mauStart),
    env.DB.prepare(
      `SELECT
         e.ver AS ver,
         COUNT(DISTINCT e.cid) AS total_users_reached,
         COUNT(DISTINCT CASE WHEN e.day >= ?1 THEN e.cid END) AS active_users_in_window,
         (SELECT COUNT(*) FROM users u WHERE u.current_ver = e.ver) AS current_users,
         COUNT(DISTINCT CASE WHEN e.event = 'upgrade' AND e.upgrade_via = 'hot_update' THEN e.cid END) AS hot_updates_to_ver,
         COUNT(DISTINCT CASE WHEN e.event = 'upgrade' AND e.upgrade_via = 'installer_upgrade' THEN e.cid END) AS installer_upgrades_to_ver,
         COUNT(DISTINCT CASE WHEN e.event = 'install' AND e.upgrade_via = 'new_install' THEN e.cid END) AS new_installs_on_ver,
         COUNT(DISTINCT CASE WHEN e.event = 'install' AND e.upgrade_via = 'legacy_migration' THEN e.cid END) AS legacy_migrations_to_ver,
         COUNT(DISTINCT CASE WHEN e.event = 'upgrade' AND e.upgrade_via = 'rollback' THEN e.cid END) AS rollbacks_to_ver
       FROM daily_events e
       GROUP BY e.ver
       ORDER BY MAX(e.day) DESC, total_users_reached DESC
       LIMIT 100`
    ).bind(windowStart),
    env.DB.prepare(
      `SELECT
         prev_ver,
         ver,
         upgrade_via,
         COUNT(DISTINCT cid) AS users
       FROM daily_events
       WHERE day >= ?1 AND event = 'upgrade'
       GROUP BY prev_ver, ver, upgrade_via
       ORDER BY users DESC
       LIMIT 50`
    ).bind(windowStart),
    env.DB.prepare(
      `SELECT channel, COUNT(*) AS users
       FROM users
       GROUP BY channel
       ORDER BY users DESC`
    ),
    env.DB.prepare(
      `SELECT
         day,
         COUNT(DISTINCT cid) AS dau,
         COUNT(DISTINCT CASE WHEN event = 'install' AND upgrade_via = 'new_install' THEN cid END) AS new_installs,
         COUNT(DISTINCT CASE WHEN event = 'install' AND upgrade_via = 'legacy_migration' THEN cid END) AS legacy_migrations,
         COUNT(DISTINCT CASE WHEN event = 'upgrade' AND upgrade_via = 'hot_update' THEN cid END) AS hot_updates,
         COUNT(DISTINCT CASE WHEN event = 'upgrade' AND upgrade_via = 'installer_upgrade' THEN cid END) AS installer_upgrades,
         COUNT(DISTINCT CASE WHEN event = 'upgrade' AND upgrade_via = 'rollback' THEN cid END) AS rollbacks
       FROM daily_events
       WHERE day >= ?1
       GROUP BY day
       ORDER BY day ASC`
    ).bind(windowStart),
  ]);

  return jsonResponse({
    generated_at: now.toISOString(),
    today,
    days,
    overview: overviewRes.results?.[0] || {},
    versions: versionsRes.results || [],
    hot_update_matrix: matrixRes.results || [],
    channels: channelsRes.results || [],
    daily_trend: trendRes.results || [],
  });
}

const DASHBOARD_HTML = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ok-bd2 版本与活跃统计看板</title>
  <style>
    :root {
      --bg: #0f1117; --card: #181c26; --border: #282e3f;
      --fg: #e7eaf3; --muted: #939bb0; --accent: #5b8cff;
      --ok: #34d399; --warn: #fbbf24; --danger: #f87171;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); padding: 24px; }
    .wrap { max-width: 1180px; margin: 0 auto; }
    header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 20px; }
    .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    input, select, button {
      background: var(--card); color: var(--fg); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 12px; font-size: 13px;
    }
    button { background: var(--accent); border-color: var(--accent); color: #fff; cursor: pointer; font-weight: 600; }
    button:hover { opacity: 0.92; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
    .kpi-label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .kpi-val { font-size: 26px; font-weight: 700; }
    .kpi-sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
    h2 { font-size: 15px; margin: 0 0 12px; color: var(--fg); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 600; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .section { margin-bottom: 20px; overflow-x: auto; }
    .bars { display: flex; align-items: flex-end; gap: 4px; height: 140px; padding-top: 12px; }
    .bar-col { flex: 1; min-width: 12px; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }
    .bar { width: 100%; max-width: 26px; background: var(--accent); border-radius: 4px 4px 0 0; min-height: 2px; }
    .bar-lbl { font-size: 10px; color: var(--muted); margin-top: 4px; white-space: nowrap; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #232a3b; }
    .badge.hot { color: var(--ok); }
    .badge.inst { color: var(--warn); }
    .badge.rb { color: var(--danger); }
    #status { font-size: 13px; color: var(--muted); margin-bottom: 14px; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>ok-bd2 匿名活跃与热更新统计</h1>
      <div class="controls">
        <input id="token" type="password" placeholder="输入 ADMIN_TOKEN" />
        <select id="days">
          <option value="7">近 7 天</option>
          <option value="14">近 14 天</option>
          <option value="30" selected>近 30 天</option>
          <option value="90">近 90 天</option>
        </select>
        <button id="loadBtn">加载数据</button>
      </div>
    </header>
    <div id="status">请输入 ADMIN_TOKEN 后点击加载。</div>
    <div class="grid" id="kpis"></div>
    <div class="card section">
      <h2>每日活跃趋势（DAU）</h2>
      <div class="bars" id="trendBars"></div>
    </div>
    <div class="card section">
      <h2>各版本触达与升级构成</h2>
      <table>
        <thead>
          <tr>
            <th>版本</th><th class="num">累计触达</th><th class="num">窗口内活跃</th>
            <th class="num">当前停留</th><th class="num">存量热更新</th>
            <th class="num">安装包升级</th><th class="num">老用户迁移</th>
            <th class="num">全新安装</th><th class="num">回退到此版</th>
          </tr>
        </thead>
        <tbody id="versionsBody"></tbody>
      </table>
    </div>
    <div class="card section">
      <h2>存量升级路径矩阵（prev_ver -> ver）</h2>
      <table>
        <thead>
          <tr><th>旧版本</th><th>目标版本</th><th>升级方式</th><th class="num">去重用户数</th></tr>
        </thead>
        <tbody id="matrixBody"></tbody>
      </table>
    </div>
  </div>
  <script>
    const tokenEl = document.getElementById('token');
    const daysEl = document.getElementById('days');
    const statusEl = document.getElementById('status');
    tokenEl.value = sessionStorage.getItem('okbd2_stats_token') || '';

    function esc(s) {
      return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    async function load() {
      const token = tokenEl.value.trim();
      if (!token) { statusEl.textContent = '请先输入 ADMIN_TOKEN。'; return; }
      sessionStorage.setItem('okbd2_stats_token', token);
      statusEl.textContent = '正在加载...';
      try {
        const res = await fetch('/v1/stats?days=' + encodeURIComponent(daysEl.value), {
          headers: { Authorization: 'Bearer ' + token }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        render(data);
        statusEl.textContent = '统计日期（北京时间）：' + data.today + ' · 更新于 ' + new Date(data.generated_at).toLocaleString();
      } catch (e) {
        statusEl.textContent = '加载失败：' + e.message;
      }
    }

    function render(data) {
      const o = data.overview || {};
      const ch = (data.channels || []).map(c => esc(c.channel) + ': ' + c.users).join(' / ') || '-';
      document.getElementById('kpis').innerHTML = [
        ['累计去重用户', o.total_users ?? 0, '渠道 ' + ch],
        ['今日日活 (DAU)', o.dau ?? 0, '今日新装 ' + (o.today_new_installs ?? 0)],
        ['7 日活跃 (WAU)', o.wau ?? 0, '今日热更 ' + (o.today_hot_updates ?? 0)],
        ['30 日活跃 (MAU)', o.mau ?? 0, '今日回退 ' + (o.today_rollbacks ?? 0)],
      ].map(([l, v, s]) => '<div class="card"><div class="kpi-label">' + l + '</div><div class="kpi-val">' + v + '</div><div class="kpi-sub">' + s + '</div></div>').join('');

      const trend = data.daily_trend || [];
      const maxDau = Math.max(1, ...trend.map(t => t.dau || 0));
      document.getElementById('trendBars').innerHTML = trend.map(t => {
        const h = Math.max(4, Math.round(((t.dau || 0) / maxDau) * 110));
        const tip = t.day + ' | DAU:' + t.dau + ' | 热更:' + t.hot_updates + ' | 新装:' + t.new_installs;
        return '<div class="bar-col" title="' + esc(tip) + '"><div class="bar" style="height:' + h + 'px"></div><div class="bar-lbl">' + esc(t.day.slice(5)) + '</div></div>';
      }).join('') || '<div class="kpi-sub">暂无数据</div>';

      document.getElementById('versionsBody').innerHTML = (data.versions || []).map(v =>
        '<tr><td><strong>' + esc(v.ver) + '</strong></td>' +
        '<td class="num">' + v.total_users_reached + '</td>' +
        '<td class="num">' + v.active_users_in_window + '</td>' +
        '<td class="num">' + v.current_users + '</td>' +
        '<td class="num">' + v.hot_updates_to_ver + '</td>' +
        '<td class="num">' + v.installer_upgrades_to_ver + '</td>' +
        '<td class="num">' + v.legacy_migrations_to_ver + '</td>' +
        '<td class="num">' + v.new_installs_on_ver + '</td>' +
        '<td class="num">' + v.rollbacks_to_ver + '</td></tr>'
      ).join('') || '<tr><td colspan="9">暂无版本数据</td></tr>';

      const viaMap = {
        hot_update: ['热更新', 'hot'],
        installer_upgrade: ['安装包覆盖', 'inst'],
        rollback: ['版本回退', 'rb'],
      };
      document.getElementById('matrixBody').innerHTML = (data.hot_update_matrix || []).map(m => {
        const [label, cls] = viaMap[m.upgrade_via] || [m.upgrade_via, ''];
        return '<tr><td>' + esc(m.prev_ver || '-') + '</td><td><strong>' + esc(m.ver) + '</strong></td>' +
          '<td><span class="badge ' + cls + '">' + esc(label) + '</span></td>' +
          '<td class="num">' + m.users + '</td></tr>';
      }).join('') || '<tr><td colspan="4">暂无升级路径记录</td></tr>';
    }

    document.getElementById('loadBtn').addEventListener('click', load);
    daysEl.addEventListener('change', () => { if (tokenEl.value.trim()) load(); });
    if (tokenEl.value.trim()) load();
  </script>
</body>
</html>`;

export async function handleRequest(request, env, now = new Date()) {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/health") {
    return jsonResponse({ ok: true });
  }
  if (request.method === "POST" && url.pathname === "/v1/ping") {
    return handlePing(request, env, now);
  }
  if (request.method === "GET" && url.pathname === "/v1/stats") {
    return handleStats(request, env, now);
  }
  if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/dashboard")) {
    return new Response(DASHBOARD_HTML, {
      status: 200,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  }
  return jsonResponse({ error: "Not found." }, 404);
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
