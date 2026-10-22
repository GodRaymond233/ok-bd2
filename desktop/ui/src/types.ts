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
  reason: "off" | "stopped" | "done" | "not_due";
  minutes: number | null;
}

export interface TodaySummary {
  batch: { name: string; enabled: boolean; auto_start: boolean; shutdown: boolean } | null;
  items: TodayItem[];
  skipped: Record<string, SkipReason>;
  refresh: { daily: number; collection_quota: number; collection_week: number };
}

export interface HistoryRun {
  task: string;
  started: number | null;
  finished: number;
  duration: number | null;
  ok: boolean;
  status: string;
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
}

export interface DesktopInfo {
  api: number;
  core_version: string;
  pid: number;
  mode: string;
  ui_built: boolean;
  legacy_instances: { pid: number; location: string; description: string }[];
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

export interface ShellBridge {
  info(): Promise<ShellInfo | null>;
  openLogsFolder(): Promise<void>;
  quit(): Promise<void>;
}
