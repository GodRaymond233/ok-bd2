import type { LanguagePreference } from "./i18n.ts";

/** ok-script's form field description (ok/core/config_schema.py build_config_fields). */
export interface ConfigField {
  key: string;
  value: unknown;
  default: unknown;
  description: string;
  kind: "boolean" | "integer" | "number" | "select" | "multi_selection" | "list" | "text" | "multiline" | "file";
  options: unknown[] | null;
  allow_duplication: boolean;
  minimum: number | null;
  maximum: number | null;
  /** Shown under a parent switch (sub_configs). */
  sub_config: boolean;
  /** Set when only a dedicated editor may change the value (the quick-hunt plan). */
  editor?: string;
  /** The value as that editor describes it. */
  editor_title?: string;
  editor_summary?: string;
  /** Display name and option names for settings the core keys in English. */
  label?: string;
  option_labels?: Record<string, string>;
  /** A token or webhook address: masked until asked. */
  secret?: boolean;
}

/** A global settings group (/api/settings, named by src/desktop/settings_text.py). */
export interface SettingsGroup {
  name: string;
  label: string;
  description: string;
  top_level: boolean;
  fields: ConfigField[];
}

/** Result of a task's current or last run (src/tasks/run_history.py run_state). */
export type RunState = "run" | "pause" | "done" | "warn" | "abort" | "fail";

export interface TaskPayload {
  name: string;
  class_name: string;
  enabled: boolean;
  running: boolean;
  paused: boolean;
  trigger: boolean;
  description: string;
  visible: boolean;
  group_name: string | null;
  waiting_for: string | null;
  start_time: number;
  info: Record<string, unknown>;
  config_busy?: boolean;
  run_state?: RunState;
  config?: ConfigField[];
  hidden_config_count?: number;
  instructions?: string | null;
  guide?: string | null;
  /** The illustrated guide, read with api.guide. */
  guide_id?: string | null;
  /** What the task reports while it runs, under the names it gives them. */
  status_rows?: StatusRow[];
}

export interface StatusRow {
  key: string;
  label: string;
  value: string;
}

export interface Guide {
  id: string;
  title: string;
  summary: string;
  markdown: string;
}

export interface ToolState {
  run_id: number;
  kind: "code" | "ocr" | "check" | null;
  active: boolean;
  stopping: boolean;
  output: string;
  truncated: boolean;
  code: string;
  task_class: string;
  image_url: string | null;
  result: {
    value: string | null;
    error: string | null;
    cancelled: boolean;
    duration: number;
    details: {
      checks?: { name: string; ok: boolean; detail: string }[];
      boxes?: { text: string; x: number; y: number; width: number; height: number; confidence: number }[];
      width?: number;
      height?: number;
    } | null;
  } | null;
}

export interface McpState {
  enabled: boolean;
  requested: boolean;
  url: string | null;
  connection_file: string | null;
  error: string | null;
}

export interface ScriptEntry {
  name: string;
  modified: number;
  /** The task it defines, once it loads. */
  task: string | null;
  error: boolean;
}

export interface ScriptDocument {
  name: string;
  code: string;
  modified: number;
  task: string | null;
  error: string | null;
  error_line: number | null;
}

/** A BaseTask method offered in the editor's palette (ok/core/script_templates.py). */
export interface ScriptHelper {
  name: string;
  params: { name: string; default: string | null; doc: string }[];
  doc: string;
  full_doc: string;
  category: string;
  class_name: string;
  is_static: boolean;
}

export interface ExportOptions {
  tasks: string[];
  manifest: { file_name?: string; script_name?: string; version?: string };
}

export interface RecordingState {
  recording: boolean;
  /** The game window is in front, so clicks count. */
  active: boolean;
  clicks: number;
  target: string | null;
}

export interface TemplateImage {
  name: string;
  url: string;
  modified: number;
  categories: string[];
}

export interface TemplatesInfo {
  debug: boolean;
  generate_label_enum: boolean;
  enum_path: string;
}

export interface TemplateBox {
  id?: number;
  category: string;
  bbox: [number, number, number, number];
}

export interface TemplateMarkup {
  name: string;
  url: string;
  width: number;
  height: number;
  annotations: TemplateBox[];
}

export interface LanguageState {
  language: LanguagePreference;
  catalogs: Record<string, Record<string, string>>;
}

export interface LastRun {
  finished: number;
  duration: number | null;
  ok: boolean;
  status: string;
}

export interface TodayItem {
  /** The child's switch on 一键完成日常; also the name the batch reports progress by. */
  key: string;
  name: string;
  enabled: boolean;
  done: boolean;
  completion: "today" | "quota" | "week" | null;
  weekly: boolean;
  last: LastRun | null;
}

export interface SkipReason {
  reason: "off" | "stopped" | "done" | "not_due" | "settlement";
  minutes: number | null;
}

