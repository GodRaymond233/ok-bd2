// Small shared pieces: the line icon set, the switch, the sliding selection, the ⋯ menu and the
// confirm dialog.
import { type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";
import { t } from "./i18n.ts";
import { taskGlyph } from "./library.ts";
import { clampRailWidth, RAIL_WIDTH } from "./settings.ts";

// Fluent-style line icons on a 24 grid; play, pause and stop are filled.
const PATHS: Record<string, ReactNode> = {
  today: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
  tasks: <path d="M4 6.5l1.8 1.8L9 5M4 12.5l1.8 1.8L9 11M4 18.5l1.8 1.8L9 17M12.5 7h8M12.5 13h8M12.5 19h8" />,
  history: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  logs: <path d="M6 3.5h8.5l4 4v13H6zM14.5 3.5v4h4M9 12h6.5M9 15.5h6.5" />,
  device: (
    <>
      <rect x="3" y="4.5" width="18" height="12" rx="2" />
      <path d="M9 20h6M12 16.5V20" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  report: <path d="M5.5 21V4M5.5 4.5h11l-2 4 2 4h-11" />,
  play: <path d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.6-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8 0 0 0 8 5.2z" />,
  pause: (
    <>
      <rect x="6.5" y="5" width="3.8" height="14" rx="1.2" />
      <rect x="13.7" y="5" width="3.8" height="14" rx="1.2" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" rx="2.2" />,
  "chev-r": <path d="M9.5 6l6 6-6 6" />,
  "chev-l": <path d="M14.5 6l-6 6 6 6" />,
  "chev-d": <path d="M6 9.5l6 6 6-6" />,
  "chev-u": <path d="M6 14.5l6-6 6 6" />,
  more: (
    <>
      <circle cx="6" cy="12" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="18" cy="12" r="1.7" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  x: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  skip: <path d="M6 6.5l8 5.5-8 5.5zM17.5 6.5v11" />,
  alert: <path d="M12 4L21 19.5H3zM12 10v4.2M12 17v.1" />,
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2" />
      <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4.6-4.6" />
    </>
  ),
  power: <path d="M12 3.5v8M7 6.5a7.5 7.5 0 1 0 10 0" />,
  minus: <path d="M6 12h12" />,
  plus: <path d="M6 12h12M12 6v12" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5M12 8v.1" />
    </>
  ),
  login: <path d="M10 17l5-5-5-5M15 12H3.5M14 4h4.5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H14" />,
  map: <path d="M9 4.5l-5.5 2v13l5.5-2 6 2 5.5-2v-13l-5.5 2zM9 4.5v13M15 6.5v13" />,
  level: <path d="M3.5 17l6-6 4 4 7-7M15 8h5.5v5.5" />,
  folder: <path d="M3.5 6.5a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5v9.3a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z" />,
  refresh: <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4" />,
  download: <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" />,
  camera: (
    <>
      <path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.3l1.4-2h5.6l1.4 2h2.3A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />
      <circle cx="12" cy="13" r="3.5" />
    </>
  ),
  send: <path d="M4.5 12 19.5 5l-4 14-3.5-5.5zM12 13.5l7.5-8.5" />,
  bolt: <path d="M13 3.5 5.5 13.5H11l-1 7 7.5-10H12z" />,
  home: <path d="M4 11.5 12 5l8 6.5M6.5 9.8V19.5h11V9.8M10 19.5v-5h4v5" />,
  gift: <path d="M4 9h16v3.5H4zM5.5 12.5v7.5h13v-7.5M12 9v11M12 9S10.5 4.5 8 4.5A2.2 2.2 0 0 0 8 9zM12 9s1.5-4.5 4-4.5A2.2 2.2 0 0 1 16 9z" />,
  target: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="3.5" />
      <path d="M12 2v3.5M12 18.5V22M2 12h3.5M18.5 12H22" />
    </>
  ),
  star: <path d="M12 4l2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.6-.8z" />,
  coins: (
    <>
      <ellipse cx="12" cy="7" rx="7.5" ry="3.2" />
      <path d="M4.5 7v5c0 1.8 3.4 3.2 7.5 3.2s7.5-1.4 7.5-3.2V7M4.5 12v5c0 1.8 3.4 3.2 7.5 3.2s7.5-1.4 7.5-3.2v-5" />
    </>
  ),
  swords: <path d="M4 4l10 10M4 4h3.5L17 13.5M4 4v3.5L13.5 17M20 4 13.5 10.5M20 4h-3.5l-3 3M20 4v3.5l-3 3M14.5 17.5l3-3M6.5 14.5l3 3M6.5 17.5l-2.5 2.5M17.5 17.5l2.5 2.5" />,
  book: <path d="M12 6.5C10.3 5 7.8 4.5 4 4.5v14c3.8 0 6.3.5 8 2 1.7-1.5 4.2-2 8-2v-14c-3.8 0-6.3.5-8 2zM12 6.5v14" />,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
  tag: (
    <>
      <path d="M4 4.5h7.5l8.5 8.5-7 7-8.5-8.5z" />
      <path d="M8 8.5v.1" />
    </>
  ),
};

const FILLED = new Set(["play", "pause", "stop"]);

export function Icon({ name }: { name: string }) {
  return (
    <svg className={FILLED.has(name) ? "i f" : "i"} viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

/** A task's icon on a tile tinted by its category. */
export function TaskGlyph({ name, group, trigger }: { name: string; group?: string | null; trigger?: boolean }) {
  const glyph = taskGlyph(name, group ?? null, Boolean(trigger));
  return (
    <span className={`glyph ${glyph.category}`} aria-hidden="true">
      <Icon name={glyph.icon} />
    </span>
  );
}

/**
 * Keeps the host's selection highlight (its ::before, see .slides in styles.css) on the child
 * matching `selector`, so the highlight travels when `selection` changes instead of jumping.
 */
export function useSlider<T extends HTMLElement>(selector: string, selection: unknown): RefObject<T | null> {
  const host = useRef<T>(null);
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) {
      return;
    }
    const place = () => {
      const active = element.querySelector<HTMLElement>(selector);
      element.toggleAttribute("data-slide", Boolean(active));
      if (active) {
        // Measured against the host, as the item may sit in a nested group of its own.
        const from = element.getBoundingClientRect();
        const to = active.getBoundingClientRect();
        element.style.setProperty("--slide-x", `${to.left - from.left - element.clientLeft + element.scrollLeft}px`);
        element.style.setProperty("--slide-y", `${to.top - from.top - element.clientTop + element.scrollTop}px`);
        element.style.setProperty("--slide-w", `${to.width}px`);
        element.style.setProperty("--slide-h", `${to.height}px`);
      }
    };
    place();
    // Only moves animate: not the first placement, which comes before the first paint.
    const frame = requestAnimationFrame(() => element.toggleAttribute("data-slide-ready", true));
    const observer = new ResizeObserver(place);
    observer.observe(element);
    for (const child of element.querySelectorAll(":scope > *")) {
      observer.observe(child);
    }
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [selector, selection]);
  return host;
}

/** Calls `close` on a press outside `root` or on Escape, while `open`. */
export function useDismiss(open: boolean, root: RefObject<HTMLElement | null>, close: () => void) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node)) {
        close();
      }
    };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", dismiss);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", dismiss);
    };
  }, [open, root, close]);
}

