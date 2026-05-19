import re
import unittest

import usage_dashboard


class DashboardHtmlTest(unittest.TestCase):
    def setUp(self):
        self.html = usage_dashboard.DASHBOARD_HTML

    def test_date_filter_controls_render_before_range_select(self):
        date_filter_pos = self.html.index('class="date-filter"')
        range_pos = self.html.index('id="range"')

        self.assertLess(date_filter_pos, range_pos)
        self.assertIn('class="date-filter-menu"', self.html)
        self.assertRegex(
            self.html,
            r'data-view="day"[\s\S]+data-view="month"[\s\S]+data-view="year"',
        )
        self.assertNotIn('data-mode="multiple"', self.html)
        self.assertNotIn('function toggleDateMode', self.html)
        self.assertNotIn("let selectedDates", self.html)
        self.assertIn("let selectedPeriod", self.html)
        self.assertNotIn('id="dateFilterConfirm"', self.html)
        self.assertNotIn('id="dateFilterFooter"', self.html)
        self.assertNotIn('date-filter-footer', self.html)
        self.assertIn('data-shift="year-prev"', self.html)
        self.assertIn('data-shift="month-prev"', self.html)
        self.assertIn('data-shift="month-next"', self.html)
        self.assertIn('data-shift="year-next"', self.html)
        self.assertIn('data-view="year"', self.html)
        self.assertIn('data-view="month"', self.html)
        self.assertIn('data-view="day"', self.html)
        self.assertIn('id="dateFilterSelection"', self.html)

    def test_date_filter_trigger_uses_input_like_selected_value(self):
        self.assertIn('class="date-filter-icon"', self.html)
        self.assertIn('viewBox="0 0 16 16"', self.html)
        self.assertIn('id="dateFilterClear"', self.html)
        self.assertIn('aria-label="清除日期筛选"', self.html)
        self.assertNotIn('class="date-filter-label"', self.html)
        self.assertIn(".date-filter-trigger.has-value + .date-filter-clear", self.html)
        self.assertIn("function updateDateFilterTrigger()", self.html)
        self.assertIn("trigger.classList.toggle('has-value', Boolean(selectedPeriod));", self.html)
        self.assertIn("clear.hidden = !selectedPeriod;", self.html)

    def test_date_filter_selects_single_day_month_or_year(self):
        self.assertIn("function pickDay(key)", self.html)
        self.assertIn("function pickMonth(month)", self.html)
        self.assertIn("function pickYear(year)", self.html)
        self.assertIn(
            "let selectedPeriod = {type:'day', key: dateKey(today), label: dateKey(today)};",
            self.html,
        )
        self.assertIn("if (target.dataset.date) pickDay(target.dataset.date);", self.html)
        self.assertIn("if (target.dataset.month) pickMonth(Number(target.dataset.month));", self.html)
        self.assertIn("if (target.dataset.year) pickYear(Number(target.dataset.year));", self.html)
        self.assertIn("const startYear = Math.floor(currentYear / 10) * 10;", self.html)
        self.assertIn("${startYear}年 - ${startYear + 9}年", self.html)

    def test_date_filter_view_tabs_are_centered_normal_weight_with_blue_active(self):
        self.assertRegex(
            self.html,
            r"\.date-filter-menu button \{[^}]*display:flex;[^}]*align-items:center;[^}]*justify-content:center;[^}]*color:#1f2937;[^}]*font-weight:400;",
        )
        self.assertRegex(
            self.html,
            r"\.date-filter-menu button\.active \{[^}]*background:#eef6ff;",
        )
        self.assertNotIn(".date-filter-menu button.active { border-color:#d9dee7;", self.html)

    def test_date_filter_day_month_year_selected_cells_share_style(self):
        self.assertIn(".date-cell.selected {", self.html)
        self.assertRegex(
            self.html,
            r"\.date-cell\.selected \{[^}]*border-color:transparent;[^}]*background:#eef6ff;",
        )
        self.assertNotIn(".date-filter-grid.day .date-cell.selected", self.html)
        self.assertNotIn(".date-filter-grid.month .date-cell.selected", self.html)
        self.assertNotIn(".date-filter-grid.year .date-cell.selected", self.html)

    def test_date_filter_month_and_year_cells_share_font_style(self):
        self.assertIn(
            ".date-filter-grid.month .date-cell, .date-filter-grid.year .date-cell {",
            self.html,
        )
        self.assertRegex(
            self.html,
            r"\.date-filter-grid\.month \.date-cell, \.date-filter-grid\.year \.date-cell \{[^}]*font-family:inherit;[^}]*font-size:12px;[^}]*font-weight:400;[^}]*line-height:1;",
        )

    def test_date_filter_is_local_ui_only(self):
        self.assertIn("let selectedPeriod", self.html)
        self.assertIn("function setCalendarView", self.html)

        summary_url_match = re.search(
            r"getJSON\('/api/summary\?range=' \+ encodeURIComponent\(range\)\)",
            self.html,
        )
        self.assertIsNotNone(summary_url_match)
        self.assertNotRegex(self.html, r"/api/summary\?[^']*date")


if __name__ == "__main__":
    unittest.main()
