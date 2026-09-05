"""关键依赖完整性预检与自愈（BUG-20260905-07）。

pywin32 等包可能处于「dist-info 登记在而实际文件缺失」的损坏安装态：pip 视为
already satisfied 永远跳过，启动器的 marker 重装与发版依赖同步都修不了它，应用
最终在 import 链深处裸崩 ModuleNotFoundError。这里在框架 import 之前用 find_spec
校验核心模块的存在性，发现缺失时按 requirements.txt 锁定版本 --force-reinstall
对应发行包，修复完成后提示重启应用。

只做存在性校验（find_spec 不执行模块代码），「文件在但加载崩」类问题交由
faulthandler 与既有报错链路。本模块刻意不 import ok / 第三方包，保证在依赖
已损坏的裸环境下自身可运行。锁定版本必须取自 requirements.txt：不带版本强制
重装会让 pywin32 拉到 312+ 回归 mfc140u.dll 缺失（BUG-20260902-04）。
"""

from __future__ import annotations

import ctypes
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

LOG_NAME = "dependency_guard.log"

# 核心模块 -> pip 发行包。覆盖捕获/OCR/UI 主链路上没有替代路径、损坏即裸崩的包。
CORE_MODULE_PACKAGES = {
    "win32con": "pywin32",
    "win32api": "pywin32",
    "win32ui": "pywin32",
    "cv2": "opencv-python",
    "PySide6": "PySide6",
    "openvino": "openvino",
    "onnxocr": "onnxocr-ppocrv5",
}

# 依次尝试的 --index-url；None 表示不指定，走用户本机 pip 配置（含官方源）。
PIP_INDEX_FALLBACK: tuple[str | None, ...] = (
    "https://mirrors.aliyun.com/pypi/simple/",
    None,
)

REPAIR_TIMEOUT_SECONDS = 900
_REQUIREMENT_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)")


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _log(line: str) -> None:
    try:
        Path("logs").mkdir(exist_ok=True)
        with open(Path("logs") / LOG_NAME, "a", encoding="utf-8") as file:
            from datetime import datetime

            file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def find_missing_modules() -> list[str]:
    """返回核心清单里无法定位的模块名（spec 缺失或定位即抛错）。"""
    missing = []
    for module_name in CORE_MODULE_PACKAGES:
        try:
            if importlib.util.find_spec(module_name) is None:
                missing.append(module_name)
        except (ImportError, ValueError, ModuleNotFoundError):
            missing.append(module_name)
    return missing


def parse_locked_versions(requirements_text: str) -> dict[str, str]:
    """从 pip-compile 锁文件文本提取 发行包名 == 锁定版本。"""
    versions: dict[str, str] = {}
    for line in requirements_text.splitlines():
        match = _REQUIREMENT_RE.match(line)
        if match:
            versions[_normalize_dist_name(match.group(1))] = match.group(2)
    return versions


def read_locked_versions() -> dict[str, str]:
    try:
        text = Path("requirements.txt").read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_locked_versions(text)


def build_repair_command(
    package: str, version: str, index_url: str | None
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        f"{package}=={version}",
    ]
    if index_url:
        command += ["--index-url", index_url]
    return command


def force_reinstall(package: str, version: str) -> bool:
    for index_url in PIP_INDEX_FALLBACK:
        command = build_repair_command(package, version, index_url)
        _log(f"repair: {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=REPAIR_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _log(f"repair: {package} launch failed: {error}")
            continue
        if result.returncode == 0:
            _log(f"repair: {package}=={version} installed")
            return True
        _log(
            f"repair: {package} failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()[-500:]}"
        )
    return False


def _notify(text: str, *, error: bool = False) -> None:
    flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    ctypes.windll.user32.MessageBoxW(
        0, text, "ok-bd2 依赖自检", flags | 0x1000  # MB_TOPMOST
    )


def _missing_packages(missing_modules: list[str]) -> dict[str, list[str]]:
    packages: dict[str, list[str]] = {}
    for module_name in missing_modules:
        packages.setdefault(CORE_MODULE_PACKAGES[module_name], []).append(module_name)
    return packages


def ensure_core_dependencies() -> None:
    """损坏即强制重装并以弹窗收尾（退出码 0=已修复请重启，1=需人工处理）。"""
    missing_modules = find_missing_modules()
    if not missing_modules:
        return
    _log(f"missing modules: {', '.join(missing_modules)}")

    locked = read_locked_versions()
    if not locked:
        _notify(
            "检测到核心依赖缺失（"
            + ", ".join(missing_modules)
            + "），但未能从 requirements.txt 读取到锁定版本，无法自动修复。\n"
            "请重新安装 ok-bd2，或把启动器日志发给维护者。",
            error=True,
        )
        raise SystemExit(1)

    results: dict[str, bool] = {}
    for package, modules in _missing_packages(missing_modules).items():
        version = locked.get(_normalize_dist_name(package))
        if version is None:
            _log(f"repair: no locked version for {package}, skipped")
            results[package] = False
            continue
        results[package] = force_reinstall(package, version)

    still_missing = find_missing_modules()
    repaired = [pkg for pkg, ok_flag in results.items() if ok_flag]
    if repaired and not still_missing:
        _log(f"repaired packages: {', '.join(repaired)}; restart required")
        _notify(
            "检测到损坏的依赖（"
            + ", ".join(missing_modules)
            + "）并已自动修复。\n请重新启动 ok-bd2。"
        )
        raise SystemExit(0)

    manual_lines = [
        f"检测到无法自动修复的核心依赖缺失：{', '.join(still_missing or missing_modules)}。",
        "可尝试手动执行：",
        f'{sys.executable} -m pip install --force-reinstall --no-deps '
        "包名==锁定版本（见 working\\requirements.txt）",
        "或重新安装 ok-bd2；仍失败请把 logs 与启动器日志发给维护者。",
    ]
    _log(f"repair incomplete: results={results}, still_missing={still_missing}")
    _notify("\n".join(manual_lines), error=True)
    raise SystemExit(1)
