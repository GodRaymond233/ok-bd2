"""Serializable quick-hunt plans shared by the editor and task runner."""

import json
from copy import deepcopy

PLAN_KEY = "快速狩猎刷取方案"
LEGACY_KEYS = (
    "快速狩猎冒险航线",
    "快速狩猎狩猎场",
    "快速狩猎双倍策略",
    "快速狩猎资源倾向",
    "快速狩猎米饭分配",
)
CONDITIONS = {
    "gold": "金币双倍",
    "no_gold": "金币非双倍",
    "exp": "经验双倍",
    "no_exp": "经验非双倍",
    "both": "金币和经验都双倍",
    "neither": "金币和经验都非双倍",
    "either": "任意资源双倍",
    "always": "始终执行",
}
TARGETS = {"hunt": "狩猎场（游戏当前关卡）", "gold": "金币", "exp": "经验 / 史莱姆"}
RICE_PER_HUNT = 6
MODES = {"count": "固定次数", "budget": "最多消耗米饭", "all": "用完剩余米饭"}


def step(target="hunt", mode="all", count=1):
    return {"target": target, "mode": mode, "count": count}


def preset_plans():
    return [
        {
            "name": "金币双倍优先，平时刷材料",
            "rules": [{"condition": "gold", "steps": [step(mode="count"), step("gold")]}],
            "fallback": [step()],
        },
        {
            "name": "经验双倍优先，平时刷材料",
            "rules": [{"condition": "exp", "steps": [step(mode="count"), step("exp")]}],
            "fallback": [step()],
        },
        {
            "name": "任意双倍优先",
            "rules": [
                {"condition": resource, "steps": [step(mode="count"), step(resource)]}
                for resource in ("gold", "exp")
            ],
            "fallback": [step()],
        },
        {"name": "只刷狩猎场", "rules": [], "fallback": [step()]},
    ]


def legacy_plan(config):
    hunt = config.get("快速狩猎狩猎场", True)
    adventure = config.get("快速狩猎冒险航线", True)
    allocation = config.get("快速狩猎米饭分配", "狩猎场x1 / 双倍图MAX")
    if allocation in ("狩猎场MAX / 跳过冒险航线", "狩猎场MAX / 双倍图x1"):
        return {"name": "原有刷取设置", "rules": [], "fallback": [step()] if hunt else []}
    base = [step(mode="count")] if hunt else []
    result = {"name": "原有刷取设置", "rules": [], "fallback": base}
    if adventure:
        strategy = config.get("快速狩猎双倍策略", "优先双倍")
        if strategy == "忽视双倍":
            result["fallback"] = base + [step("gold")]
        else:
            order = ["gold", "exp"]
            if strategy == "强制双倍" and config.get("快速狩猎资源倾向") == "经验":
                order.reverse()
            result["rules"] = [
                {"condition": target, "steps": deepcopy(base) + [step(target)]} for target in order
            ]
    return result


def validate_plan(plan):
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("name"), str)
        or not plan["name"].strip()
    ):
        raise ValueError("请填写方案名称。")
    if type(plan.get("reserve", 0)) is not int or not 0 <= plan.get("reserve", 0) <= 9999:
        raise ValueError("保留米饭应为 0 至 9999 的整数。")
    rules = plan.get("rules")
    if not isinstance(rules, list) or not isinstance(plan.get("fallback"), list):
        raise ValueError("方案缺少规则或其他情况的步骤。")
    groups = [plan["fallback"]]
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or rule.get("condition") not in CONDITIONS:
            raise ValueError("不支持的规则条件。")
        if rule["condition"] == "always" and index != len(rules) - 1:
            raise ValueError("始终执行规则必须放在最后，否则后续规则无法命中。")
        groups.append(rule.get("steps"))
    for steps in groups:
        if not isinstance(steps, list):
            raise ValueError("刷取步骤必须为列表。")
        for index, item in enumerate(steps):
            if (
                not isinstance(item, dict)
                or item.get("target") not in TARGETS
                or item.get("mode") not in MODES
            ):
                raise ValueError("不支持的刷取目标或数量方式。")
            if item["mode"] == "all" and index != len(steps) - 1:
                raise ValueError("用完剩余米饭必须是最后一步。")
            if item["mode"] != "all" and (
                type(item.get("count")) is not int or not 1 <= item["count"] <= 999
            ):
                raise ValueError("次数或米饭预算应为 1 至 999 的整数。")


