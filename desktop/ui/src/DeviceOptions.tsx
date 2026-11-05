// The game window and capture method choices, on the device page and in the connection popover.
import type { Core } from "./App.tsx";
import { captureChoices } from "./device.ts";
import { coreText, t } from "./i18n.ts";

export function WindowOptions({ core, locked, notes }: { core: Core; locked: boolean; notes?: boolean }) {
  const devices = core.ui?.devices ?? [];
  return (
    <div role="radiogroup" aria-label={t("游戏窗口")}>
      {devices.map((device) => (
        <button
          key={device.id}
          type="button"
          role="radio"
          className="opt"
          aria-checked={Boolean(device.selected)}
          disabled={locked}
          onClick={() => !device.selected && void core.act(() => core.selectDevice(device.id))}
        >
          <span className="rd" />
          <span className="txt">
            <b>{coreText(device.label)}</b>
            {notes && <span className="dsc">{t("只支持 PC 版；列表来自「刷新窗口」。")}</span>}
          </span>
        </button>
      ))}
      {devices.length === 0 && <div className="empty">{t("没有找到游戏窗口")}</div>}
    </div>
  );
}

export function CaptureOptions({ core, locked, notes }: { core: Core; locked: boolean; notes?: boolean }) {
  const choices = core.ui ? captureChoices(core.ui) : [];
  return (
    <div role="radiogroup" aria-label={t("截图方式")}>
      {choices.map((choice) => (
        <button
          key={choice.id}
          type="button"
          role="radio"
          className="opt"
          aria-checked={choice.selected}
          disabled={locked}
          title={notes ? undefined : choice.note}
          onClick={() => !choice.selected && void core.act(() => core.selectCapture(choice.id))}
        >
          <span className="rd" />
          <span className="txt">
            <b>{choice.label}</b>
            {notes && <span className="dsc">{choice.note}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}
