// 设置: 通用, 通知, 关于与更新. Fields come from ok-script's global settings, named by the
// core; the game start and exit options are on the device page.
import { type ReactNode, useEffect, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { versionText } from "./format.ts";
import { coreText, type LanguagePreference, t } from "./i18n.ts";
import {
  AUTO_RESIZE_KEY,
  BASIC,
  DESKTOP_RETRY_MS,
  DEVICE_KEYS,
  findGroup,
  LAUNCHER,
  needsDesktopRetry,
  NOTIFICATION,
  otherFields,
  pickFields,
  SHELL_KEYS,
  shellSettingsKey,
  SYSTEM_NOTIFICATION_KEY,
  TEST_CHANNELS,
  applyTheme,
  storedTheme,
} from "./settings.ts";
import type { AboutInfo, BackupEntry, BackupList, McpState, ShellDesktop, Theme, UpdateCheck } from "./types.ts";
import { ConfirmDialog, Icon, Menu, PageHead, Toggle, useSlider } from "./ui.tsx";

type Tab = "general" | "notify" | "about";
const OK_SCRIPT_URL = "https://github.com/ok-oldking/ok-script";
const NOTICES_URL = "https://github.com/GodRaymond233/ok-bd2/blob/main/THIRD_PARTY_NOTICES.md";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="setsec">
      <h2 className="gtitle">{title}</h2>
      {children}
    </section>
  );
}

function McpSettings({ core }: { core: Core }) {
  const [state, setState] = useState<McpState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [working, setWorking] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () => void api.mcp().then(
      (next) => { if (active) { setState(next); setError(null); } },
      (reason: unknown) => { if (active) setError(errorText(reason)); },
    );
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const change = async (enabled: boolean) => {
    setWorking(true); setError(null);
    try { setState(await api.setMcp(enabled, enabled)); setConfirm(false); }
    catch (reason) { setError(errorText(reason)); }
    finally { setWorking(false); }
  };
  return <Section title={t("本机工具连接（MCP）")}>
    <div className="card form">
      <div className="fld">
        <div className="lb">{t("启用 MCP 服务")}</div>
        <div className="dsc">{t("让受信任的本机编程工具连接客户端。默认关闭，需要连接时再开启。")}</div>
        <div className="ctrl"><Toggle label={t("启用 MCP 服务")} checked={Boolean(state?.enabled || state?.requested)}
          disabled={!state || working || (!state.enabled && core.busy) || Boolean(core.ui?.maintenance) || Boolean(core.ui?.diagnostic_pause)}
          onChange={(enabled) => enabled ? setConfirm(true) : void change(false)} /></div>
      </div>
      {state?.enabled && <div className="pad">
        <div className="row"><code>{state.url}</code><button className="btn sm subtle" onClick={() => void core.act(async () => { await navigator.clipboard.writeText(state.url ?? ""); core.notify(t("MCP 地址已复制")); })}>{t("复制地址")}</button></div>
        <p className="why">{t("连接令牌保存在以下本机文件中，请只交给你信任的工具，不要发送到群聊或问题报告。")}</p>
        <p className="dsc mono path">{state.connection_file}</p>
        <button className="btn sm" onClick={() => void core.act(core.openLogsFolder)}>{t("打开日志文件夹")}</button>
      </div>}
      {(error || state?.error) && <div className="ferr" role="alert">{error ?? coreText(state?.error ?? "")}</div>}
    </div>
    {confirm && <ConfirmDialog title={t("启用 MCP 服务？")} confirm={t("允许并开启")} disabled={working || core.busy}
      onConfirm={() => void change(true)} onCancel={() => setConfirm(false)}>
      <p>{t("连接的工具可以使用你的账户权限运行 Python、读取或修改文件，并操作客户端和游戏。请只连接受信任的工具，并保管好连接令牌。")}</p>
      {error && <div className="ferr">{error}</div>}
    </ConfirmDialog>}
  </Section>;
}

