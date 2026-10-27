"""Task 1 RED：timefmt 转换器单元测试。

被测契约：ISO（UTC 或带时区）→ 「YYYY-MM-DD HH:MM:SS」（固定 UTC+8 显示）。
仅用于给人看的位置：库内/JSONL/上游机器接口保持 ISO UTC 原样，本模块不回写。
"""
from __future__ import annotations

from server.backend.timefmt import display, display_epoch

PATTERN = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"


def test_user_sample_utc_to_local():
    # 用户原始样例逐字复现：15:30 UTC = 北京 23:30。
    assert display("2026-10-26T15:30:02.075819+00:00") == "2026-10-26 23:30:02"


def test_day_boundary_rolls_forward():
    assert display("2026-10-25T23:00:00+00:00") == "2026-10-26 07:00:00"


def test_plain_utc_offset():
    assert display("2026-10-26T01:02:03+00:00") == "2026-10-26 09:02:03"


def test_already_local_timezone_input():
    # 带 +0800 的输入按其自身时区转（诚实容错）。
    assert display("2026-10-26T10:00:00+0800") == "2026-10-26 10:00:00"


def test_microseconds_are_truncated():
    assert display("2026-10-26T15:30:02.999999+00:00") == "2026-10-26 23:30:02"


def test_none_and_empty_passthrough():
    assert display(None) is None
    assert display("") == ""


def test_epoch_zero_is_gmt_eight():
    assert display_epoch(0) == "1970-01-01 08:00:00"
    assert display_epoch(None) is None


def test_output_is_parseable_display_shape():
    got = display("2026-10-26T15:30:02.075819+00:00")
    assert got is not None and __import__("re").fullmatch(PATTERN, got)
