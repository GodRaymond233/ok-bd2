"""卡带冷却管理；按服务器刷新点比较已保存的本地完成时间。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

COLLECTION_REFRESH_HOUR = 8
G_WEEKLY = "g_weekly"
G_DAILY = "g_daily"


@dataclass(frozen=True)
class CycleStrategy:
    """一个冷却周期的刷新规则。"""

    name: str
    type: str  # "weekly" | "daily"
    reset_hour: int
    reset_minute: int
    timezone_offset_hours: int
    reset_weekday: int | None = None  # weekly 专用：0=周一
    blackout_minutes: int = 0


CYCLE_STRATEGIES: dict[str, CycleStrategy] = {
    G_WEEKLY: CycleStrategy(
        name=G_WEEKLY,
        type="weekly",
        reset_hour=COLLECTION_REFRESH_HOUR,
        reset_minute=0,
        timezone_offset_hours=8,
        reset_weekday=0,
    ),
    G_DAILY: CycleStrategy(
        name=G_DAILY,
        type="daily",
        reset_hour=COLLECTION_REFRESH_HOUR,
        reset_minute=0,
        timezone_offset_hours=8,
    ),
}

DEFAULT_STRATEGY_NAME = G_WEEKLY
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def storage_key(card_name: str, strategy_name: str) -> str:
    """卡带与周期策略的组合存档键。"""

    return f"{card_name}@{strategy_name}"


def resolve_strategy(strategy_name: str) -> CycleStrategy:
    """未知周期名称使用默认策略。"""

    strategy = CYCLE_STRATEGIES.get(strategy_name)
    if strategy is None:
        return CYCLE_STRATEGIES[DEFAULT_STRATEGY_NAME]
    return strategy


def last_reset_timestamp(strategy: CycleStrategy, now: datetime) -> datetime:
    """计算游戏服务器时区内最近一次刷新点（返回带 UTC 时区的时间）。"""

    server_tz = timezone(timedelta(hours=strategy.timezone_offset_hours))
    now_server = now.astimezone(server_tz)
    day_start = now_server.replace(hour=0, minute=0, second=0, microsecond=0)
    base_reset = day_start + timedelta(
        hours=strategy.reset_hour, minutes=strategy.reset_minute
    )
    if now_server < base_reset:
        base_reset -= timedelta(days=1)
    if strategy.type == "weekly":
        target_weekday = strategy.reset_weekday or 0
        days_diff = (base_reset.weekday() - target_weekday) % 7
        base_reset -= timedelta(days=days_diff)
    return base_reset.astimezone(UTC)


def _ensure_aware(value: datetime) -> datetime:
    """ naive 时间按当前电脑时区补全，统一为绝对时间参与比较。 """

    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return value.replace(tzinfo=UTC)
        return value.replace(tzinfo=local_tz)
    return value


def _parse_local_timestamp(value: str) -> datetime | None:
    """把本地时间字符串按当前电脑时区还原为绝对时间。"""

    try:
        naive = datetime.strptime(value, _TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        return None
    return naive.replace(tzinfo=local_tz).astimezone(UTC)


def _local_wall_time(value: datetime) -> datetime:
    """把完成时刻归一为“当前电脑时区的墙上时间”。

    存档只保留 naive 本地时间字符串，读取端按机器本地时区还原；
    写入端遇到 aware 入参（如测试注入的北京时间）必须先转换到本地，
    否则写读两侧的时区语义不对称，非 UTC+8 机器会把已完成卡带误判
    为可再跑。
    """

    if value.tzinfo is not None:
        return value.astimezone()
    return value


class CooldownStore:
    """卡带完成时间存档。"""

    def __init__(self, path: Path | str | None = None, now_provider=None, *, progress=None):
        if path is None and progress is None:
            raise ValueError("cooldown requires a path or a progress store")
        self._path = Path(path) if path is not None else None
        self._progress = progress
        self._lock = threading.Lock()
        self._now_provider = now_provider or datetime.now
        self._records: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._progress is not None:
            self._records = dict(self._progress.state.cooldown_records)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._records = {}
            return
        if isinstance(raw, dict):
            self._records = {
                str(key): str(value) for key, value in raw.items() if value
            }
        else:
            self._records = {}

    def _save(self) -> None:
        if self._progress is not None:
            self._progress.state.cooldown_records = dict(self._records)
            self._progress.save()
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._path)

    def _now(self) -> datetime:
        return self._now_provider()

    def check_available(
        self,
        card_name: str,
        strategy_name: str = DEFAULT_STRATEGY_NAME,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """检查卡带是否可执行；返回 (是否可执行, 人类可读原因)。"""

        strategy = resolve_strategy(strategy_name)
        now = _ensure_aware(now or self._now())
        reset = last_reset_timestamp(strategy, now)
        if reset <= now < reset + timedelta(minutes=strategy.blackout_minutes):
            return False, f"{card_name} 处于结算保护期"

        key = storage_key(card_name, strategy.name)
        last_run = self._records.get(key)
        if last_run is None:
            return True, f"{card_name} 本周期未运行"
        last_run_ts = _parse_local_timestamp(last_run)
        if last_run_ts is None:
            # 解析失败按未运行处理，避免坏数据永久卡死卡带。
            return True, f"{card_name} 完成时间无法解析，视为未运行"
        if last_run_ts < reset:
            return True, f"{card_name} 上次完成早于刷新点"
        return False, f"{card_name} 本周期已完成"

    def mark_complete(
        self,
        card_name: str,
        strategy_name: str = DEFAULT_STRATEGY_NAME,
        now: datetime | None = None,
    ) -> None:
        """记录完成时间；多条目标共享同一个完成时刻。"""

        strategy = resolve_strategy(strategy_name)
        now = _local_wall_time(now or self._now())
        with self._lock:
            self._records[storage_key(card_name, strategy.name)] = now.strftime(
                _TIME_FORMAT
            )
            self._save()

    def mark_many(
        self,
        card_names: list[str],
        strategy_name: str = DEFAULT_STRATEGY_NAME,
        now: datetime | None = None,
    ) -> None:
        now = _local_wall_time(now or self._now())
        strategy = resolve_strategy(strategy_name)
        with self._lock:
            stamp = now.strftime(_TIME_FORMAT)
            for card_name in card_names:
                self._records[storage_key(card_name, strategy.name)] = stamp
            self._save()

    def record(self, card_name: str, strategy_name: str = DEFAULT_STRATEGY_NAME) -> str | None:
        return self._records.get(storage_key(card_name, strategy_name))
