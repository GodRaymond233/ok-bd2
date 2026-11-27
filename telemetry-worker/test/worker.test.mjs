import test from "node:test";
import assert from "node:assert/strict";
import {
  beijingDate,
  checkRateLimit,
  handleRequest,
  resetRateLimiter,
  validatePayload,
} from "../src/index.js";

function createFakeD1() {
  const users = new Map();
  const events = new Map();

  function execStatement(sql, params) {
    if (sql.includes("INSERT INTO users")) {
      const [cid, day, ver, channel, firstVia, os, shellVer, updatedAt] = params;
      const existing = users.get(cid);
      if (!existing) {
        users.set(cid, {
          cid,
          first_seen_date: day,
          last_seen_date: day,
          current_ver: ver,
          channel,
          first_via: firstVia,
          os,
          shell_ver: shellVer,
          updated_at: updatedAt,
        });
      } else {
        existing.last_seen_date = day > existing.last_seen_date ? day : existing.last_seen_date;
        existing.current_ver = ver;
        if (channel !== "unknown") existing.channel = channel;
        if (os !== "unknown") existing.os = os;
        if (shellVer) existing.shell_ver = shellVer;
        existing.updated_at = updatedAt;
      }
      return { results: [] };
    }
    if (sql.includes("INSERT OR IGNORE INTO daily_events")) {
      const [day, cid, event, ver, prevVer, upgradeVia, channel, shellVer, os, createdAt] = params;
      const key = `${day}|${cid}|${event}|${ver}`;
      if (!events.has(key)) {
        events.set(key, {
          day,
          cid,
          event,
          ver,
          prev_ver: prevVer,
          upgrade_via: upgradeVia,
          channel,
          shell_ver: shellVer,
          os,
          created_at: createdAt,
        });
      }
      return { results: [] };
    }
    if (sql.includes("AS total_users,")) {
      const [today, wauStart, mauStart] = params;
      const allEvents = [...events.values()];
      const distinct = (pred) => new Set(allEvents.filter(pred).map((e) => e.cid)).size;
      return {
        results: [
          {
            total_users: users.size,
            dau: distinct((e) => e.day === today),
            wau: distinct((e) => e.day >= wauStart),
            mau: distinct((e) => e.day >= mauStart),
            today_new_installs: distinct(
              (e) => e.day === today && e.event === "install" && e.upgrade_via === "new_install"
            ),
            today_hot_updates: distinct(
              (e) => e.day === today && e.event === "upgrade" && e.upgrade_via === "hot_update"
            ),
            today_rollbacks: distinct(
              (e) => e.day === today && e.event === "upgrade" && e.upgrade_via === "rollback"
            ),
          },
        ],
      };
    }
    if (sql.includes("AS total_users_reached")) {
      const [windowStart] = params;
      const byVer = new Map();
      for (const e of events.values()) {
        if (!byVer.has(e.ver)) byVer.set(e.ver, []);
        byVer.get(e.ver).push(e);
      }
      const results = [];
      for (const [ver, list] of byVer) {
        const distinct = (pred) => new Set(list.filter(pred).map((e) => e.cid)).size;
        results.push({
          ver,
          total_users_reached: new Set(list.map((e) => e.cid)).size,
          active_users_in_window: distinct((e) => e.day >= windowStart),
          current_users: [...users.values()].filter((u) => u.current_ver === ver).length,
          hot_updates_to_ver: distinct((e) => e.event === "upgrade" && e.upgrade_via === "hot_update"),
          installer_upgrades_to_ver: distinct(
            (e) => e.event === "upgrade" && e.upgrade_via === "installer_upgrade"
          ),
          new_installs_on_ver: distinct((e) => e.event === "install" && e.upgrade_via === "new_install"),
          legacy_migrations_to_ver: distinct(
            (e) => e.event === "install" && e.upgrade_via === "legacy_migration"
          ),
          rollbacks_to_ver: distinct((e) => e.event === "upgrade" && e.upgrade_via === "rollback"),
        });
      }
      return { results };
    }
    if (sql.includes("GROUP BY prev_ver, ver, upgrade_via")) {
      const [windowStart] = params;
      const groups = new Map();
      for (const e of events.values()) {
        if (e.day >= windowStart && e.event === "upgrade") {
          const k = `${e.prev_ver}|${e.ver}|${e.upgrade_via}`;
          if (!groups.has(k)) {
            groups.set(k, { prev_ver: e.prev_ver, ver: e.ver, upgrade_via: e.upgrade_via, cids: new Set() });
          }
          groups.get(k).cids.add(e.cid);
        }
      }
      return {
        results: [...groups.values()].map((g) => ({
          prev_ver: g.prev_ver,
          ver: g.ver,
          upgrade_via: g.upgrade_via,
          users: g.cids.size,
        })),
      };
    }
    if (sql.includes("FROM users")) {
      const counts = new Map();
      for (const u of users.values()) {
        counts.set(u.channel, (counts.get(u.channel) || 0) + 1);
      }
      return { results: [...counts.entries()].map(([channel, count]) => ({ channel, users: count })) };
    }
    if (sql.includes("GROUP BY day")) {
      const [windowStart] = params;
      const days = new Map();
      for (const e of events.values()) {
        if (e.day >= windowStart) {
          if (!days.has(e.day)) days.set(e.day, []);
          days.get(e.day).push(e);
        }
      }
      return {
        results: [...days.entries()].map(([day, list]) => ({
          day,
          dau: new Set(list.map((e) => e.cid)).size,
          new_installs: new Set(
            list.filter((e) => e.event === "install" && e.upgrade_via === "new_install").map((e) => e.cid)
          ).size,
          legacy_migrations: new Set(
            list
              .filter((e) => e.event === "install" && e.upgrade_via === "legacy_migration")
              .map((e) => e.cid)
          ).size,
          hot_updates: new Set(
            list.filter((e) => e.event === "upgrade" && e.upgrade_via === "hot_update").map((e) => e.cid)
          ).size,
          installer_upgrades: new Set(
            list
              .filter((e) => e.event === "upgrade" && e.upgrade_via === "installer_upgrade")
              .map((e) => e.cid)
          ).size,
          rollbacks: new Set(
            list.filter((e) => e.event === "upgrade" && e.upgrade_via === "rollback").map((e) => e.cid)
          ).size,
        })),
      };
    }
    throw new Error(`Unhandled SQL: ${sql}`);
  }

  return {
    users,
    events,
    prepare(sql) {
      return {
        sql,
        params: [],
        bind(...params) {
          return { sql, params };
        },
      };
    },
    async batch(statements) {
      return statements.map((stmt) => execStatement(stmt.sql, stmt.params || []));
    },
  };
}

