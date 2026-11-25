"""Prepare a built, self-contained course for static hosting."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Chapter package containing course/index.html")
    args = parser.parse_args()

    index = args.package.resolve() / "course" / "index.html"
    if not index.is_file():
        parser.error("Build the course first: uv run python -m course.build <package>")

    course_dir = Path(__file__).resolve().parent
    checkout = course_dir / ".site" / "source"
    output = checkout / "dist"
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(index.read_text(encoding="utf-8"), encoding="utf-8")

    manifest = course_dir / ".openai" / "hosting.json"
    if manifest.is_file():
        destination = checkout / ".openai" / "hosting.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Static Site source: {checkout}")


if __name__ == "__main__":
    main()
