"""时间显示转换：给人看的位置统一为「YYYY-MM-DD HH:MM:SS」（固定 UTC+8）。

纪律：库表列、JSONL ``ts``、上游过滤条件一律保持 ISO UTC 原样——本模块只读入
时刻字符串，绝不回写机器格式。中国无夏令时，固定 +8 取确定性而非依赖 tzdata。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_ZH = timezone(timedelta(hours=8))
_DISPLAY = "%Y-%m-%d %H:%M:%S"


def display(value: str | None) -> str | None:
    """ISO 时刻字符串（库内不变量为 aware UTC；Z/+0800 亦容忍）→ 本地显示。

    朴素输入按 UTC 处置（防御分支：现行产线只产 aware ISO）。
    """
    if value is None or value == "":
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_ZH).strftime(_DISPLAY)


def display_epoch(seconds: float | None) -> str | None:
    """epoch 秒 → 本地显示（None 原样）。"""
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, _ZH).strftime(_DISPLAY)
