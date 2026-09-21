import pytest

from nodeseek_bot.matcher import expand_rules, matches


@pytest.mark.parametrize(
    ("title_norm", "keyword_raw", "expected"),
    [
        ("vps 补货了", "vps", True),
        ("vps 补货了", "VPS", True),
        ("vps 补货了", "显卡", False),
        ("日本 vps 补货", "日本 vps", True),
        ("日本 显卡 补货", "日本 vps", False),
        ("日本vps补货", "日本 vps", True),
        ("vps", "  vps  ", True),
        ("anything", "   ", False),
    ],
)
def test_matches(title_norm: str, keyword_raw: str, expected: bool) -> None:
    rule = expand_rules(keyword_raw)
    result = False if rule is None else matches(title_norm, rule)
    assert result is expected


def test_expand_rules_normalises_and_collapses_whitespace() -> None:
    assert expand_rules("  ＶＰＳ   日本 ") == "vps 日本"


def test_expand_rules_rejects_blank() -> None:
    assert expand_rules("   ") is None


def test_expand_rules_rejects_too_long() -> None:
    assert expand_rules("x" * 65) is None


def test_expand_rules_accepts_boundary_length() -> None:
    assert expand_rules("x" * 64) == "x" * 64
