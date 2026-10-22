// Pages designed in a later batch. 日志 and 设备 keep what the stage-1 check page offered
// (recent logs, the log folder, window refresh) until their own designs land.
import type { Core } from "./App.tsx";
import { t } from "./i18n.ts";
import { levelName, type LogLine } from "./state.ts";
import { Icon } from "./ui.tsx";

function Later({ title, what }: { title: string; what: string }) {
  return (
    <div className="page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{title}</h1>
        </div>
      </div>
      <div className="card empty" style={{ marginTop: 18 }}>
        {what}
      </div>
    </div>
  );
}

export function RecordsPage() {
  return <Later title={t("记录")} what={t("运行历史列表、筛选和失败详情在阶段 3 接入；各任务最近 3 次运行见任务详情。")} />;
}

export function SettingsPage() {
  return <Later title={t("设置")} what={t("通用、通知、更新与关于随后接入；现在请在 Qt 版中修改。")} />;
}

export function LogsPage({ core, logs }: { core: Core; logs: LogLine[] }) {
  return (
    <div className="page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("日志")}</h1>
          <span className="date">{t("最近")} {logs.length} {t("条")}</span>
        </div>
        <button type="button" className="btn" onClick={() => void core.openLogsFolder()}>
          <Icon name="folder" />
          {t("打开日志文件夹")}
        </button>
      </div>
      <div className="logs tall" style={{ marginTop: 18 }} aria-live="polite">
        {logs.length === 0
          ? t("暂无日志")
          : logs.map((line) => (
              <div key={line.key} className={line.level >= 40 ? "bad" : undefined} title={levelName(line.level)}>
                {line.text}
              </div>
            ))}
      </div>
      <p className="why">{t("级别筛选与搜索在日志页的正式设计里接入。")}</p>
    </div>
  );
}

export function DevicePage({ core }: { core: Core }) {
  const devices = core.ui?.devices ?? [];
  const interaction = core.ui?.interaction_methods.find((item) => item.selected)?.label ?? t("无");
  return (
    <div className="page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("设备")}</h1>
        </div>
        <button type="button" className="btn" onClick={() => void core.act(core.refreshDevices)}>
          <Icon name="refresh" />
          {t("刷新窗口")}
        </button>
      </div>
      <div className="card" style={{ marginTop: 18, padding: "6px 0" }}>
        <div className="fld">
          <div className="lb">{t("游戏窗口")}</div>
          <div className="dsc">{devices.length ? devices.map((device) => device.label).join("；") : t("未找到游戏窗口")}</div>
        </div>
        <div className="fld">
          <div className="lb">{t("截图方式")}</div>
          <div className="dsc">{core.captureText}</div>
        </div>
        <div className="fld">
          <div className="lb">{t("交互方式")}</div>
          <div className="dsc">{interaction}</div>
        </div>
      </div>
      <p className="why">{t("切换窗口与截图方式、分辨率调整、Starter 等待与取消在设备页的正式设计里接入；现在请在 Qt 版中修改。")}</p>
    </div>
  );
}