function General({ core }: { core: Core }) {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const themePick = useSlider<HTMLSpanElement>('[aria-pressed="true"]', theme);
  const [desktop, setDesktop] = useState<ShellDesktop | null>(null);
  const [desktopError, setDesktopError] = useState<string | null>(null);
  const [zoomSaving, setZoomSaving] = useState(false);
  const shell = window.okbd2Shell;
  // The shell's answer changes when a setting does; only a problem it keeps retrying can clear alone.
  const retrying = needsDesktopRetry(desktop, desktopError);
  const shellSettings = shellSettingsKey(core.settings);
  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (!shell?.desktop) return;
      shell.desktop().then((value) => {
        if (active) { setDesktop(value); setDesktopError(null); }
      }, (reason) => { if (active) setDesktopError(errorText(reason)); });
    };
    refresh();
    const timer = retrying ? window.setInterval(refresh, DESKTOP_RETRY_MS) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [shell, shellSettings, retrying]);
  const basic = findGroup(core.settings ?? [], BASIC);
  const batch = core.today?.batch;
  const locked = core.busy;
  const save = (key: string, value: unknown) => core.setSetting(BASIC, key, value);
  const themes: [Theme, string][] = [
    ["system", t("跟随系统")],
    ["light", t("浅色")],
    ["dark", t("深色")],
  ];
  return (
    <>
      <Section title={t("外观")}>
        <div className="card form">
          <div className="fld">
            <div className="lb">{t("主题")}</div>
            <div className="dsc">{t("只影响这台电脑。")}</div>
            <div className="ctrl">
              <span className="segpick slides" ref={themePick} role="group" aria-label={t("主题")}>
                {themes.map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={theme === value}
                    onClick={() => {
                      setTheme(value);
                      applyTheme(value);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </span>
            </div>
          </div>
          <div className="fld">
            <div className="lb">{t("语言")}</div>
            <div className="dsc">{t("立即生效。任务日志和游戏里的文字不受影响。")}</div>
            <div className="ctrl">
              <select className="select" aria-label={t("语言")} value={core.language}
                onChange={(event) => void core.act(() => core.setLanguage(event.target.value as LanguagePreference))}>
                <option value="Auto">{t("跟随系统")}</option>
                <option value="zh_CN">简体中文</option>
                <option value="en_US">English</option>
              </select>
            </div>
          </div>
          <div className="fld">
            <div className="lb">
              {t("界面缩放")}
            </div>
            <div className="dsc">{t("在 Windows 缩放基础上调整客户端文字与控件大小，重启后保留。")}</div>
            <div className="ctrl">
              <select className="select" aria-label={t("界面缩放")} value={desktop?.zoom ?? 100} disabled={!desktop || zoomSaving}
                onChange={(event) => {
                  const zoom = Number(event.target.value);
                  if (!shell?.setZoom) return;
                  setZoomSaving(true);
                  void core.act(async () => setDesktop(await shell.setZoom(zoom)))
                    .finally(() => setZoomSaving(false));
                }}>
                {(desktop?.zoomLevels ?? [100]).map((zoom) => <option key={zoom} value={zoom}>{zoom}%</option>)}
              </select>
            </div>
          </div>
        </div>
      </Section>
      {batch && (
        <Section title={t("日常")}>
          <div className="card form">
            <div className="fld">
              <div className="lb">{t("启动后自动执行剩余日常")}</div>
              <div className="dsc">{t("与「日常」页上的是同一个开关。")}</div>
              <div className="ctrl">
                <Toggle
                  checked={batch.auto_start}
                  disabled={locked}
                  label={t("启动后自动执行剩余日常")}
                  onChange={(value) => void core.act(() => core.setConfig(batch.name, "启动自动执行日常", value))}
                />
              </div>
            </div>
            <div className="fld">
              <div className="lb">{t("完成日常后自动关机")}</div>
              <div className="dsc">{t("全部已启用的日常今日均已完成后，60 秒倒计时关机。")}</div>
              <div className="ctrl">
                <Toggle
                  checked={batch.shutdown}
                  disabled={locked}
                  label={t("完成日常后自动关机")}
                  onChange={(value) => void core.act(() => core.setConfig(batch.name, "完成日常后自动关机", value))}
                />
              </div>
            </div>
            {locked && <div className="hidden-note">{t("运行中，这两个开关已锁定。")}</div>}
          </div>
        </Section>
      )}
      <Section title={t("窗口与快捷键")}>
        <ConfigForm
          fields={pickFields(basic, SHELL_KEYS)}
          disabled={!desktop || core.busy}
          saveField={save}
        >
          <div className="hidden-note">{t("关闭到托盘时任务继续运行；从托盘菜单选择退出才会停止任务。在其他窗口中也可以用快捷键开始或暂停任务。")}</div>
          {!shell?.desktop && <div className="ferr">{t("当前版本不支持这些设置，请更新客户端。")}</div>}
          {desktopError && <div className="ferr" role="alert">{desktopError}</div>}
          {desktop?.syncError && <div className="ferr" role="alert">{desktop.syncError}</div>}
          {desktop?.hotkeyError && <div className="ferr" role="alert">{desktop.hotkeyError}</div>}
          {desktop && !desktop.hotkeyError && <div className="hidden-note">
            {desktop.registeredHotkey ? t("快捷键已启用：{key}", { key: desktop.registeredHotkey }) : t("全局快捷键已关闭")}
          </div>}
          {desktop && !desktop.trayAvailable && <div className="ferr">{t("系统托盘不可用，关闭窗口将退出客户端。")}</div>}
        </ConfigForm>
      </Section>
      <Section title={t("识别与性能")}>
        <ConfigForm fields={otherFields(basic, [...DEVICE_KEYS, AUTO_RESIZE_KEY, ...SHELL_KEYS])} disabled={false} saveField={save} />
      </Section>
      <McpSettings core={core} />
      <p className="why">{t("改完即保存。游戏启动、退出和窗口相关的选项在「设备」页。")}</p>
    </>
  );
}

function Notify({ core }: { core: Core }) {
  const group = findGroup(core.settings ?? [], NOTIFICATION);
  const [sending, setSending] = useState<string | null>(null);
  const save = (key: string, value: unknown) => core.setSetting(NOTIFICATION, key, value);
  const test = (channel: string) => {
    setSending(channel);
    core
      .act(async () => {
        await api.testNotification(channel);
        core.notify(t("测试消息已发送"));
      })
      .finally(() => setSending(null));
  };
  return (
    <>
      <Section title={t("系统通知")}>
        <ConfigForm fields={pickFields(group, [SYSTEM_NOTIFICATION_KEY])} disabled={false} saveField={save}>
          <div className="hidden-note">{t("系统通知由客户端托盘发送；点击通知可恢复窗口。显示效果遵循 Windows 通知设置。")}</div>
          <button type="button" className="btn sm" disabled={sending !== null || !window.okbd2Shell?.desktop}
            onClick={() => test("system")}>
            {sending === "system" ? t("正在发送…") : t("测试系统通知")}
          </button>
        </ConfigForm>
      </Section>
      <Section title={t("发到手机或群聊")}>
        <ConfigForm
          fields={otherFields(group, [SYSTEM_NOTIFICATION_KEY])}
          disabled={false}
          saveField={save}
          extra={(field) =>
            TEST_CHANNELS[field.key] && field.value ? (
              <button type="button" className="btn sm" disabled={sending !== null} onClick={() => test(TEST_CHANNELS[field.key])}>
                <Icon name="send" />
                {sending === TEST_CHANNELS[field.key] ? t("正在发送…") : t("发送测试消息")}
              </button>
            ) : null
          }
        >
        </ConfigForm>
      </Section>
      <p className="why">{t("打开一个渠道后才显示它的地址和令牌；令牌默认遮住，离开输入框时保存。")}</p>
    </>
  );
}

function About({ core }: { core: Core }) {
  const [about, setAbout] = useState<AboutInfo | null>(null);
  const [shell, setShell] = useState<string | null>(null);
  const [check, setCheck] = useState<UpdateCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const [confirmUpdate, setConfirmUpdate] = useState(false);
  const [applying, setApplying] = useState(false);
  useEffect(() => {
    api.about().then(setAbout, () => undefined);
    window.okbd2Shell?.info().then((info) => setShell(info?.shellVersion ?? null), () => undefined);
  }, []);
  const launcher = core.info?.launcher;
  const launcherGroup = findGroup(core.settings ?? [], LAUNCHER);
  const links = about?.links?.default ?? {};
  const runCheck = () => {
    setChecking(true);
    setCheckError(null);
    api.updates().then(setCheck, (reason: unknown) => setCheckError(errorText(reason))).finally(() => setChecking(false));
  };
  const latest = check?.versions[0];
  let update;
  if (checking) {
    update = <span className="dsc">{t("正在检查…")}</span>;
  } else if (checkError) {
    update = (
      <>
        <span className="ferr">{t("检查失败：{reason}", { reason: checkError })}</span>
        <button type="button" className="btn sm" onClick={runCheck}>
          {t("再试一次")}
        </button>
      </>
    );
  } else if (check && check.update_available && latest) {
    update = (
      <>
        <b>
          {t("有新版本 {version}", { version: latest.version })}
        </b>
        <button type="button" className="linkbtn" aria-expanded={notesOpen} onClick={() => setNotesOpen(!notesOpen)}>
          {t("更新说明")}
        </button>
        <button type="button" className="btn sm primary" disabled={applying || !about?.update_supported}
          onClick={() => setConfirmUpdate(true)}>
          {applying ? t("正在准备更新…") : t("更新到 {version}", { version: latest.version })}
        </button>
      </>
    );
  } else if (check) {
    update = (
      <>
        <span>{t("已是最新版本")}</span>
        <button type="button" className="btn sm" onClick={runCheck}>
          {t("再检查一次")}
        </button>
      </>
    );
  } else {
    update = (
      <button type="button" className="btn sm" onClick={runCheck}>
        {t("检查更新")}
      </button>
    );
  }
  return (
    <>
      <Section title={t("版本")}>
        <div className="card form">
          <div className="fld">
            <div className="lb">ok-bd2</div>
            <div className="dsc">{t("脚本与任务")}</div>
            <div className="ctrl mono">{core.info ? versionText(core.info.core_version) : "…"}</div>
          </div>
          <div className="fld">
            <div className="lb">{t("客户端")}</div>
            <div className="dsc">{t("窗口与界面")}</div>
            <div className="ctrl mono">{shell ? versionText(shell) : "—"}</div>
          </div>
          <div className="fld">
            <div className="lb">{t("启动器")}</div>
            <div className="dsc">{t("负责安装和更新")}</div>
            <div className="ctrl mono">
              {launcher?.version ? versionText(launcher.version) : t("未知（源码运行）")}
              {launcher?.outdated && <span className="chip warn">{t("过旧")}</span>}
            </div>
          </div>
          <div className="fld">
            <div className="lb">{t("更新")}</div>
            <div className="dsc">
              {check?.update_available
                ? t("先停止任务并保存数据，备份成功后交给启动器更新。")
                : t("通过当前安装的启动器检查更新。")}
            </div>
            <div className="ctrl">{update}</div>
            {notesOpen && latest && (
              <ul className="notes">
                {latest.notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Section>
      {launcherGroup && (
        <Section title={t("启动器")}>
          <ConfigForm
            fields={launcherGroup.fields}
            disabled={false}
            saveField={(key, value) => core.setSetting(LAUNCHER, key, value)}
          >
            <button type="button" className="btn sm" onClick={() => void core.act(api.openLauncher)}>
              <Icon name="link" />
              {t("打开启动器")}
            </button>
          </ConfigForm>
        </Section>
      )}
      {confirmUpdate && latest && <ConfirmDialog title={t("更新到 {version}？", { version: latest.version })}
        confirm={t("停止任务并更新")} onCancel={() => setConfirmUpdate(false)} onConfirm={() => {
          setConfirmUpdate(false); setApplying(true);
          void core.act(() => api.applyUpdate(latest.version)).then((ok) => { if (!ok) setApplying(false); });
        }}>
        <p className="why">{t("当前任务会停止，配置与进度将先备份。客户端随后关闭，由启动器完成更新；游戏不会被关闭。")}</p>
      </ConfirmDialog>}
      <Backups core={core} />
      <Section title={t("关于")}>
        <div className="card pad">
          <p>
            <b>{t("本软件免费、开源")}</b>
            {t("，用于学习 Python、计算机视觉与界面自动化。请在了解对账号和游戏客户端的风险后再使用自动化。")}
          </p>
          <p className="dsc">{t("本软件使用 MiSans 字体。MiSans © 小米科技有限责任公司，许可协议随附于 assets/fonts/LICENSE.txt。")}</p>
          <div className="row wrap">
            {links.github && (
              <a href={links.github} target="_blank" rel="noreferrer">
                GitHub
              </a>
            )}
            {links.download && (
              <a href={links.download} target="_blank" rel="noreferrer">
                {t("下载页")}
              </a>
            )}
            {links.faq && links.faq !== links.github && (
              <a href={links.faq} target="_blank" rel="noreferrer">
                {t("常见问题")}
              </a>
            )}
            <a href={NOTICES_URL} target="_blank" rel="noreferrer">
              {t("第三方许可")}
            </a>
          </div>
          <p className="why">
            {t("基于")} <a href={OK_SCRIPT_URL} target="_blank" rel="noreferrer">ok-script</a>{t(" 开发。")}
          </p>
        </div>
      </Section>
      <Section title={t("文件夹")}>
        <div className="card pad row wrap">
          <button type="button" className="btn sm" onClick={() => void core.act(() => core.tool("install-folder"))}>
            <Icon name="folder" />
            {t("安装文件夹")}
          </button>
          <button type="button" className="btn sm" onClick={() => void core.openLogsFolder()}>
            <Icon name="folder" />
            {t("日志")}
          </button>
          <button type="button" className="btn sm" onClick={() => void core.act(() => core.tool("screenshot-folder"))}>
            <Icon name="folder" />
            {t("截图")}
          </button>
        </div>
      </Section>
    </>
  );
}

function Backups({ core }: { core: Core }) {
  const [data, setData] = useState<BackupList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState<BackupEntry | null>(null);
  const refresh = () => api.backups().then((value) => { setData(value); setError(null); },
    (reason) => setError(errorText(reason)));
  useEffect(() => { void refresh(); }, []);
  return <Section title={t("配置备份与恢复")}>
    <div className="card pad">
      <p className="dsc">{t("包含任务设置、方案、任务进度和运行记录；保留最近 5 份。更新后第一次打开时会自动备份。")}</p>
      <div className="row wrap">
        <button className="btn sm" disabled={busy || core.busy || !data?.supported} onClick={() => {
          setBusy(true);
          void core.act(async () => { await api.createBackup(); await refresh(); core.notify(t("备份已保存，任务保持暂停。")); })
            .finally(() => setBusy(false));
        }}>{busy ? t("正在处理…") : t("立即备份")}</button>
        <button className="btn sm" disabled={busy} onClick={() => void refresh()}>{t("刷新列表")}</button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
      {data && !data.supported && <p className="why">{t("当前使用自定义外部配置目录，请在对应目录自行管理备份。")}</p>}
      {data?.items.length === 0 && <p className="why">{t("还没有备份。")}</p>}
      {data?.items.map((item) => <div className="fld" key={item.id}>
        <div className="lb">{item.created_at ? new Date(item.created_at).toLocaleString() : item.id}</div>
        <div className="dsc">{item.invalid ? t("校验失败，不能恢复") :
          `${t("版本 {version}", { version: versionText(item.version ?? "") })} · ${t("{count} 个文件", { count: item.file_count ?? 0 })}`}</div>
        <div className="ctrl"><button className="btn sm" disabled={busy || item.invalid || item.version !== data.version}
          onClick={() => setRestoring(item)}>{t("恢复")}</button></div>
      </div>)}
      <p className="why">{t("恢复会覆盖备份之后的设置与进度，恢复前会再备份当前数据；完成后请退出客户端，再从启动器重新打开。只能恢复当前版本生成的备份。")}</p>
      <p className="why">{t("启动器的重新安装或切换国内/海外配置可能删除现有配置，请事先备份。")}</p>
    </div>
    {restoring && <ConfirmDialog title={t("恢复这份备份？")} confirm={t("停止任务并恢复")}
      onCancel={() => setRestoring(null)} onConfirm={() => {
        const id = restoring.id; setRestoring(null); setBusy(true);
        void core.act(() => api.restoreBackup(id)).then((ok) => { if (!ok) setBusy(false); });
      }}><p className="why">{t("当前设置、任务进度与运行记录会被备份内容整体替换，之后新增的进度不会保留。完成后请重新启动客户端。")}</p>
    </ConfirmDialog>}
  </Section>;
}

export function SettingsPage({ core }: { core: Core }) {
  const [tab, setTab] = useState<Tab>("general");
  const [resetting, setResetting] = useState<string | null>(null);
  const strip = useSlider<HTMLDivElement>('[aria-selected="true"]', tab);
  const tabs: [Tab, string][] = [
    ["general", t("通用")],
    ["notify", t("通知")],
    ["about", t("关于与更新")],
  ];
  const group = tab === "general" ? BASIC : tab === "notify" ? NOTIFICATION : null;
  const label = group ? findGroup(core.settings ?? [], group)?.label ?? group : "";
  return (
    <div className="page settings-page">
      <PageHead title={t("设置")}>
        {group && (
          <Menu
            items={[
              {
                label: t("把「{name}」恢复默认…", { name: coreText(label) }),
                icon: "refresh",
                onSelect: () => setResetting(group),
              },
            ]}
          />
        )}
      </PageHead>
      <div className="tabs slides" ref={strip} role="tablist">
        {tabs.map(([value, text]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>
            {text}
          </button>
        ))}
      </div>
      <div className="setbody" role="tabpanel">
        {core.settings === null && tab !== "about" ? (
          <div className="card empty">{t("正在读取设置…")}</div>
        ) : tab === "general" ? (
          <General core={core} />
        ) : tab === "notify" ? (
          <Notify core={core} />
        ) : (
          <About core={core} />
        )}
      </div>
      {resetting && (
        <ConfirmDialog
          title={t("把「{name}」全部恢复默认？", { name: coreText(findGroup(core.settings ?? [], resetting)?.label ?? resetting) })}
          confirm={t("恢复默认")}
          onCancel={() => setResetting(null)}
          onConfirm={() => {
            const name = resetting;
            setResetting(null);
            void core.act(() => core.resetSettings(name));
          }}
        >
          <p className="why">
            {resetting === NOTIFICATION
              ? t("所有通知渠道会关闭，填写的地址和令牌会清空。")
              : t("包括「设备」页里的游戏启动与退出选项。")}
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
