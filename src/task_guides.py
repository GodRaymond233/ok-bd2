"""Which tasks have an illustrated guide under docs/guides, with a one-line summary.

Qt-free so both the Qt task cards (``src/ui/task_guide.py``) and the desktop client
can use it.
"""

from pathlib import Path

GUIDE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "guides"
# task class name -> (title, guide file stem, summary)
GUIDES = {
    "MapCollectionTask": ("每周跑图", "collection", "选择卡带，按周进度完成采集。"),
    "MapTradeTask": ("每日跑商", "trade", "先确认买卖范围，再开始每日跑商。"),
    "QuickHuntTask": ("快速狩猎", "quick-hunt", "安排米饭用途，按需补充属性圣石。"),
}
