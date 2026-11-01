"""The design tokens are the one source of raw values. These tests are what stop the look drifting, page by page."""
import re

from ra.web import DASHBOARD_HTML, STATIC

TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
SHEET = (STATIC / "desk.css").read_text(encoding="utf-8")
SCRIPT = (STATIC / "desk.js").read_text(encoding="utf-8")
PAGE = (STATIC / "desk.html").read_text(encoding="utf-8")
ACTIVITY = {name: (STATIC / name).read_text(encoding="utf-8") for name in ("activity.css", "activity.js", "activity-flow.js", "activity-boards.js", "activity-assistant.js", "activity.html")}
SURFACES = {"desk.css": SHEET, "desk.js": SCRIPT, "desk.html": PAGE, "dashboard": DASHBOARD_HTML, **ACTIVITY}
RAW_COLOUR = re.compile(r"(?<![\w&/#-])#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
RAW_FONT = re.compile(r"font-family\s*:\s*(?!var\()|system-ui|ui-monospace|Georgia|Consolas")


def test_no_surface_uses_a_raw_colour_or_font():
    for name, text in SURFACES.items():
        assert not RAW_COLOUR.findall(text), f"{name} has a raw colour; add a token: {RAW_COLOUR.findall(text)[:3]}"
        assert not RAW_FONT.findall(text), f"{name} has a raw font; use --font-ui, --font-read or --font-data: {RAW_FONT.findall(text)[:3]}"


def test_every_token_a_surface_reads_is_defined():
    defined = set(re.findall(r"(--[\w-]+)\s*:", TOKENS))
    for name, text in {"desk.css": SHEET, "dashboard": DASHBOARD_HTML, "activity.css": ACTIVITY["activity.css"]}.items():
        local = set(re.findall(r"(--[\w-]+)\s*:", text))
        set_by_script = {"--span", "--x", "--at", "--i"}
        missing = set(re.findall(r"var\((--[\w-]+)", text)) - defined - local - set_by_script
        assert not missing, f"{name} reads tokens that do not exist: {sorted(missing)}"


def test_every_font_is_shipped_with_its_licence():
    for url in re.findall(r'url\("/static/(fonts/[^"]+)"\)', TOKENS):
        assert (STATIC / url).stat().st_size > 10_000, url
    assert len(re.findall(r"@font-face", TOKENS)) == 3
    for licence in ("OFL-Geist.txt", "OFL-SourceSerif4.txt"):
        assert "SIL Open Font License" in (STATIC / "fonts" / licence).read_text(encoding="utf-8")
