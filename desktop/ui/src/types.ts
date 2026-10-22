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
