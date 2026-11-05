import { useEffect, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { t } from "./i18n.ts";
import { Icon } from "./ui.tsx";

export function Preview({ core }: { core: Core }) {
  const connected = Boolean(core.device?.connected);
  const [live, setLive] = useState(true);
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!connected || !live) return;
    let cancelled = false;
    let url: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const tick = async () => {
      try {
        if (!document.hidden) {
          const blob = await api.preview(controller.signal);
          if (cancelled) return;
          const next = URL.createObjectURL(blob);
          if (url) URL.revokeObjectURL(url);
          url = next;
          setImage(next);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) { setImage(null); setError(errorText(reason)); }
      } finally {
        if (!cancelled) timer = setTimeout(() => void tick(), 500);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
      setImage(null);
    };
  }, [connected, live]);
  return <div className="shot-col">
    <div className="shot empty-shot">{connected && image ? <img src={image} alt={t("脚本当前捕获的游戏画面")} /> : <span>{!connected ? t("连接游戏后显示截图预览") : !live ? t("预览已暂停") : error || t("正在获取画面…")}</span>}</div>
    <div className="row"><button className="btn sm" disabled={!connected} onClick={() => setLive(!live)}>{live ? t("暂停预览") : t("继续预览")}</button>
      <button className="btn sm" disabled={!connected} onClick={() => void core.act(async () => { await core.tool("capture"); core.notify(t("截图已保存到截图文件夹")); })}><Icon name="camera" />{t("保存截图")}</button></div>
    <div className="ds">{t("预览仅在此页可见时刷新；暂停预览不会暂停任务。")}</div>
  </div>;
}
