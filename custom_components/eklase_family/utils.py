import re
from datetime import time

_TIME_RE = re.compile(r"^\s*(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\s*$")
_TAG_RE = re.compile(r"<[^>]+>")

def parse_schedule(text: str) -> list[tuple[time, time]]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Empty schedule")
    out = []
    for ln in lines:
        m = _TIME_RE.match(ln)
        if not m:
            raise ValueError(f"Bad schedule line: {ln}")
        sh, sm, eh, em = map(int, m.groups())
        out.append((time(sh, sm), time(eh, em)))
    return out

def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    s = html.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n").replace("<p>", "")
    s = _TAG_RE.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s or None