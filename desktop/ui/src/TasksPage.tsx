// 任务: the task library (grouped, searchable) and the selected task's detail with its settings.
import { useEffect, useState } from "react";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { modifiedFields, valueText } from "./form.ts";
import { durationText, whenText } from "./format.ts";
import { t } from "./i18n.ts";
import { checklistRows } from "./today.ts";
import { libraryGroups } from "./library.ts";
import type { HistoryRun, TaskPayload } from "./types.ts";
import { ConfirmDialog, Icon, Menu, Toggle } from "./ui.tsx";

function Marker({ core, task }: { core: Core; task: TaskPayload }) {
  if (task.trigger) {
    return <span className={task.enabled ? "chip ok" : "chip off"}>{task.enabled ? t("开") : t("关")}</span>;
  }
  if (task.enabled || core.current === task.name) {
    return (
      <span className="mk run" title={t("运行中")}>
        <Icon name="play" />
      </span>
    );
  }
  if (task.name === core.today?.batch?.name) {
    return <span className="chip info">{t("合辑")}</span>;
  }
  const item = core.today?.items.find((candidate) => candidate.name === task.name);
  if (item?.done) {
    return (
      <span className="mk ok" title={t("已完成")}>
        <Icon name="check" />
      </span>
    );
  }
  if (item?.last && !item.last.ok) {
    return (
      <span className="mk bad" title={t("上次未成功")}>
        <Icon name="alert" />
      </span>
    );
  }
  return null;
}

