import datetime
import json
import tempfile
import unittest
from pathlib import Path

from src.telemetry import (
    TELEMETRY_DISABLED_ENV,
    TELEMETRY_FORCE_ENV,
    TELEMETRY_OPTION,
    beijing_today,
    build_telemetry_ping,
    detect_channel,
    detect_launcher_sig,
    is_telemetry_enabled,
    load_state,
    run_startup_telemetry,
    snapshot_telemetry_context,
)


def _make_installed_working(root: Path) -> Path:
    working = root / "data" / "apps" / "ok-bd2" / "working"
    working.mkdir(parents=True, exist_ok=True)
    return working


class TelemetryTest(unittest.TestCase):
    def test_beijing_today_uses_utc_plus_8(self):
        utc_dt = datetime.datetime(2026, 11, 27, 16, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(beijing_today(utc_dt), "2026-11-28")

    def test_new_install_vs_legacy_migration(self):
        cid = "11111111-1111-4111-8111-111111111111"
        payload_new, state_new = build_telemetry_ping(
            None,
            current_version="v2.1.3",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=False,
            uuid_factory=lambda: cid,
        )
        self.assertEqual(payload_new["event"], "install")
        self.assertEqual(payload_new["upgrade_via"], "new_install")
        self.assertEqual(payload_new["ver"], "2.1.3")
        self.assertEqual(payload_new["prev_ver"], "")
        self.assertEqual(state_new["last_version"], "2.1.3")

        payload_legacy, _ = build_telemetry_ping(
            None,
            current_version="2.1.3",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=True,
            legacy_version="2.1.2",
            uuid_factory=lambda: cid,
        )
        self.assertEqual(payload_legacy["event"], "install")
        self.assertEqual(payload_legacy["upgrade_via"], "legacy_migration")
        self.assertEqual(payload_legacy["prev_ver"], "2.1.2")

    def test_hot_update_installer_upgrade_and_rollback(self):
        base_state = {
            "cid": "11111111-1111-4111-8111-111111111111",
            "first_seen_at": "2026-11-20",
            "last_version": "2.1.2",
            "last_active_date": "2026-11-26",
            "last_launcher_sig": "100:200",
        }
        hot, _ = build_telemetry_ping(
            base_state,
            current_version="2.1.3",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=True,
        )
        self.assertEqual(hot["event"], "upgrade")
        self.assertEqual(hot["upgrade_via"], "hot_update")
        self.assertEqual(hot["prev_ver"], "2.1.2")

        inst, _ = build_telemetry_ping(
            base_state,
            current_version="2.1.3",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:999",
            had_existing_configs=True,
        )
        self.assertEqual(inst["event"], "upgrade")
        self.assertEqual(inst["upgrade_via"], "installer_upgrade")

        rb, _ = build_telemetry_ping(
            base_state,
            current_version="2.1.1",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=True,
        )
        self.assertEqual(rb["event"], "upgrade")
        self.assertEqual(rb["upgrade_via"], "rollback")

    def test_daily_dedup_and_next_day_active(self):
        state = {
            "cid": "11111111-1111-4111-8111-111111111111",
            "first_seen_at": "2026-11-20",
            "last_version": "2.1.3",
            "last_active_date": "2026-11-27",
            "last_launcher_sig": "100:200",
        }
        same_day, _ = build_telemetry_ping(
            state,
            current_version="2.1.3",
            today="2026-11-27",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=True,
        )
        self.assertIsNone(same_day)

        next_day, next_state = build_telemetry_ping(
            state,
            current_version="2.1.3",
            today="2026-11-28",
            channel="china",
            launcher_sig="100:200",
            had_existing_configs=True,
        )
        self.assertEqual(next_day["event"], "active")
        self.assertEqual(next_day["upgrade_via"], "none")
        self.assertEqual(next_state["last_active_date"], "2026-11-28")

    def test_run_startup_telemetry_end_to_end_and_retry_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            working = _make_installed_working(root)
            launcher = root / "ok-bd2.exe"
            launcher.write_bytes(b"launcher-binary")
            (working.parent / "app.json").write_text(
                '{"git_url": "https://cnb.cool/GodRaymond233/ok-bd2-update.git"}',
                encoding="utf-8",
            )
            configs = working / "configs"
            configs.mkdir()
            (configs / ".core_version").write_text("2.1.2", encoding="utf-8")

            state_path = root / "telemetry.json"
            sent = []
            ctx = snapshot_telemetry_context({"config_folder": "configs"}, working_dir=working)
            self.assertTrue(ctx.had_existing_configs)
            self.assertEqual(ctx.legacy_version, "2.1.2")
            self.assertEqual(detect_channel(working), "china")
            self.assertTrue(detect_launcher_sig(working))

            # Failed send must not persist state so next launch retries.
            ok = run_startup_telemetry(
                {"version": "2.1.3", "config_folder": "configs"},
                ctx,
                state_path=state_path,
                endpoint="https://stats.example.com/v1/ping",
                env={},
                sender=lambda url, payload: False,
                now=datetime.datetime(2026, 11, 27, 4, 0, tzinfo=datetime.timezone.utc),
            )
            self.assertFalse(ok)
            self.assertIsNone(load_state(state_path))

            # Successful send persists state as legacy_migration.
            def fake_sender(url, payload):
                sent.append((url, payload))
                return True

            ok = run_startup_telemetry(
                {"version": "2.1.3", "config_folder": "configs"},
                ctx,
                state_path=state_path,
                endpoint="https://stats.example.com/v1/ping",
                env={},
                sender=fake_sender,
                now=datetime.datetime(2026, 11, 27, 4, 0, tzinfo=datetime.timezone.utc),
            )
            self.assertTrue(ok)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][1]["upgrade_via"], "legacy_migration")
            self.assertEqual(sent[0][1]["channel"], "china")

            # Same day, same version -> skipped.
            ok2 = run_startup_telemetry(
                {"version": "2.1.3", "config_folder": "configs"},
                ctx,
                state_path=state_path,
                endpoint="https://stats.example.com/v1/ping",
                env={},
                sender=fake_sender,
                now=datetime.datetime(2026, 11, 27, 5, 0, tzinfo=datetime.timezone.utc),
            )
            self.assertFalse(ok2)
            self.assertEqual(len(sent), 1)

            # Hot update to 2.1.4 with unchanged launcher -> hot_update!
            ok3 = run_startup_telemetry(
                {"version": "2.1.4", "config_folder": "configs"},
                ctx,
                state_path=state_path,
                endpoint="https://stats.example.com/v1/ping",
                env={},
                sender=fake_sender,
                now=datetime.datetime(2026, 11, 27, 6, 0, tzinfo=datetime.timezone.utc),
            )
            self.assertTrue(ok3)
            self.assertEqual(sent[-1][1]["event"], "upgrade")
            self.assertEqual(sent[-1][1]["upgrade_via"], "hot_update")
            self.assertEqual(sent[-1][1]["prev_ver"], "2.1.3")
            self.assertEqual(sent[-1][1]["ver"], "2.1.4")

    def test_opt_out_and_source_checkout_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Source checkout (not under data/apps/ok-bd2/working) -> skipped unless forced.
            ctx_src = snapshot_telemetry_context({"config_folder": "configs"}, working_dir=root)
            sent = []
            self.assertFalse(
                run_startup_telemetry(
                    {"version": "2.1.3"},
                    ctx_src,
                    state_path=root / "t.json",
                    endpoint="https://stats.example.com/v1/ping",
                    env={},
                    sender=lambda u, p: sent.append(p) or True,
                )
            )
            self.assertEqual(sent, [])

            # Opt-out via Basic Options.json
            working = _make_installed_working(root)
            configs = working / "configs"
            configs.mkdir()
            (configs / "Basic Options.json").write_text(
                json.dumps({TELEMETRY_OPTION: False}), encoding="utf-8"
            )
            ctx_inst = snapshot_telemetry_context({"config_folder": "configs"}, working_dir=working)
            self.assertFalse(is_telemetry_enabled(ctx_inst, env={}))
            self.assertFalse(
                run_startup_telemetry(
                    {"version": "2.1.3"},
                    ctx_inst,
                    state_path=root / "t2.json",
                    endpoint="https://stats.example.com/v1/ping",
                    env={TELEMETRY_FORCE_ENV: "1"},
                    sender=lambda u, p: sent.append(p) or True,
                )
            )
            # Env var disable
            self.assertFalse(is_telemetry_enabled(ctx_src, env={TELEMETRY_DISABLED_ENV: "1"}))


if __name__ == "__main__":
    unittest.main()