export interface TodaySummary {
  batch: { name: string; enabled: boolean; auto_start: boolean; shutdown: boolean } | null;
  items: TodayItem[];
  skipped: Record<string, SkipReason>;
  refresh: { daily: number; collection_quota: number; collection_week: number };
}

export interface HuntStep {
  target: string;
  mode: string;
  count?: number;
}

export interface HuntPlan {
  name: string;
  reserve?: number;
  rules: { condition: string; steps: HuntStep[] }[];
  fallback: HuntStep[];
}

export interface HuntBook {
  version: number;
  active: number;
  plans: HuntPlan[];
}

export interface HuntPlans {
  book: HuntBook | null;
  load_error: string | null;
  inherited: boolean;
  revision: string;
  presets: HuntPlan[];
  conditions: Record<string, string>;
  targets: Record<string, string>;
  modes: Record<string, string>;
  busy: boolean;
}

export interface HistoryRun {
  task: string;
  started: number | null;
  finished: number;
  duration: number | null;
  ok: boolean;
  status: string;
  details?: Record<string, string>;
}

export interface ReportState {
  session_id: string | null;
  snapshot: {
    captured_at: string;
    task: Record<string, unknown>;
    warnings: string[];
    was_running: boolean;
  } | null;
  can_resume: boolean;
  result: { id: string; path: string; message: string } | null;
}

export interface ExecutorStatus {
  paused: boolean;
  running: boolean;
  starting: boolean;
  current_task: string | null;
  task_count: number;
  hotkey: string | null;
}

export interface Choice {
  id: string;
  label: string;
  selected: boolean;
}

export interface Device {
  id: string;
  label: string;
  kind: string;
  connected: boolean;
  selected?: boolean;
}

export interface CaptureUi {
  title: string;
  version: string;
  event_session_key: string;
  status: ExecutorStatus;
  devices: Device[];
  capture_methods: Choice[];
  /** Chosen capture method; null when the core tries capture_methods in order. */
  capture_preference: string | null;
  /** Capture method running for the connected game window, if any. */
  capture_in_use: string | null;
  interaction_methods: Choice[];
  /** The Starter wait while the game starts (src/desktop/starter_wait.py). */
  starter_wait?: StarterWait | null;
  diagnostic_pause?: boolean;
  maintenance?: boolean;
  tools_busy?: boolean;
  overlay?: { boxes: boolean };
}

export interface StarterWait {
  /** What the Starter is doing (updating, the install wizard); empty while just waiting. */
  message: string;
  /** Seconds before the wait gives up; 0 while the Starter is updating. */
  seconds_left: number;
  waited: number;
}

export interface WindowInfo {
  size: [number, number] | null;
  ratio_ok: boolean;
  resolutions: [number, number][];
  default: [number, number];
  resizing: boolean;
}

export interface GamePaths {
  game: string;
  launcher: string;
}

export interface UpdateCheck {
  current_version: string;
  versions: { version: string; notes: string[] }[];
  update_available: boolean;
}

export interface AboutInfo {
  version: string;
  links: Record<string, Record<string, string>>;
  update_supported: boolean;
}

export interface BackupEntry {
  id: string;
  invalid?: boolean;
  created_at?: string;
  version?: string;
  reason?: string;
  file_count?: number;
  bytes?: number;
}

export interface BackupList { items: BackupEntry[]; version: string; busy: boolean; supported: boolean }

export interface DesktopInfo {
  api: number;
  core_version: string;
  pid: number;
  mode: string;
  ui_built: boolean;
  /** The script editor and template pages are available. */
  custom_scripts?: boolean;
  legacy_instances: { pid: number; location: string; description: string }[];
  /** PyAppify launcher; outdated is null when the version is unknown (a source checkout). */
  launcher?: { version: string | null; outdated: boolean | null };
}

export interface EventPayload {
  event: string;
  args?: unknown[];
  kwargs?: Record<string, unknown>;
  seq?: number;
  ui?: CaptureUi | null;
  resync?: boolean;
}

export interface ShellInfo {
  shellVersion: string;
  electron: string;
  api: { min: number; max: number };
}

export type Theme = "system" | "light" | "dark";

export interface UiPreferences {
  theme?: Theme;
  dockExpanded?: boolean;
  railWidth?: number;
  taskListWidth?: number;
}

export interface ShellBridge {
  desktop(): Promise<ShellDesktop>;
  setZoom(zoom: number): Promise<ShellDesktop>;
  uiPreferences(): UiPreferences;
  setUiPreferences(change: UiPreferences): Promise<void>;
  info(): Promise<ShellInfo | null>;
  openLogsFolder(): Promise<void>;
  quit(): Promise<void>;
}

/** What the shell applied from the settings; a different thing from the core's DesktopInfo. */
export interface ShellDesktop {
  zoom: number;
  zoomLevels: number[];
  closeToTray: boolean;
  hotkey: string;
  registeredHotkey: string | null;
  hotkeyError: string | null;
  syncError: string | null;
  trayAvailable: boolean;
}
