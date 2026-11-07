"""English-first UI strings with an optional Japanese table (i18n/ja.json).

The language comes from Settings > Prompt Line Combo Queue > UI language:
"Auto" follows the webui Localization setting (Japanese when its name starts
with "ja"), or force "English" / "日本語".
"""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLES: dict[str, dict[str, str]] = {}

LANG_CHOICES = ["Auto", "English", "日本語"]


def _table(lang: str) -> dict[str, str]:
    if lang not in _TABLES:
        path = os.path.join(_HERE, "i18n", f"{lang}.json")
        try:
            with open(path, encoding="utf8") as f:
                _TABLES[lang] = json.load(f)
        except OSError:
            _TABLES[lang] = {}
    return _TABLES[lang]


def current_lang(opts=None) -> str:
    if opts is None:
        try:
            from modules import shared

            opts = shared.opts
        except Exception:
            return "en"
    choice = getattr(opts, "plcq_language", "Auto") or "Auto"
    if choice == "English":
        return "en"
    if choice == "日本語":
        return "ja"
    loc = str(getattr(opts, "localization", "") or "").lower()
    return "ja" if loc.startswith("ja") else "en"


def translator(lang: str):
    table = _table(lang) if lang != "en" else {}

    def t(s: str, **kw) -> str:
        out = table.get(s, s)
        return out.format(**kw) if kw else out

    return t


def table_for(lang: str) -> dict[str, str]:
    return dict(_table(lang)) if lang != "en" else {}
