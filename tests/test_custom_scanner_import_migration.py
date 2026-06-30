"""R3 upgrade migration: rewrite users' custom-strategy ``from forven.scanner import …`` to the
``forven.strategies.indicators`` facade so an existing custom strategy doesn't silently stop
loading after forven.scanner is removed from the untrusted-import allowlist.

See forven/migrations.py::_m_2026_06_rewrite_custom_scanner_imports and docs/UPGRADE.md.
"""

import pytest

from forven.migrations import (
    MIGRATIONS,
    redirect_pure_scanner_imports as R,
    rewrite_custom_scanner_imports_in_dir,
)


def test_migration_is_registered():
    assert any(m.name == "2026_06_rewrite_custom_scanner_imports" for m in MIGRATIONS)


@pytest.mark.parametrize(
    "src,expected",
    [
        # pure indicators -> redirected
        ("from forven.scanner import atr, rsi\n", "from forven.strategies.indicators import atr, rsi\n"),
        ("    from forven.scanner import rsi as r\n", "    from forven.strategies.indicators import rsi as r\n"),
        ("from forven.scanner import atr  # stop\n", "from forven.strategies.indicators import atr  # stop\n"),
        ("from forven.scanner import funding_rate_zscore, oi_price_divergence\n",
         "from forven.strategies.indicators import funding_rate_zscore, oi_price_divergence\n"),
        # deputy / non-pure -> LEFT (these strategies must stay rejected)
        ("from forven.scanner import fetch_candles\n", "from forven.scanner import fetch_candles\n"),
        ("from forven.scanner import get_db\n", "from forven.scanner import get_db\n"),
        ("from forven.scanner import _execute_direct\n", "from forven.scanner import _execute_direct\n"),
        # mixed (one non-pure) -> LEFT
        ("from forven.scanner import atr, fetch_candles\n", "from forven.scanner import atr, fetch_candles\n"),
        # parenthesised / multiline -> LEFT (conservative)
        ("from forven.scanner import (atr,\n  rsi)\n", "from forven.scanner import (atr,\n  rsi)\n"),
        # unrelated imports untouched
        ("import pandas as pd\n", "import pandas as pd\n"),
        ("from forven.strategies.base import BaseStrategy\n", "from forven.strategies.base import BaseStrategy\n"),
    ],
)
def test_redirect_pure_scanner_imports(src, expected):
    assert R(src) == expected


def test_redirect_is_idempotent():
    once = R("from forven.scanner import adx\n")
    assert R(once) == once == "from forven.strategies.indicators import adx\n"


def test_dir_rewriter_only_touches_pure_indicator_files(tmp_path):
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    pure = tmp_path / "ema_rsi.py"
    pure.write_text("from forven.scanner import atr, rsi\n\nX = 1\n", encoding="utf-8")
    deputy = tmp_path / "self_fetch.py"
    deputy.write_text("from forven.scanner import fetch_candles\n", encoding="utf-8")
    unrelated = tmp_path / "clean.py"
    unrelated.write_text("import numpy as np\n", encoding="utf-8")

    changed = rewrite_custom_scanner_imports_in_dir(tmp_path)

    assert changed == ["ema_rsi.py"]                                  # only the pure-indicator file
    assert pure.read_text(encoding="utf-8") == "from forven.strategies.indicators import atr, rsi\n\nX = 1\n"
    assert deputy.read_text(encoding="utf-8") == "from forven.scanner import fetch_candles\n"  # left
    assert unrelated.read_text(encoding="utf-8") == "import numpy as np\n"
    # idempotent: a second run changes nothing.
    assert rewrite_custom_scanner_imports_in_dir(tmp_path) == []


def test_dir_rewriter_skips_non_utf8_files(tmp_path):
    bad = tmp_path / "weird.py"
    bad.write_bytes(b"from forven.scanner import atr  # \x97 cp1252 em-dash\n")
    # must not raise, and must not (be able to) rewrite the undecodable file.
    assert rewrite_custom_scanner_imports_in_dir(tmp_path) == []
