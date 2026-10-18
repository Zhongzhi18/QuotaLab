"""Render the single public Pages landing page with this repository's links."""
from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path


SOURCE = Path(__file__).with_name("index.html")
REPOSITORY_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_url")
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    if not REPOSITORY_URL.fullmatch(args.repository_url):
        parser.error("repository_url must be an https://github.com/owner/repository URL")
    if not SOURCE.is_file():
        parser.error("site/index.html is missing")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    rendered = SOURCE.read_text(encoding="utf-8").replace(
        "__REPOSITORY_URL__", html.escape(args.repository_url, quote=True)
    )
    (args.output_directory / "index.html").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
