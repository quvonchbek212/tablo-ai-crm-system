import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat_charts import _rank_chart_types, chart_for_rows


class ChatChartTests(unittest.TestCase):
    def test_all_five_types_are_ranked_for_each_choice(self):
        kinds = {"bar", "horizontal_bar", "line", "area", "donut"}
        signals = dict(temporal=False, cumulative=False, ranking=False, share=False,
                       complete_composition=True, wide_categories=False)
        for changes, winner in [
            ({}, "bar"),
            ({"wide_categories": True}, "horizontal_bar"),
            ({"share": True, "wide_categories": True}, "donut"),
            ({"share": True, "complete_composition": False}, "bar"),
            ({"temporal": True}, "line"),
            ({"temporal": True, "cumulative": True}, "area"),
            ({"temporal": True, "cumulative": True, "share": True, "ranking": True}, "horizontal_bar"),
        ]:
            with self.subTest(changes=changes):
                ranked = _rank_chart_types(**{**signals, **changes})
                self.assertEqual(len(ranked), 5)
                self.assertEqual(set(ranked), kinds)
                self.assertEqual(ranked[0], winner)

    def test_equipment_ranking_preserves_source_and_exact_large_values(self):
        base = 10 ** 30
        rows = [{"Uskuna": "A", "Sotuv": base}, {"Uskuna": "Z", "Sotuv": base + 1}]
        chart = self.plot("Qaysi IT uskunalar eng ko‘p daromad bergan?", rows)
        self.assertEqual(chart.chart_type, "horizontal_bar")
        self.assertEqual([item["Uskuna"] for item in chart.data], ["Z", "A"])
        self.assertEqual(rows[0]["Uskuna"], "A")

    def plot(self, prompt, rows, **kwargs):
        return chart_for_rows(prompt, rows, title="Sotuv", **kwargs)

    def test_categorical_views_preserve_source_and_rank_in_requested_direction(self):
        rows = [{"city": "C", "amount": 7}, {"city": "B", "amount": 2}, {"city": "A", "amount": 7}]
        for prompt, kind, expected in [
                ("Shaharlar bo'yicha sotuv", "bar", ["A", "C", "B"]),
                ("Eng kam sotuvdan boshlab", "horizontal_bar", ["B", "A", "C"]),
                ("Har bir shaharning sotuvdagi ulushi", "donut", ["A", "C", "B"])]:
            with self.subTest(kind=kind):
                chart = self.plot(prompt, rows)
                self.assertEqual(chart.chart_type, kind)
                self.assertEqual([row[chart.x_axis] for row in chart.data], expected)
                self.assertEqual(chart.y_axis, "amount")
        self.assertEqual([row["city"] for row in rows], ["C", "B", "A"])

    def test_time_views_are_chronological_and_preserve_gaps(self):
        rows = [{"month": 12, "total": 1}, {"month": 3, "total": None}, {"month": 1, "total": 50}]
        for prompt, kind in [("Oylar bo‘yicha daromad", "line"), ("Kumulyativ daromad", "area")]:
            with self.subTest(kind=kind):
                chart = self.plot(prompt, rows)
                self.assertEqual(chart.chart_type, kind)
                self.assertEqual(chart.data, [rows[2], rows[1], rows[0]])
        ranked = self.plot("Eng past daromadli oylar", rows)
        self.assertEqual(ranked.chart_type, "horizontal_bar")
        self.assertEqual(ranked.data, [rows[0], rows[2], rows[1]])

    def test_declared_metric_wins_over_numeric_year_and_id(self):
        rows = [{"id": 1, "year": 2025, "units": 9, "revenue": "80.5"},
                {"id": 2, "year": 2024, "units": 8, "revenue": "110.2"}]
        chart = self.plot("Yillar bo'yicha daromad", rows, x_axis="year", y_axis="revenue")
        self.assertEqual((chart.x_axis, chart.y_axis, chart.chart_type), ("year", "revenue", "line"))
        self.assertEqual(chart.data, rows[::-1])

    def test_invalid_compositions_do_not_become_donuts(self):
        for values, kwargs in [
                ([0, 0], {}), ([-3, 4], {}), ([1, 2], {"truncated": True}),
                ([1, 2], {"sql": "SELECT city, SUM(amount) FROM sales GROUP BY city LIMIT 2"}),
                ([1, 2], {"additive": False}), ([1, None, 2], {})]:
            with self.subTest(values=values, kwargs=kwargs):
                rows = [{"city": str(index), "total": value} for index, value in enumerate(values)]
                self.assertEqual(self.plot("Sotuvdagi ulushlar", rows, **kwargs).chart_type, "bar")
        rates = [{"city": "A", "share_pct": 20}, {"city": "B", "share_pct": 30}]
        self.assertEqual(self.plot("Ulushlar", rates).chart_type, "bar")

    def test_uzbek_ranking_and_explicit_display_order(self):
        rows = [{"oy": "2026-10", "summa": 3}, {"oy": "2026-08", "summa": 2},
                {"oy": "2026-09", "summa": 7}]
        for prompt, expected in [
            ("Summasi eng katta bo'lgan uchta oy qaysi?", [7, 3, 2]),
            ("Summasi eng kichik bo‘lgan uchta oy qaysi?", [2, 3, 7]),
            ("Eng kam tushumli oylarni kamayish tartibida ko'rsat", [7, 3, 2]),
            ("Eng katta uchta summani o‘sish tartibida ko‘rsat", [2, 3, 7]),
        ]:
            with self.subTest(prompt=prompt):
                chart = self.plot(prompt, rows)
                self.assertEqual(chart.chart_type, "horizontal_bar")
                self.assertEqual([row["summa"] for row in chart.data], expected)

    def test_scalar_text_only_and_incompatible_series_have_no_chart(self):
        for rows in [[], [{"amount": 25}], [{"name": "A"}, {"name": "B"}],
                     [{"city": "A", "id": 1}, {"city": "B", "id": 2}],
                     [{"x": "A", "y": True}, {"x": "B", "y": False}],
                     [{"x": "A", "y": float("inf")}, {"x": "B", "y": 3}],
                     [{"x": "A", "y": 1}, {"x": "A", "y": 2}]]:
            with self.subTest(rows=rows):
                self.assertIsNone(self.plot("Taqqosla", rows))