/** Resize locally while dragging; persist only the finished gesture. */
export function RailDivider({ width, maximum, onResize, onCommit }: {
  width: number; maximum: number; onResize: (width: number) => void; onCommit: (width: number) => void;
}) {
  const drag = useRef<{ id: number; x: number; start: number; value: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const change = (value: number) => {
    const next = clampRailWidth(value, maximum);
    onResize(next);
    onCommit(next);
  };
  const finish = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current || drag.current.id !== event.pointerId) return;
    const value = clampRailWidth(drag.current.value, maximum);
    drag.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    onResize(value);
    onCommit(value);
  };
  return <div
    className="rail-divider"
    role="separator"
    tabIndex={0}
    aria-label={t("调整导航栏宽度")}
    aria-orientation="vertical"
    aria-controls="navigation-rail"
    aria-valuemin={RAIL_WIDTH.min}
    aria-valuemax={maximum}
    aria-valuenow={width}
    title={t("左右拖动调整导航栏宽度，双击恢复默认")}
    data-dragging={dragging || undefined}
    onPointerDown={(event) => {
      if (event.button !== 0 || !event.isPrimary) return;
      event.preventDefault();
      event.currentTarget.focus();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { id: event.pointerId, x: event.clientX, start: width, value: width };
      setDragging(true);
    }}
    onPointerMove={(event) => {
      if (!drag.current || drag.current.id !== event.pointerId) return;
      const next = clampRailWidth(drag.current.start + event.clientX - drag.current.x, maximum);
      drag.current.value = next;
      onResize(next);
    }}
    onPointerUp={finish}
    onPointerCancel={finish}
    onLostPointerCapture={finish}
    onDoubleClick={() => change(RAIL_WIDTH.default)}
    onKeyDown={(event) => {
      const choices: Record<string, number> = { ArrowLeft: width - 10, ArrowRight: width + 10, Home: RAIL_WIDTH.min, End: maximum };
      const next = choices[event.key];
      if (next === undefined) return;
      event.preventDefault();
      change(next);
    }}
  />;
}

export function Toggle({
  checked,
  disabled,
  label,
  title,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  title?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      className="tg"
      aria-checked={checked}
      aria-label={label}
      title={title}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onChange(!checked);
      }}
    />
  );
}

export interface MenuItem {
  label: string;
  note?: string;
  icon?: string;
  disabled?: boolean;
  onSelect: () => void;
}

/** The ⋯ button and its popover; closes on outside click or Escape. */
export function Menu({ items, label }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useDismiss(open, root, () => setOpen(false));
  return (
    <span className="menu" ref={root}>
      <button
        type="button"
        className="btn icon"
        aria-haspopup="menu"
        aria-expanded={open}
        title={label ?? t("更多")}
        aria-label={label ?? t("更多")}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(!open);
        }}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div className="pop" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={(event) => {
                event.stopPropagation();
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.icon && <Icon name={item.icon} />}
              {item.label}
              {item.note && <small>{item.note}</small>}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

export function ConfirmDialog({
  title,
  children,
  confirm,
  cancel,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirm: string;
  cancel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirmButton.current?.focus();
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onCancel();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onCancel]);
  return (
    <div className="scrim" onMouseDown={onCancel}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
        <h2>{title}</h2>
        {children}
        <div className="acts">
          <button type="button" className="btn" onClick={onCancel}>
            {cancel ?? t("取消")}
          </button>
          <button type="button" className="btn primary" ref={confirmButton} onClick={onConfirm}>
            {confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
