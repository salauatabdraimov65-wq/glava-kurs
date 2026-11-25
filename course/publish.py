"""Prepare a built, self-contained course for static hosting."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import stat
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from course.build import geology_theme_vars


class MediaSources(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"img", "video", "source"}:
            self.paths.extend(value for key, value in attrs if key in {"src", "poster"} and value)


def prepare(packages: list[Path]) -> Path:
    course_dir = Path(__file__).resolve().parent
    checkout = course_dir / ".site" / "source"
    output = checkout / "dist"
    sources = [(package.resolve(), package.resolve() / "course" / "index.html") for package in packages]
    if len({package.name for package, _ in sources}) != len(sources):
        raise ValueError("Chapter package names must be unique")
    for _, index in sources:
        if not index.is_file():
            raise ValueError(f"Build the course first: {index}")
    if output.exists():
        if output.is_symlink() or not output.resolve().is_relative_to(checkout.resolve()):
            raise ValueError("Static output must stay inside the Site staging directory")
        def remove_readonly(function, path, error):
            Path(path).chmod(stat.S_IWRITE)
            function(path)
        shutil.rmtree(output, onexc=remove_readonly)
    output.mkdir(parents=True, exist_ok=True)
    cards = []
    for package, index in sources:
        chapter_output = output / package.name
        destination = chapter_output / "course" / "index.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        page = index.read_text(encoding="utf-8")
        destination.write_text(page, encoding="utf-8")
        media = MediaSources()
        media.feed(page)
        for src in media.paths:
            url = urlsplit(src)
            if url.scheme or url.netloc:
                raise ValueError("Published course media must be local")
            asset = (index.parent / unquote(url.path)).resolve()
            relative = asset.relative_to(package)
            target = chapter_output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset, target)
        layer = json.loads((package / "layer.json").read_text(encoding="utf-8"))
        title = html.escape(str(layer.get("title") or layer.get("chapter") or package.name))
        count = len(layer.get("nodes") or [])
        href = quote((Path(package.name) / "course" / "index.html").as_posix(), safe="/")
        cards.append(f'<a class="chapter-card" href="{href}"><span>{count} шагов</span><h2>{title}</h2><b>Открыть курс →</b></a>')

    catalog = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Глава → курс</title>
<style>:root {{{geology_theme_vars()}}} *{{box-sizing:border-box}} body{{margin:0;background:var(--geo-background);color:var(--geo-text);font:17px/1.6 "Segoe UI",system-ui,sans-serif}} main{{padding:clamp(20px,5vw,72px)}} .kicker{{color:var(--geo-sand);letter-spacing:.12em}} h1{{font-size:clamp(32px,5vw,56px);line-height:1.15;max-width:20ch}} .intro{{max-width:70ch;color:var(--geo-salt)}} .chapters{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:22px;margin-top:36px}} .chapter-card{{display:flex;flex-direction:column;align-items:flex-start;padding:26px;border:1px solid var(--geo-dense);border-radius:8px;color:inherit;background:var(--geo-panel);text-decoration:none;overflow-wrap:anywhere}} .chapter-card:hover,.chapter-card:focus-visible{{outline:3px solid var(--geo-sand);outline-offset:4px}} .chapter-card span{{color:var(--geo-salt)}} .chapter-card h2{{font-size:24px;line-height:1.3}} .chapter-card b{{margin-top:auto;padding-top:20px;color:var(--geo-sand)}} </style></head>
<body><main><p class="kicker">ГЛАВА → КУРС</p><h1>Выберите главу для изучения</h1>
<p class="intro">Материал книги, тренировки на данных и задания. Каждый курс сохраняет ваш прогресс на этом устройстве.</p>
<div class="chapters">{"".join(cards)}</div></main></body></html>'''
    (output / "index.html").write_text(catalog, encoding="utf-8")
    manifest = course_dir / ".openai" / "hosting.json"
    if manifest.is_file():
        destination = checkout / ".openai" / "hosting.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    return checkout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, nargs="+", help="Chapter packages containing course/index.html")
    args = parser.parse_args()
    try:
        checkout = prepare(args.package)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Static Site source: {checkout}")


if __name__ == "__main__":
    main()