export function TasksPage({
  core,
  selected,
  detailOpen,
  onSelect,
  onBack,
}: {
  core: Core;
  selected: string | null;
  detailOpen: boolean;
  onSelect: (name: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState("");
  const groups = libraryGroups(core.tasks, query);
  const task = core.tasks.find((candidate) => candidate.name === selected) ?? groups[0]?.tasks[0];
  return (
    <div className={detailOpen ? "lib detail-open" : "lib"}>
      <nav className="libcol" aria-label={t("任务库")}>
        <label className="search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            placeholder={t("搜索任务名称或说明")}
            aria-label={t("搜索任务")}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {groups.map((group) => (
          <div key={group.name}>
            <div className="grp">{group.name}</div>
            {group.tasks.map((item) => (
              <button
                key={item.name}
                type="button"
                className="ti"
                aria-current={item.name === task?.name}
                onClick={() => onSelect(item.name)}
              >
                <span className="nm">{item.name}</span>
                <Marker core={core} task={item} />
              </button>
            ))}
          </div>
        ))}
        {groups.length === 0 && <div className="libnote">{t("没有匹配的任务")}</div>}
        {core.tasks.some((item) => item.group_name === "测试") || <div className="libnote">{t("调试模式下另有「测试」组")}</div>}
      </nav>
      {task && <TaskDetail key={task.name} core={core} task={task} onBack={onBack} />}
    </div>
  );
}

function TaskDetail({ core, task, onBack }: { core: Core; task: TaskPayload; onBack: () => void }) {
  const [history, setHistory] = useState<HistoryRun[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const summary = core.today;
  const isBatch = task.name === summary?.batch?.name;
  const item = summary?.items.find((candidate) => candidate.name === task.name);
  const batch = summary?.batch ? core.tasks.find((candidate) => candidate.name === summary.batch?.name) : undefined;
  const locked = core.busy || Boolean(task.config_busy);
  const fields = task.config ?? [];
  const changed = modifiedFields(fields);

  const { history: loadHistory, historyVersion } = core;
  useEffect(() => {
    let cancelled = false;
    loadHistory(task.name, 3).then(
      (runs) => !cancelled && setHistory(runs),
      () => !cancelled && setHistory([]),
    );
    return () => {
      cancelled = true;
    };
  }, [loadHistory, task.name, historyVersion]);

  const row = item && summary ? checklistRows(summary, core.batchRun, core.childStage, core.now).find((candidate) => candidate.item === item) : undefined;
  const last = row ? row.text : history?.[0] ? `${whenText(history[0].finished, core.now)} ${history[0].ok ? t("完成") : t("未成功")}` : t("还没有运行记录");
  const lastBad = row ? row.tone === "bad" : history?.[0] ? !history[0].ok : false;
  const saveField = (key: string, value: unknown) => core.setConfig(task.name, key, value).then(() => undefined);

  return (
    <section className="detail" aria-label={task.name}>
      <button type="button" className="crumb back-only" onClick={onBack}>
        <Icon name="chev-l" />
        {t("任务")}
      </button>
      <div className="crumb wide-only">
        {t("任务")} <Icon name="chev-r" /> {task.name}
      </div>
      <div className="dhead">
        <div className="tt">
          <h1>
            {task.name}
            {isBatch && <span className="chip info">{t("合辑")}</span>}
            {task.trigger && <span className="chip ok">{t("触发任务")}</span>}
          </h1>
          {task.description && <div className="ds">{task.description}</div>}
          <div className="metas">
            {item && batch && (
              <label className="tgl">
                <Toggle
                  checked={item.enabled}
                  disabled={locked}
                  label={t("参与一键日常")}
                  onChange={(value) => void core.act(() => core.setConfig(batch.name, item.key, value))}
                />
                {t("参与一键日常")}
              </label>
            )}
            {!task.trigger && <span className={lastBad ? "last bad" : "last"}>{`${t("上次运行")} · ${last}`}</span>}
          </div>
        </div>
        <div className="go">
          {task.trigger ? (
            <label className="tgl">
              <Toggle
                checked={task.enabled}
                label={t("启用")}
                onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
              />
              {t("启用")}
            </label>
          ) : (
            <>
              <div>
                <button
                  type="button"
                  className="btn primary"
                  disabled={Boolean(core.startBlocked)}
                  onClick={() => void core.act(() => core.startTask(task.name))}
                >
                  <Icon name="play" />
                  {t("开始")}
                </button>
                {core.startBlocked && <div className="why">{core.startBlocked}</div>}
              </div>
              <Menu
                items={[
                  {
                    label: `${t("全部恢复默认")}…`,
                    note: changed.length ? `${t("改过")} ${changed.length} ${t("项")}` : t("都是默认值"),
                    icon: "refresh",
                    disabled: locked || changed.length === 0,
                    onSelect: () => setConfirming(true),
                  },
                ]}
              />
            </>
          )}
        </div>
      </div>
      {locked && (
        <div className="lockbar" role="status">
          <Icon name="lock" />
          <b>{core.ui?.diagnostic_pause ? t("问题报告期间，设置已锁定") : t("运行中，设置已锁定")}</b>
          <span>{core.current ? `${core.current} ${t("结束后可以修改。")}` : t("任务结束后可以修改。")}</span>
        </div>
      )}
      <div className="dgrid">
        <div>
          {isBatch && summary ? (
            <div className="card form">
              {summary.items.map((child, index) => (
                <div key={child.key} className="fld">
                  <div className="lb">
                    <span className="order">{index + 1}</span>
                    {child.name}
                  </div>
                  <div className="dsc">
                    {t("是否在一键完成日常中执行")}
                    {child.key}。
                  </div>
                  <div className="ctrl">
                    <Toggle
                      checked={child.enabled}
                      disabled={locked}
                      label={child.name}
                      onChange={(value) => void core.act(() => core.setConfig(task.name, child.key, value))}
                    />
                  </div>
                </div>
              ))}
              {fields
                .filter((field) => field.key === "启动自动执行日常" || field.key === "完成日常后自动关机")
                .map((field) => (
                  <div key={field.key} className="fld">
                    <div className="lb">{field.key}</div>
                    <div className="dsc">{field.description}</div>
                    <div className="ctrl">
                      <Toggle
                        checked={Boolean(field.value)}
                        disabled={locked}
                        label={field.key}
                        onChange={(value) => void core.act(() => core.setConfig(task.name, field.key, value))}
                      />
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <ConfigForm fields={fields} hidden={task.hidden_config_count ?? 0} disabled={locked} saveField={saveField} />
          )}
        </div>
        <div className="dside">
          {!task.trigger && (
            <div className="card sc">
              <div className="hd">
                <span className="bdg">
                  <Icon name="history" />
                </span>
                <b>{t("最近运行")}</b>
              </div>
              <div className="hist">
                {history === null && <div className="r">{t("读取中…")}</div>}
                {history?.length === 0 && <div className="r">{t("还没有运行记录")}</div>}
                {history?.map((run) => (
                  <div key={`${run.finished}`} className="r">
                    <span className={run.ok ? "d" : "d bad"} />
                    <span className="w">{whenText(run.finished, core.now)}</span>
                    <span className={run.ok ? "s" : "s bad"} title={run.status}>
                      {run.ok ? t("成功") : t("未成功")}
                      {run.duration ? ` · ${durationText(run.duration)}` : ""}
                    </span>
                    <button type="button" className="linkbtn" aria-label={`${t("查看这次的日志")} ${whenText(run.finished, core.now)}`} onClick={() => core.openLogs(run)}>
                      {t("日志")}
                    </button>
                  </div>
                ))}
              </div>
              <button className="linkbtn more" onClick={() => core.navigate("records")}>{t("查看全部运行记录")}</button>
            </div>
          )}
          {(task.guide || task.instructions) && (
            <div className="card sc">
              <div className="hd">
                <span className="bdg info">
                  <Icon name="info" />
                </span>
                <b>{t("使用说明")}</b>
              </div>
              <div className="l1">{task.guide ?? task.instructions}</div>
              {task.guide && <div className="l2">{t("图文说明在 Qt 版任务卡的「使用说明」里，新客户端稍后接入。")}</div>}
            </div>
          )}
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          title={`${t("把")}「${task.name}」${t("的设置全部恢复默认？")}`}
          confirm={t("恢复默认")}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            void core.act(() => core.resetTask(task.name));
          }}
        >
          {(task.hidden_config_count ?? 0) > 0 && (
            <p className="why">{t("隐藏的识别与维护参数也会一并恢复默认。")}</p>
          )}
          <ul>
            {changed.map((field) => (
              <li key={field.key}>
                {field.key}：{valueText(field.value)} → {valueText(field.default)}
              </li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
    </section>
  );
}
