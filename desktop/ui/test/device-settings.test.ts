import assert from "node:assert/strict";
import { test } from "node:test";
import { captureChoices, sizeText, starterProgress, starterText } from "../src/device.ts";
import { fieldLabel, optionText } from "../src/form.ts";
import { BASIC, DEVICE_KEYS, findGroup, needsDesktopRetry, otherFields, pickFields, SHELL_KEYS, shellSettingsKey } from "../src/settings.ts";
import type { ConfigField, SettingsGroup, ShellDesktop } from "../src/types.ts";

test("the shell is only told about settings it applies", () => {
  const group = (hotkey: string, other: string) =>
    [{ name: BASIC, label: BASIC, fields: [
      { key: "Start/Stop", value: hotkey },
      { key: "Minimize Window to System Tray when Closing", value: false },
      { key: "Auto Resize Game Window", value: other },
    ] }] as unknown as SettingsGroup[];
  assert.equal(shellSettingsKey(null), null);
  assert.equal(shellSettingsKey(group("F9", "a")), shellSettingsKey(group("F9", "b")));
  assert.notEqual(shellSettingsKey(group("F9", "a")), shellSettingsKey(group("F10", "a")));
});

test("the settings page polls the shell only while a desktop problem is open", () => {
  const healthy: ShellDesktop = {
    zoom: 100,
    zoomLevels: [100],
    closeToTray: false,
    hotkey: "F9",
    registeredHotkey: "F9",
    hotkeyError: null,
    syncError: null,
    trayAvailable: true,
  };
  assert.equal(needsDesktopRetry(null, null), false);
  assert.equal(needsDesktopRetry(healthy, null), false);
  assert.equal(needsDesktopRetry({ ...healthy, hotkeyError: "占用" }, null), true);
  assert.equal(needsDesktopRetry({ ...healthy, syncError: "读取失败" }, null), true);
  assert.equal(needsDesktopRetry(healthy, "无权访问"), true);
});

const methods = ["WGC", "BitBlt_RenderFull", "ForegroundBitBlt"].map((id) => ({ id, label: id, selected: false }));

test("capture choices start with automatic, which is chosen when the core has no preference", () => {
  const auto = captureChoices({ capture_methods: methods, capture_preference: null });
  assert.deepEqual(
    auto.map((choice) => [choice.id, choice.selected]),
    [
      ["auto", true],
      ["WGC", false],
      ["BitBlt_RenderFull", false],
      ["ForegroundBitBlt", false],
    ],
  );
  assert.match(auto[0].note, /WGC → BitBlt_RenderFull → ForegroundBitBlt/);
  const fixed = captureChoices({ capture_methods: methods, capture_preference: "ForegroundBitBlt" });
  assert.deepEqual(
    fixed.filter((choice) => choice.selected).map((choice) => choice.id),
    ["ForegroundBitBlt"],
  );
  assert.equal(sizeText([1920, 1080]), "1920 × 1080");
  assert.equal(sizeText(null), "—");
});

test("the Starter wait says how long it waited and how long it may still wait", () => {
  assert.equal(starterText({ message: "", seconds_left: 78, waited: 42 }), "已等待 00:42 · 最多再等 01:18");
  assert.equal(starterProgress({ message: "", seconds_left: 78, waited: 42 }), 0.35);
  const updating = { message: "游戏正在更新，已等待 42 秒\n连续 5 分钟未检测到进度", seconds_left: 0, waited: 42 };
  assert.equal(starterText(updating), "已等待 00:42 · 游戏正在更新");
  assert.equal(starterProgress(updating), null);
  assert.equal(starterText({ message: "", seconds_left: 0, waited: 3 }), "已等待 00:03");
});

function field(key: string, extra: Partial<ConfigField> = {}): ConfigField {
  return {
    key,
    value: false,
    default: false,
    description: "",
    kind: "boolean",
    options: null,
    allow_duplication: false,
    minimum: null,
    maximum: null,
    sub_config: false,
    ...extra,
  };
}

test("basic settings split between the device page, the shell and 设置", () => {
  const keys = [
    "Auto Start Game When App Starts",
    "Minimize Window to System Tray when Closing",
    "Mute Game while in Background",
    "Auto Resize Game Window",
    "Exit App when Game Exits",
    "Use DirectML",
    "Trigger Interval",
    "Start/Stop",
    "Kill Launcher After Start",
    "Enable Blur",
  ];
  const groups: SettingsGroup[] = [{ name: BASIC, label: "基本设置", description: "", top_level: false, fields: keys.map((key) => field(key)) }];
  const basic = findGroup(groups, BASIC);
  assert.deepEqual(
    pickFields(basic, DEVICE_KEYS).map((item) => item.key),
    DEVICE_KEYS,
  );
  assert.deepEqual(
    otherFields(basic, [...DEVICE_KEYS, "Auto Resize Game Window", ...SHELL_KEYS,
      "Kill Launcher After Start"]).map((item) => item.key),
    ["Use DirectML", "Trigger Interval", "Enable Blur"],
  );
  assert.deepEqual(pickFields(undefined, DEVICE_KEYS), []);
});

test("fields show the core's names for settings and options", () => {
  const directml = field("Use DirectML", {
    label: "用显卡加速识别（DirectML）",
    kind: "select",
    option_labels: { Auto: "自动", Yes: "是" },
  });
  assert.equal(fieldLabel(directml), "用显卡加速识别（DirectML）");
  assert.equal(optionText(directml, "Yes"), "是");
  assert.equal(optionText(directml, "No"), "No");
  assert.equal(fieldLabel(field("启用")), "启用");
});
