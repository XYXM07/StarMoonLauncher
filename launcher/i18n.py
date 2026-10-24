"""Presentation-only localization; identifiers, paths and stored data stay intact."""
import re
import string
from functools import lru_cache

from .translations import EN

LANGUAGES = (("zh_CN", "简体中文"), ("en_US", "English"))
_language = "zh_CN"
_han = re.compile(r"[\u4e00-\u9fff]")
_formatter = string.Formatter()


def language():
    return _language


def set_language(value):
    global _language
    if value not in dict(LANGUAGES):
        raise ValueError("Unsupported language")
    _language = value


def _parts(template):
    auto = 0
    for literal, name, spec, conversion in _formatter.parse(template):
        if name == "":
            name = str(auto)
            auto += 1
        yield literal, name


@lru_cache(maxsize=1)
def _templates():
    patterns = []
    for source, target in EN.items():
        if "{" not in source:
            continue
        fields, pattern = [], ""
        try:
            for literal, name in _parts(source):
                pattern += re.escape(literal)
                if name is not None:
                    fields.append(name)
                    pattern += "(.*?)"
            patterns.append((re.compile(r"\A" + pattern + r"\Z", re.S), fields, target))
        except ValueError:
            continue
    # Prefer concrete messages over generic wrappers such as "{} versions".
    return sorted(patterns, key=lambda item: len(item[0].pattern), reverse=True)


def tr(text, _depth=0):
    text = str(text)
    if _language == "zh_CN" or _depth > 5 or not _han.search(text):
        return text
    if text in EN:
        return EN[text]
    # Captured paths are data, including any Chinese filenames they contain.
    if re.match(r"^(?:[A-Za-z]:[\\/]|[/\\]|https?://)", text):
        return text
    for pattern, names, target in _templates():
        match = pattern.fullmatch(text)
        if match:
            values = dict(zip(names, match.groups()))
            return "".join(literal + (tr(values[name], _depth + 1) if name is not None else "")
                           for literal, name in _parts(target))
    for source in sorted(EN, key=len, reverse=True):
        if source.endswith(("：", " ")) and text.startswith(source) and len(text) > len(source):
            return EN[source] + tr(text[len(source):], _depth + 1)
    if "\n" in text:
        return "\n".join(tr(line, _depth + 1) for line in text.split("\n"))
    chunks = re.split(r"(\n|：|: | · |；|（|）)", text)
    if len(chunks) > 1:
        return "".join(tr(chunk, _depth + 1) if index % 2 == 0 else {"：": ": ", "；": "; ", "（": " (", "）": ")"}.get(chunk, chunk)
                       for index, chunk in enumerate(chunks))
    return text