def load_book(config):
    raw = config.get(PLAN_KEY, "")
    if not raw:
        return {"version": 1, "active": 0, "plans": [legacy_plan(config)]}
    try:
        book = json.loads(raw)
        if not isinstance(book, dict) or book.get("version") != 1:
            raise ValueError("不支持的方案版本。")
        plans = book.get("plans")
        if not isinstance(plans, list) or not plans:
            raise ValueError("至少保留一个刷取方案。")
        if type(book.get("active")) is not int or not 0 <= book["active"] < len(plans):
            raise ValueError("当前方案不存在。")
        for plan in plans:
            validate_plan(plan)
        return book
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取刷取方案，请在编辑器中重新设置。") from exc


def match_condition(condition, states):
    """None means a required reward state could not be confirmed."""
    gold, exp = states.get("金币"), states.get("经验")
    if condition == "always":
        return True
    if condition in ("gold", "exp", "no_gold", "no_exp"):
        value = gold if condition.endswith("gold") else exp
        return None if value is None else (not value if condition.startswith("no_") else value)
    if condition == "both":
        return (
            False
            if gold is False or exp is False
            else None
            if gold is None or exp is None
            else True
        )
    if condition == "neither":
        return (
            False if gold is True or exp is True else None if gold is None or exp is None else True
        )
    if condition == "either":
        return (
            True if gold is True or exp is True else None if gold is None or exp is None else False
        )
    raise ValueError("不支持的规则条件。")


def select_steps(plan, states):
    validate_plan(plan)
    for index, rule in enumerate(plan["rules"]):
        matched = match_condition(rule["condition"], states)
        if matched is None:
            raise ValueError(f"无法确认规则 {index + 1} 的双倍状态，暂停米饭刷取。")
        if matched:
            return f"规则 {index + 1}：{CONDITIONS[rule['condition']]}", deepcopy(rule["steps"])
    return "其他情况", deepcopy(plan["fallback"])


def describe_steps(steps):
    return (
        " → ".join(
            f"{TARGETS[s['target']]} · "
            + (
                f"{s['count']} 次"
                if s["mode"] == "count"
                else f"最多 {s['count']} 米饭"
                if s["mode"] == "budget"
                else "用完剩余米饭"
            )
            for s in steps
        )
        or "保留米饭，不刷取"
    )


def describe_plan(plan):
    return "\n".join(
        [f"{CONDITIONS[r['condition']]}：{describe_steps(r['steps'])}" for r in plan["rules"]]
        + [
            f"其他情况：{describe_steps(plan['fallback'])}",
            f"保留免费米饭：{plan.get('reserve', 0)}",
        ]
    )


def step_count(item, rice, reserve=0):
    count = max(0, rice - reserve) // RICE_PER_HUNT
    if item["mode"] == "count":
        return min(count, item["count"])
    if item["mode"] == "budget":
        return min(count, item["count"] // RICE_PER_HUNT)
    return count


def preview_plan(plan, states, rice):
    matched, steps = select_steps(plan, states)
    lines = [f"命中{matched}"]
    for item in steps:
        count = step_count(item, rice, plan.get("reserve", 0))
        rice -= count * RICE_PER_HUNT
        lines.append(f"{TARGETS[item['target']]}：{count} 次，消耗 {count * RICE_PER_HUNT} 米饭")
    if not steps:
        lines.append("保留米饭，不刷取")
    lines.append(f"剩余免费米饭：{rice}")
    return "\n".join(lines)
