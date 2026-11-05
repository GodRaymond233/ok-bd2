// A task's settings, generated from the core's field descriptions. Switches, lists and
// choices save at once; text and numbers save when the field loses focus or on Enter.
// A refused value shows the core's reason under the field and the field shows the saved value.
import { type KeyboardEvent, type ReactNode, useEffect, useState } from "react";
import { errorText } from "./api.ts";
import { fieldLabel, isModified, linesToList, numberStep, optionList, optionText, parseNumber, stepNumber, toggleChoice, valueText } from "./form.ts";
import { t } from "./i18n.ts";
import type { ConfigField } from "./types.ts";
import { Icon, Toggle } from "./ui.tsx";

export type SaveField = (key: string, value: unknown) => Promise<void>;

function Control({
  field,
  disabled,
  save,
  setError,
}: {
  field: ConfigField;
  disabled: boolean;
  save: (value: unknown) => void;
  setError: (message: string | null) => void;
}) {
  const textValue = field.kind === "list" && !field.options ? (Array.isArray(field.value) ? field.value.join("\n") : "") : String(field.value ?? "");
  const [draft, setDraft] = useState(textValue);
  const [revealed, setRevealed] = useState(false);
  useEffect(() => setDraft(textValue), [textValue]);
  const label = fieldLabel(field);
  const commitText = () => {
    if (draft === textValue) {
      return;
    }
    if (field.kind === "integer" || field.kind === "number") {
      const parsed = parseNumber(draft, field);
      if (typeof parsed === "string") {
        setError(parsed);
        setDraft(textValue);
        return;
      }
      save(parsed);
    } else if (field.kind === "list") {
      save(linesToList(draft));
    } else {
      save(draft);
    }
  };
  const onEnter = (event: KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault();
      (event.target as HTMLElement).blur();
    }
  };

  if (field.editor) {
    return <span className="why">{field.value ? t("已设置方案") : t("未设置方案（按默认规则）")}</span>;
  }
  switch (field.kind) {
    case "boolean":
      return <Toggle checked={Boolean(field.value)} disabled={disabled} label={label} onChange={save} />;
    case "select":
      return (
        <select className="select" value={String(field.value)} disabled={disabled} aria-label={label} onChange={(event) => save(event.target.value)}>
          {optionList(field).map((option) => (
            <option key={option} value={option}>
              {optionText(field, option)}
            </option>
          ))}
        </select>
      );
    case "multi_selection":
    case "list":
      if (field.options) {
        const chosen = Array.isArray(field.value) ? field.value.map(String) : [];
        return (
          <span className="chips" role="group" aria-label={label}>
            {optionList(field).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={chosen.includes(option)}
                disabled={disabled}
                onClick={() => save(toggleChoice(field.value, option))}
              >
                {chosen.includes(option) && <Icon name="check" />}
                {optionText(field, option)}
              </button>
            ))}
          </span>
        );
      }
      return (
        <textarea
          className="input"
          value={draft}
          disabled={disabled}
          aria-label={label}
          placeholder={t("每行一项")}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitText}
        />
      );
    case "integer":
    case "number":
      return (
        <>
          <span className="numbox">
            <button
              type="button"
              disabled={disabled || (field.minimum !== null && Number(field.value) <= field.minimum)}
              aria-label={`${label} ${t("减少")}`}
              onClick={() => save(stepNumber(field.value, field, -1))}
            >
              <Icon name="minus" />
            </button>
            <input
              inputMode="decimal"
              value={draft}
              disabled={disabled}
              aria-label={label}
              step={numberStep(field)}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={commitText}
              onKeyDown={onEnter}
            />
            <button
              type="button"
              disabled={disabled || (field.maximum !== null && Number(field.value) >= field.maximum)}
              aria-label={`${label} ${t("增加")}`}
              onClick={() => save(stepNumber(field.value, field, 1))}
            >
              <Icon name="plus" />
            </button>
          </span>
          {(field.minimum !== null || field.maximum !== null) && (
            <span className="range">
              {field.minimum ?? "…"}–{field.maximum ?? "…"}
            </span>
          )}
        </>
      );
    case "multiline":
      return (
        <textarea
          className="input"
          value={draft}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitText}
        />
      );
    default:
      if (field.secret) {
        return (
          <span className="secret">
            <input
              className="input"
              type={revealed ? "text" : "password"}
              value={draft}
              disabled={disabled}
              aria-label={label}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={commitText}
              onKeyDown={onEnter}
            />
            <button type="button" className="linkbtn" onClick={() => setRevealed(!revealed)}>
              {revealed ? t("隐藏") : t("显示")}
            </button>
          </span>
        );
      }
      return (
        <input
          className="input"
          value={draft}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitText}
          onKeyDown={onEnter}
        />
      );
  }
}

function Field({
  field,
  disabled,
  saveField,
  extra,
  note,
}: {
  field: ConfigField;
  disabled: boolean;
  saveField: SaveField;
  extra?: ReactNode;
  note?: ReactNode;
}) {
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(0);
  const save = (value: unknown) => {
    setError(null);
    saveField(field.key, value).then(
      () => setSaved(Date.now()),
      (reason: unknown) => setError(errorText(reason)),
    );
  };
  const modified = isModified(field);
  return (
    <div className={field.sub_config ? "fld sub" : "fld"}>
      <div className="lb">
        {fieldLabel(field)}
        {note}
        {modified && !field.editor && (
          <>
            <span className="mod" title={t("与默认值不同")} />
            <button type="button" className="reset" disabled={disabled} onClick={() => save(field.default)}>
              {t("恢复默认")}（{field.secret ? t("空") : field.option_labels ? optionText(field, String(field.default)) : valueText(field.default)}）
            </button>
          </>
        )}
        {saved > 0 && (
          <span key={saved} className="saved" role="status">
            <Icon name="check" />
            {t("已保存")}
          </span>
        )}
      </div>
      {field.description && <div className="dsc">{field.description}</div>}
      {field.editor && <div className="dsc">{t("方案在编辑器中确认保存后生效。")}</div>}
      <div className="ctrl">
        {extra}
        <Control field={field} disabled={disabled} save={save} setError={setError} />
      </div>
      {error && (
        <div className="ferr" role="alert">
          <Icon name="alert" />
          {error}
        </div>
      )}
    </div>
  );
}

export function ConfigForm({
  fields,
  hidden = 0,
  disabled,
  saveField,
  extra,
  note,
  children,
}: {
  fields: ConfigField[];
  hidden?: number;
  disabled: boolean;
  saveField: SaveField;
  /** Something to put beside a field's control, such as a test button. */
  extra?: (field: ConfigField) => ReactNode;
  /** Something to put after a field's name, such as a stage tag. */
  note?: (field: ConfigField) => ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="card form">
      {fields.length === 0 && !children && <div className="empty">{t("这个任务没有可修改的设置。")}</div>}
      {fields.map((field) => (
        <Field
          key={field.key}
          field={field}
          disabled={disabled}
          saveField={saveField}
          extra={extra?.(field)}
          note={note?.(field)}
        />
      ))}
      {children}
      {hidden > 0 && (
        <div className="hidden-note">
          <Icon name="info" />
          {t("已隐藏")} {hidden} {t("项识别与维护参数（阈值、等待秒数、测试按钮等），与 Qt 版一致")}
        </div>
      )}
    </div>
  );
}
