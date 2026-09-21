import pytest

from nodeseek_bot.normalize import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VPS", "vps"),
        ("ＶＰＳ", "vps"),
        ("Ｖps", "vps"),
        ("日本 VPS 补货", "日本 vps 补货"),
        ("Ａ１", "a1"),
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_is_idempotent() -> None:
    once = normalize("ＶＰＳ ＡＢＣ")
    assert normalize(once) == once


def test_normalize_keeps_chinese_unchanged() -> None:
    assert normalize("显卡") == "显卡"


def test_normalize_does_not_strip_or_collapse_whitespace() -> None:
    assert normalize("  VPS  ") == "  vps  "
