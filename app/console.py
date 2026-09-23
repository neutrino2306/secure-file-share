"""Web console: serves app/static/index.html with a strict Content-Security-Policy.

The page is one file with a single inline <style> and a single inline <script>.
Instead of allowing 'unsafe-inline', the CSP pins the SHA-256 hash of each inline
block (computed once at startup), so the browser refuses any other script or style,
including anything injected. The page may only talk to this origin.
"""

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

CONSOLE_PATH = Path(__file__).parent / "static" / "index.html"

_SCRIPT_BLOCK = re.compile(r"<script>(.*?)</script>", re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def csp_hash(block: str) -> str:
    digest = hashlib.sha256(block.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


@dataclass(frozen=True)
class ConsolePage:
    html: str
    headers: dict[str, str]


def load_console_page(path: Path = CONSOLE_PATH) -> ConsolePage:
    # read_text normalizes line endings to "\n", matching what browsers hash.
    html = path.read_text(encoding="utf-8")
    scripts = [csp_hash(block) for block in _SCRIPT_BLOCK.findall(html)]
    styles = [csp_hash(block) for block in _STYLE_BLOCK.findall(html)]
    if not scripts or not styles:
        raise RuntimeError(f"{path} must contain an inline <script> and <style> block")

    csp = "; ".join(
        [
            "default-src 'none'",
            f"script-src {' '.join(scripts)}",
            f"style-src {' '.join(styles)}",
            "connect-src 'self'",
            "img-src 'self' data:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        ]
    )
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    return ConsolePage(html=html, headers=headers)
