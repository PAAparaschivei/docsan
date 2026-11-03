#!/usr/bin/env python3
"""Check local Markdown links and HTML image paths in the public file set."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

from public_source import candidate_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tree", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    names = set(candidate_names(root, args.tree))
    broken, checked = [], 0
    for name in sorted(names):
        path = root / name
        if path.suffix != ".md" or path.is_symlink() or not path.is_file():
            continue
        text = path.read_text()
        links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
        links += re.findall(r'<img[^>]+src="([^"]+)"', text)
        for link in links:
            link = link.split("#", 1)[0]
            if not link or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", link):
                continue
            checked += 1
            target = (path.parent / unquote(link)).resolve()
            if not target.is_relative_to(root) or target.relative_to(root).as_posix() not in names:
                broken.append({"file": name, "target": link})
    print({"local_links_checked": checked, "broken": broken})
    return bool(broken)


if __name__ == "__main__":
    raise SystemExit(main())
