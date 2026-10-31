// 设置: 通用, 通知, 关于与更新. Fields come from ok-script's global settings, named by the
// core; the game start and exit options are on the device page.
import { type ReactNode, useEffect, useState } from "react";
import { api } from "./api.ts";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { t } from "./i18n.ts";
import {
  AUTO_RESIZE_KEY,
  BASIC,
  DEVICE_KEYS,
  findGroup,
  NOTIFICATION,
  otherFields,
  pickFields,
  SHELL_KEYS,
  SYSTEM_NOTIFICATION_KEY,
  TEST_CHANNELS,
  type Theme,
  applyTheme,
  storedTheme,
} from "./settings.ts";
import type { AboutInfo, UpdateCheck } from "./types.ts";
import { ConfirmDialog, Icon, Menu, Toggle } from "./ui.tsx";

type Tab = "general" | "notify" | "about";
const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));
const NOTICES_URL = "https://github.com/GodRaymond233/ok-bd2/blob/main/THIRD_PARTY_NOTICES.md";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="setsec">
      <h2 className="gtitle">{title}</h2>
      {children}
    </section>
  );
}

function General({ core }: { core: Core }) {
  const [theme, setTheme] = useState<Theme>(storedTheme);
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
            <div className="dsc">{t("只影响这台电脑上的新客户端。")}</div>
            <div className="ctrl">
              <span className="segpick" role="group" aria-label={t("主题")}>
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
            <div className="lb">
              {t("界面缩放")} <span className="chip">{t("阶段 4")}</span>
            </div>
            <div className="dsc">{t("现在跟随 Windows 的缩放设置；单独调整和高 DPI 一起在阶段 4 做。")}</div>
          </div>
        </div>
      </Section>
      {batch && (
        <Section title={t("日常")}>
          <div className="card form">
            <div className="fld">
              <div className="lb">{t("启动后自动执行剩余日常")}</div>
              <div className="dsc">{t("与「今日」页和一键完成日常设置里的是同一个开关。")}</div>
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
          disabled
          saveField={save}
          note={() => <span className="chip">{t("阶段 4")}</span>}
        >
          <div className="hidden-note">{t("托盘和全局快捷键由新客户端的外壳实现，阶段 4 接上；在此之前关闭窗口就是退出客户端。")}</div>
        </ConfigForm>
      </Section>
      <Section title={t("识别与性能")}>
        <ConfigForm fields={otherFields(basic, [...DEVICE_KEYS, AUTO_RESIZE_KEY, ...SHELL_KEYS])} disabled={false} saveField={save} />
      </Section>
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
          <div className="hidden-note">{t("当前由自动化核心发送 Windows 通知，可能出现额外的托盘图标；阶段 4 将统一由新客户端发送。")}</div>
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
          <div className="hidden-note">
            {t("QQ、微信桌面通知不在这里：它们要操作你电脑上的 QQ / 微信窗口，ok-script 也标注它们不可靠。已经开启的照常工作，要关闭请用 Qt 版。")}
          </div>
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
  useEffect(() => {
    api.about().then(setAbout, () => undefined);
    window.okbd2Shell?.info().then((info) => setShell(info?.shellVersion ?? null), () => undefined);
  }, []);
  const launcher = core.info?.launcher;
  const links = about?.links?.default ?? {};
  const runCheck = () => {
    setChecking(true);
    setCheckError(null);
    api.updates().then(setCheck, (reason: unknown) => setCheckError(message(reason))).finally(() => setChecking(false));
  };
  const latest = check?.versions[0];
  let update;
  if (checking) {
    update = <span className="dsc">{t("正在检查…")}</span>;
  } else if (checkError) {
    update = (
      <>
        <span className="ferr">{t("检查失败")}：{checkError}</span>
        <button type="button" className="btn sm" onClick={runCheck}>
          {t("再试一次")}
        </button>
      </>
    );
  } else if (check && check.update_available && latest) {
    update = (
      <>
        <b>
          {t("有新版本")} {latest.version}
        </b>
        <button type="button" className="linkbtn" aria-expanded={notesOpen} onClick={() => setNotesOpen(!notesOpen)}>
          {t("更新说明")}
        </button>
        <button type="button" className="btn sm primary" disabled title={t("阶段 5")}>
          {t("更新到")} {latest.version}
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
            <div className="dsc">{t("自动化核心与任务")}</div>
            <div className="ctrl mono">{core.info ? `v${core.info.core_version}` : "…"}</div>
          </div>
          <div className="fld">
            <div className="lb">{t("新客户端")}</div>
            <div className="dsc">{t("外壳与页面")}</div>
            <div className="ctrl mono">{shell ? `v${shell}` : "—"}</div>
          </div>
          <div className="fld">
            <div className="lb">{t("启动器")}</div>
            <div className="dsc">{t("PyAppify，负责安装和更新")}</div>
            <div className="ctrl mono">
              {launcher?.version ? `v${launcher.version}` : t("未知（源码运行）")}
              {launcher?.outdated && <span className="chip warn">{t("过旧")}</span>}
            </div>
          </div>
          <div className="fld">
            <div className="lb">{t("更新")}</div>
            <div className="dsc">
              {check?.update_available
                ? t("现在请退出客户端后在启动器里更新；客户端里一键更新在阶段 5 接上（要先安全停止任务和游戏控制）。")
                : t("从 GitHub 发布页检查。")}
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
          <p className="why">{t("基于 ok-script 开发。")}</p>
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

export function SettingsPage({ core }: { core: Core }) {
  const [tab, setTab] = useState<Tab>("general");
  const [resetting, setResetting] = useState<string | null>(null);
  const tabs: [Tab, string][] = [
    ["general", t("通用")],
    ["notify", t("通知")],
    ["about", t("关于与更新")],
  ];
  const group = tab === "general" ? BASIC : tab === "notify" ? NOTIFICATION : null;
  const label = group ? findGroup(core.settings ?? [], group)?.label ?? group : "";
  return (
    <div className="page settings-page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("设置")}</h1>
        </div>
        {group && (
          <Menu
            items={[
              {
                label: `${t("把「")}${label}${t("」恢复默认")}…`,
                icon: "refresh",
                onSelect: () => setResetting(group),
              },
            ]}
          />
        )}
      </div>
      <div className="tabs" role="tablist">
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
          title={`${t("把「")}${findGroup(core.settings ?? [], resetting)?.label ?? resetting}${t("」全部恢复默认？")}`}
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
