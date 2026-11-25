"""Regression checks for data-driven interactive blocks and numerical behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from course.build import html_page, inline_text, render_node
from course.interactives import render_book_tables


class CourseBlocksTest(unittest.TestCase):
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
