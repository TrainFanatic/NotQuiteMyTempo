"""Small text helpers: numbers as words, audio-tag stripping, sentence splitting."""

from __future__ import annotations

import re
from typing import AsyncIterator

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

TAG_RE = re.compile(r"\[[^\]]{1,40}\]\s*")
GESTURE_RE = re.compile(r"\{([a-z_]{2,30})\}\s*")


def num_words(n: int | float) -> str:
    n = int(round(n))
    if n < 0:
        return "minus " + num_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("" if o == 0 else "-" + _ONES[o])
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + ("" if r == 0 else " and " + num_words(r))
    return str(n)


def strip_tags(s: str) -> str:
    """Remove [audio tags] and {gesture cues} for display or for voices that would read them aloud."""
    return re.sub(r"\s{2,}", " ", TAG_RE.sub("", GESTURE_RE.sub("", s))).strip()


def split_gestures(s: str) -> tuple[str, list[str]]:
    """'{slam_stand} [shouting] NO!' -> ('[shouting] NO!', ['slam_stand'])."""
    return GESTURE_RE.sub("", s).strip(), GESTURE_RE.findall(s)


def mentions_bar(text: str, bar: int) -> bool:
    low = text.lower()
    return bool(re.search(rf"\bbar\s+({bar}|{re.escape(num_words(bar))})\b", low))


class SentenceSplitter:
    """Feed streamed text, get whole sentences back as soon as they end.

    An ellipsis ("...") is a spoken pause inside a sentence, so it never ends
    one; "!" and "?" and a single "." do. Audio tags pass through untouched."""

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while True:
            cut = self._boundary()
            if cut is None:
                break
            sentence, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []

    def _boundary(self) -> int | None:
        buf = self.buf
        i = 0
        while i < len(buf):
            if buf[i] in ".!?":
                j = i
                while j < len(buf) and buf[j] in ".!?":
                    j += 1
                run = buf[i:j]
                k = j
                while k < len(buf) and buf[k] in "\"')”’":
                    k += 1
                if k >= len(buf):
                    return None  # need the next character to decide
                if buf[k].isspace():
                    is_ellipsis = run.count(".") >= 2 and "!" not in run and "?" not in run
                    if not is_ellipsis:
                        return k
                i = k
                continue
            i += 1
        return None


async def merge_short(sentences: AsyncIterator[str], min_chars: int = 24) -> AsyncIterator[str]:
    """Join tiny sentences ("Now.") onto their neighbour so the voice doesn't
    render them as separate, oddly-phrased generations."""
    pending = ""
    async for s in sentences:
        pending = f"{pending} {s}".strip() if pending else s
        if len(strip_tags(pending)) >= min_chars:
            yield pending
            pending = ""
    if pending:
        yield pending
