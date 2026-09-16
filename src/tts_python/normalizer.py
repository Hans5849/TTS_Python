from __future__ import annotations

import html
import re
import unicodedata

SUBSTITUTIONS = {r"\alpha": "alpha", r"\beta": "beta", "≤": " less than or equal to ",
                 "≥": " greater than or equal to ", "→": " leads to ", "×": " times "}


def normalize(text: str) -> str:
    """Perform conservative, deterministic cleanup without semantic rewriting."""
    text = unicodedata.normalize("NFC", text.removeprefix("\ufeff"))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:p|div|span|strong|em|b|i)(?:\s[^>]*)?>", "", text, flags=re.I)
    text = html.unescape(text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    for source, spoken in SUBSTITUTIONS.items():
        text = text.replace(source, spoken)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
