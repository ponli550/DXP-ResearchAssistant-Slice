"""Every code block in docs/TECH_STACK.md that names a source file must appear verbatim in that file."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.compile(r"<!-- source: (?P<path>[^ ]+) -->\n```\w*\n(?P<code>.*?)\n```", re.S)


def blocks():
    return [(m["path"], m["code"]) for m in BLOCK.finditer((ROOT / "docs" / "TECH_STACK.md").read_text())]


def test_the_doc_has_code_examples():
    assert len(blocks()) >= 9


def test_every_example_is_verbatim_from_its_file():
    for path, code in blocks():
        source = (ROOT / path).read_text()
        assert code in source, f"example from {path} no longer matches the file: {code[:60]!r}"