const CID_1 = "11111111-1111-4111-8111-111111111111";
const CID_2 = "22222222-2222-4222-8222-222222222222";

test("validatePayload accepts valid ping and normalizes v prefix", () => {
  const res = validatePayload({
    cid: CID_1,
    event: "upgrade",
    ver: "v2.1.3",
    prev_ver: "v2.1.2",
    upgrade_via: "hot_update",
    channel: "China",
    shell_ver: "0.3.6",
    os: "win11",
  });
  assert.equal(res.ok, true);
  assert.equal(res.data.ver, "2.1.3");
  assert.equal(res.data.prev_ver, "2.1.2");
  assert.equal(res.data.channel, "china");
});

test("validatePayload rejects bad cid and unknown event", () => {
  assert.equal(validatePayload({ cid: "not-a-uuid", event: "active", ver: "2.1.3" }).ok, false);
  assert.equal(validatePayload({ cid: CID_1, event: "hack", ver: "2.1.3" }).ok, false);
  assert.equal(validatePayload({ cid: CID_1, event: "active", ver: "bad" }).ok, false);
});

test("beijingDate uses UTC+8 boundary", () => {
  // 2026-11-27 16:30:00Z is 2026-11-28 00:30:00 in UTC+8
  assert.equal(beijingDate(new Date("2026-11-27T16:30:00Z")), "2026-11-28");
});

test("ping writes idempotently and stats requires valid bearer token", async () => {
  resetRateLimiter();
  const db = createFakeD1();
  const env = { DB: db, ADMIN_TOKEN: "super-secret-token" };
  const now = new Date("2026-11-27T08:00:00Z");

  const pingBody = JSON.stringify({
    cid: CID_1,
    event: "upgrade",
    ver: "2.1.3",
    prev_ver: "2.1.2",
    upgrade_via: "hot_update",
    channel: "china",
    shell_ver: "0.3.6",
    os: "win11",
  });

  // Send twice -> idempotent
  for (let i = 0; i < 2; i += 1) {
    const res = await handleRequest(
      new Request("https://stats.example.com/v1/ping", {
        method: "POST",
        headers: { "content-type": "application/json", "cf-connecting-ip": "1.2.3.4" },
        body: pingBody,
      }),
      env,
      now
    );
    assert.equal(res.status, 204);
  }

  // Second user new install
  const res2 = await handleRequest(
    new Request("https://stats.example.com/v1/ping", {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": "5.6.7.8" },
      body: JSON.stringify({
        cid: CID_2,
        event: "install",
        ver: "2.1.3",
        prev_ver: "",
        upgrade_via: "new_install",
        channel: "global",
        shell_ver: "0.3.6",
        os: "win10",
      }),
    }),
    env,
    now
  );
  assert.equal(res2.status, 204);
  assert.equal(db.events.size, 2);

  // Unauthorized stats rejected
  const unauth = await handleRequest(new Request("https://stats.example.com/v1/stats"), env, now);
  assert.equal(unauth.status, 401);

  // Authorized stats returns expected counts
  const okStats = await handleRequest(
    new Request("https://stats.example.com/v1/stats?days=7", {
      headers: { authorization: "Bearer super-secret-token" },
    }),
    env,
    now
  );
  assert.equal(okStats.status, 200);
  const stats = await okStats.json();
  assert.equal(stats.overview.total_users, 2);
  assert.equal(stats.overview.dau, 2);
  assert.equal(stats.overview.today_hot_updates, 1);
  assert.equal(stats.overview.today_new_installs, 1);
  assert.equal(stats.versions[0].ver, "2.1.3");
  assert.equal(stats.versions[0].hot_updates_to_ver, 1);
  assert.equal(stats.hot_update_matrix[0].prev_ver, "2.1.2");
});

test("rate limiter blocks bursts over threshold", () => {
  resetRateLimiter();
  const base = 1_700_000_000_000;
  for (let i = 0; i < 20; i += 1) {
    assert.equal(checkRateLimit("9.9.9.9", base + i), true);
  }
  assert.equal(checkRateLimit("9.9.9.9", base + 25), false);
  assert.equal(checkRateLimit("9.9.9.9", base + 61_000), true);
});
