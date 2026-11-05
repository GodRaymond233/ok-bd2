// The left end of the run bar: is the game connected and how, with a popover to change it.
import { useCallback, useRef, useState } from "react";
import type { Core } from "./App.tsx";
import { CaptureOptions, WindowOptions } from "./DeviceOptions.tsx";
import { t } from "./i18n.ts";
import { Icon, useDismiss } from "./ui.tsx";

export function ConnectionPill({ core }: { core: Core }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, root, close);

  const connected = Boolean(core.device?.connected);
  const starting = Boolean(core.ui?.status.starting);
  const locked = Boolean(core.current) || starting;
  const interaction = core.ui?.interaction_methods.find((item) => item.selected)?.label;
  const name = starting ? t("正在启动游戏") : connected ? core.deviceName || "BrownDust II" : t("未连接游戏");
  const dot = starting ? "d run" : connected ? "d" : "d off";

  return (
    <div className="connwrap" ref={root}>
      <button
        type="button"
        className="connpill"
        aria-expanded={open}
        aria-haspopup="dialog"
        title={connected ? `${t("已连接")} ${core.device?.label ?? ""}` : name}
        onClick={() => setOpen(!open)}
      >
        <span className={dot} />
        <b className="nm">{name}</b>
        {connected && core.captureText && <span className="kbdchip">{core.captureText}</span>}
        {connected && interaction && <span className="kbdchip extra">{interaction}</span>}
        <Icon name={open ? "chev-u" : "chev-d"} />
      </button>
      {open && (
        <div className="pop connpop" role="dialog" aria-label={t("游戏窗口")}>
          <div className="connhd">
            <b>{t("游戏窗口")}</b>
            <span className="grow" />
            <button
              type="button"
              className="btn subtle sm icon"
              disabled={starting}
              title={t("刷新窗口")}
              aria-label={t("刷新窗口")}
              onClick={() => void core.act(core.refreshDevices)}
            >
              <Icon name="refresh" />
            </button>
          </div>
          <WindowOptions core={core} locked={locked} />
          <div className="gtitle">{t("截图方式")}</div>
          <CaptureOptions core={core} locked={locked} />
          {locked && <div className="connnote">{t("运行中或启动游戏时不能切换窗口、截图方式和交互方式。")}</div>}
          <div className="connft">
            <button
              type="button"
              className="linkbtn"
              onClick={() => {
                close();
                core.navigate("device");
              }}
            >
              {t("分辨率、游戏启动与退出")} <Icon name="chev-r" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
