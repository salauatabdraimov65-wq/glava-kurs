"""Regression checks for data-driven interactive blocks and numerical behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from course.build import html_page, inline_text, render_map, render_node
from course.domain_theme import art_css, contrast, load_domain, readable_on
from course.interactives import render_book_tables


class CourseBlocksTest(unittest.TestCase):
    def test_domain_override_uses_its_own_art_and_plain_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="домен с пробелами ") as temporary:
            folder = Path(temporary) / "geology"
            folder.mkdir()
            (folder / "styleguide.json").write_text('{"course":{"accent":"#246378"}}', encoding="utf-8")
            domain = load_domain(str(folder))
            self.assertEqual(art_css(domain["folder"]), ("", []))
            (folder / "art").mkdir()
            (folder / "art" / "hills.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            css, names = art_css(domain["folder"])
            self.assertEqual(names, ["hills"])
            self.assertIn("--art-hills:", css)
            page = html_page({"nodes": [{"id": "new", "title": "Новая тема"}]}, {}, {}, {}, folder, domain=str(folder))
            self.assertIn('data-art="hills"', page)
            self.assertIn("--accent:#246378", page)
            self.assertNotIn("--art-hero:", page)

    def test_map_wraps_long_titles_without_losing_or_inserting_markup(self) -> None:
        title = "Незнакомое очень длинное название шага для узкого экрана <script>"
        rendered = render_map([{"id": "a", "title": title, "links": [{"target": "b", "type": "prerequisite"}]},
                               {"id": "b", "title": "Второй шаг"}])
        self.assertIn('viewBox="0 0 360 ', rendered)
        self.assertGreater(rendered.count("<tspan"), 3)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("предпосылка", rendered)

    def test_caption_contrast_covers_paper_and_tinted_panels(self) -> None:
        for paper, panels in [("#F8F3E8", ("#F1E9DA", "#F7E5CF")),
                              ("#0F1419", ("#1A2129", "#383025"))]:
            color = readable_on("#F0A35E", paper, surfaces=panels)
            for background in (paper, *panels):
                self.assertGreaterEqual(contrast(color, background), 4.6)

    def test_only_used_decorative_images_are_embedded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "art").mkdir()
            for name in ("hills.svg", "unused.svg"):
                (folder / "art" / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            css, names = art_css(str(folder), slots=("hills", "hills-mobile"))
            self.assertEqual(names, ["hills"])
            self.assertNotIn("--art-unused:", css)
            page = html_page({"nodes": [{"id": "new", "title": "Тема"}]}, {}, {}, {}, folder, domain=str(folder))
            self.assertIn('data-art="hills"', page)
            self.assertNotIn("--art-unused:", page)
            self.assertNotIn('class="strata-scene"', page)
            self.assertNotIn('class="section-svg"', page)

    def test_incomplete_ocr_formula_remains_readable(self) -> None:
        self.assertEqual(inline_text("$p_{0}"), "$p_{0}")
        self.assertIn("p<sub>0</sub>", inline_text("$p_{0}$ и $"))

    def test_complete_table_escapes_html_and_keeps_formulas_and_empty_cells(self) -> None:
        tables = [{"number": 9, "pages": [14, 15], "caption": "<script>", "ocr_note": "Пустая ячейка",
                   "markdown": "| $p_{0}$ | B | C |\n| --- | --- | --- |\n| <img> | | 7 \\| 8 |\n| 1 | 2 | 3 |"}]
        rendered = render_book_tables(tables, [], inline_text)
        self.assertIn("Таблица 9, стр. 14, 15", rendered)
        self.assertIn("p<sub>0</sub>", rendered)
        self.assertIn("&lt;img&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertEqual(rendered.count("<td>"), 6)
        self.assertIn("<td></td>", rendered)
        self.assertIn("7 | 8", rendered)
        self.assertIn("Пустая ячейка", rendered)

    def test_routing_controls_templates_with_unfamiliar_content(self) -> None:
        node = {"id": "unknown.step", "pages": [3, 4], "title": "Новая тема", "layers": {},
                "decision_on_data": {"features": ["Наблюдение X", "Наблюдение Y"], "rule": "Правило Z",
                                     "conclusion": "Вывод Q", "counterexample": "Граница W"}}
        plain = render_node(node, [], [], {}, Path("."))
        self.assertNotIn('class="data-practicum"', plain)
        routes = {"routes": [{"node_id": node["id"], "format": "практикум на данных", "template": "pressure_explorer"}]}
        rendered = render_node(node, [], [], routes, Path("."))
        self.assertIn("Практикум на данных", rendered)
        self.assertEqual(rendered.count('name="feature"'), 2)
        self.assertEqual(rendered.count('data-correct="true"'), 1)
        self.assertIn("Правило Z", rendered)
        self.assertIn("Граница W", rendered)
        self.assertIn("стр. 3, 4", rendered)
        self.assertIn('class="pressure-chart"', rendered)
        self.assertNotIn("<h2>По шагам</h2>", rendered)

    def test_optional_files_and_fields_can_be_absent(self) -> None:
        page = html_page({"nodes": [{"id": "empty", "title": "Без дополнений"}]}, {}, {}, {}, Path("."))
        self.assertNotIn('class="pressure-explorer"', page)
        self.assertNotIn('class="book-table-block"', page)
        self.assertNotIn('class="data-practicum"', page)
        self.assertIn("Без дополнений", page)

    def test_pressure_series_approximation_and_diffusion(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required to verify the browser's numerical code")
        javascript = Path(__file__).with_name("interactives.js").read_text(encoding="utf-8")
        javascript += "\nconsole.log(JSON.stringify({e:[0.01,1,1.000001,2,10,1000].map(pressureE1), t:[0,1000].map(pressureSeconds), p:[pressureAt(0,1,10),pressureAt(0,1,2592000),pressureAt(50,1,10),pressureAt(50,1,2592000),pressureAt(50,2,100),pressureAt(50,1,200)]}));"
        run = subprocess.run([node], input=javascript, text=True, encoding="utf-8", capture_output=True, check=True)
        result = json.loads(run.stdout)
        for actual, expected in zip(result["e"][:5], [4.0379295765, .2193839344, .2193835665, .0489005107, .00000415697]):
            self.assertAlmostEqual(actual, expected, delta=3e-5)
        self.assertEqual(result["e"][-1], 0)
        self.assertAlmostEqual(result["t"][0], 10)
        self.assertAlmostEqual(result["t"][1], 30 * 86400)
        near_early, near_late, far_early, far_late, same1, same2 = result["p"]
        self.assertGreater(near_early, near_late)
        self.assertGreater(far_early, far_late)
        self.assertGreater(far_late, near_late)
        self.assertEqual(same1, same2)


if __name__ == "__main__":
    unittest.main()
