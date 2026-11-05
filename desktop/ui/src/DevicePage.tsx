// 设备: is the game connected and how it is captured, the Starter wait, the window size, and
// the game start and exit options (user decision 2026-10-23: they live here, not in 设置).
import { useEffect, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { sizeText, starterProgress, starterText } from "./device.ts";
import { CaptureOptions, WindowOptions } from "./DeviceOptions.tsx";
import { t } from "./i18n.ts";
import { AUTO_RESIZE_KEY, BASIC, DEVICE_KEYS, findGroup, pickFields } from "./settings.ts";
import type { GamePaths, WindowInfo } from "./types.ts";
import { Icon } from "./ui.tsx";
import { Preview } from "./Preview.tsx";

function Connection({ core }: { core: Core }) {
  const ui = core.ui;
  const device = core.device;
  const wait = ui?.starter_wait ?? null;

  let title;
  let detail;
  let extra = null;
  if (ui?.status.starting) {
    const progress = wait ? starterProgress(wait) : null;
    title = t("正在通过 Starter 启动游戏");
    detail = core.starterTask ? `${t("开始")}「${core.starterTask}」${t("时游戏没有运行，已通过 Starter 启动。")}` : t("游戏没有运行，已通过 Starter 启动。");
    extra = (
      <div className="wait" role="status">
        <div className="row">
          <b>{wait ? starterText(wait) : t("正在启动 Starter…")}</b>
          <span className="grow" />
          <button type="button" className="btn sm" disabled={!wait} onClick={() => void core.act(core.cancelStarter)}>
            {t("取消等待")}
          </button>
        </div>
        {progress !== null && (
          <div className="bar">
            <i style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        )}
        {wait?.message && <div className="dsc">{wait.message.split("\n").slice(1).join(" ") || t("请在 Starter 里完成操作，等待会自动继续。")}</div>}
      </div>
    );
  } else if (core.startError) {
    title = t("启动游戏失败");
    detail = core.startError;
    extra = (
      <>
        <div className="warnline">{t("常见原因：Starter 需要登录或更新、游戏被安全软件拦截。手动打开游戏后再开始，或检查下方的游戏位置。")}</div>
        <div className="row">
          <button type="button" className="btn sm" onClick={() => core.openLogs(null)}>
            {t("查看日志")}
          </button>
          <button type="button" className="btn sm subtle" onClick={core.dismissStartError}>
            {t("知道了")}
          </button>
        </div>
      </>
    );
  } else if (device?.connected) {
    title = `${t("已连接")} ${core.deviceName || "BrownDust II"}`;
    const interaction = ui?.interaction_methods.find((item) => item.selected)?.label ?? "";
    detail = `${t("截图")} ${core.captureText}${interaction ? ` · ${t("交互")} ${interaction}` : ""}`;
  } else {
    title = t("未找到游戏窗口");
    detail = t("游戏没有运行。开始任务时会通过 Starter 自动启动游戏；也可以先手动打开游戏，再点「刷新窗口」。");
  }

  return (
    <div className="card conncard">
      <div className="main-col">
        <div className="big">
          <span className={device?.connected && !ui?.status.starting ? "d" : "d off"} />
          {title}
        </div>
        <div className="dsc">{detail}</div>
        {extra}
      </div>
      <Preview core={core} />
    </div>
  );
}

function Resolution({ core }: { core: Core }) {
  const [info, setInfo] = useState<WindowInfo | null>(null);
  const [target, setTarget] = useState<string>("");
  const [result, setResult] = useState<{ text: string; error: boolean } | null>(null);
  const [working, setWorking] = useState(false);
  const connected = Boolean(core.device?.connected);
  const deviceKey = JSON.stringify(core.device ?? null);

  useEffect(() => {
    let cancelled = false;
    api.window().then(
      (value) => {
        if (cancelled) {
          return;
        }
        setInfo(value);
        setTarget((current) => {
          if (current) {
            return current;
          }
          const inList = value.size && value.resolutions.some(([w, h]) => w === value.size?.[0] && h === value.size?.[1]);
          return (inList ? value.size : value.default)?.join("x") ?? "";
        });
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [deviceKey, result]);

  const apply = () => {
    const [width, height] = target.split("x").map(Number);
    setWorking(true);
    setResult(null);
    api.resizeWindow(width, height).then(
      (value) =>
        setResult({
          text: value.changed ? `${t("已调整到")} ${sizeText([value.width, value.height])}` : `${t("已经是")} ${sizeText([value.width, value.height])}`,
          error: false,
        }),
      (reason: unknown) => setResult({ text: `${t("调整失败")}：${errorText(reason)}`, error: true }),
    ).finally(() => setWorking(false));
  };
  const running = Boolean(core.current) && !core.ui?.status.paused;
  const autoResize = pickFields(findGroup(core.settings ?? [], BASIC), [AUTO_RESIZE_KEY]);

  return (
    <div className="card devcard">
      <div className="bh">{t("分辨率")}</div>
      <div className="pad">
        <div className="row">
          <span className="dsc">{t("游戏画面")}</span>
          <b className="mono">{connected ? sizeText(info?.size) : "—"}</b>
          {connected && info?.size && (
            <span className={info.ratio_ok ? "chip ok" : "chip warn"}>{info.ratio_ok ? "16:9 ✓" : t("不是 16:9")}</span>
          )}
        </div>
        {connected && info?.size && !info.ratio_ok && (
          <div className="warnline">{t("识别按 16:9 设计，这个比例可能点错位置。调整到下面任一分辨率再开始。")}</div>
        )}
        <div className="row">
          <select className="select" value={target} aria-label={t("目标分辨率")} onChange={(event) => setTarget(event.target.value)}>
            {(info?.resolutions ?? []).map(([width, height]) => (
              <option key={`${width}x${height}`} value={`${width}x${height}`}>
                {sizeText([width, height])}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn sm primary"
            disabled={!connected || running || working || Boolean(core.ui?.status.starting) || !target}
            onClick={apply}
          >
            {working ? t("正在调整…") : t("应用")}
          </button>
        </div>
        <div className="why">
          {running
            ? t("任务运行中：先在运行面板暂停，才能调整。")
            : t("调整游戏窗口的画面区域；屏幕放得下就转成窗口模式，放不下时保留无边框。失败会恢复原来的窗口。")}
        </div>
        {result && (
          <div className={result.error ? "ferr" : "saved"} role="status">
            {!result.error && <Icon name="check" />}
            {result.text}
          </div>
        )}
      </div>
      {autoResize.length > 0 && (
        <ConfigForm fields={autoResize} disabled={false} saveField={(key, value) => core.setSetting(BASIC, key, value)} />
      )}
    </div>
  );
}

export function DevicePage({ core }: { core: Core }) {
  const ui = core.ui;
  const [paths, setPaths] = useState<GamePaths | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.game().then(
      (value) => !cancelled && setPaths(value),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const locked = Boolean(core.current) || Boolean(ui?.status.starting);
  const lockReason = locked ? t("运行中或启动游戏时不能切换窗口、截图方式和交互方式。") : null;
  const basic = findGroup(core.settings ?? [], BASIC);
  const select = (action: () => Promise<unknown>) => void core.act(action);

  return (
    <div className="page device-page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("设备")}</h1>
        </div>
      </div>
      <Connection core={core} />
      <div className="grid2">
        <div className="stack">
          <div className="card devcard">
            <div className="bh">
              {t("游戏窗口")}
              <span className="grow" />
              <button type="button" className="btn sm" disabled={Boolean(ui?.status.starting)} onClick={() => select(core.refreshDevices)}>
                <Icon name="refresh" />
                {t("刷新窗口")}
              </button>
            </div>
            <WindowOptions core={core} locked={locked} notes />
            {lockReason && <div className="hidden-note">{lockReason}</div>}
          </div>
          <div className="card devcard">
            <div className="bh">
              {t("截图方式")}
              <span className="grow" />
              {ui?.capture_in_use && (
                <span className="dsc">
                  {t("正在使用")} <span className="chip info">{ui.capture_in_use}</span>
                </span>
              )}
            </div>
            <CaptureOptions core={core} locked={locked} notes />
            <div className="fld">
              <div className="lb">{t("交互方式")}</div>
              <div className="dsc">{t("BD2 专用，只有这一种。")}</div>
              <div className="ctrl">
                <span className="dsc">{ui?.interaction_methods.find((item) => item.selected)?.label ?? "—"}</span>
              </div>
            </div>
          </div>
        </div>
        <div className="stack">
          <Resolution core={core} />
          <div className="card devcard">
            <div className="bh">{t("游戏启动与退出")}</div>
            <ConfigForm fields={pickFields(basic, DEVICE_KEYS)} disabled={false} saveField={(key, value) => core.setSetting(BASIC, key, value)}>
              <div className="fld">
                <div className="lb">{t("游戏位置")}</div>
                <div className="dsc mono path">{paths?.game || t("没有找到")}</div>
                <div className="ctrl">
                  <span className="chip">{t("自动检测")}</span>
                </div>
              </div>
              <div className="fld">
                <div className="lb">Starter</div>
                <div className="dsc mono path">{paths?.launcher || t("没有找到")}</div>
                <div className="ctrl">
                  <span className="chip">{t("自动检测")}</span>
                </div>
              </div>
            </ConfigForm>
          </div>
        </div>
      </div>
    </div>
  );
}
