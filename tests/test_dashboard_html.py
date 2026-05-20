from pathlib import Path
import re
import tempfile
import unittest

import usage_dashboard


ROOT = Path(__file__).resolve().parents[1]


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

    def test_date_filter_today_cell_is_neutral_when_another_day_is_selected(self):
        self.assertRegex(
            self.html,
            r"\.date-cell\.today:not\(\.selected\) \{[^}]*color:#2f3a4a;[^}]*font-weight:400;",
        )
        self.assertNotIn(".date-cell.today:not(.selected) { color:#1677ff;", self.html)

    def test_date_filter_view_menu_matches_selected_day_background_width(self):
        self.assertIn(
            ".date-filter-popover { position:absolute; right:0; left:auto; top:calc(100% + 8px); width:438px; min-height:302px; padding:0; display:grid; grid-template-columns:56px minmax(0, 1fr);",
            self.html,
        )
        self.assertRegex(
            self.html,
            r"\.date-filter-popover \{[^}]*width:min\(438px, calc\(100vw - 48px\)\);[^}]*grid-template-columns:56px minmax\(0, 1fr\);",
        )

    def test_date_filter_popover_right_aligns_with_trigger(self):
        self.assertRegex(
            self.html,
            r"\.date-filter-popover \{[^}]*right:0;[^}]*left:auto;[^}]*width:438px;",
        )
        self.assertRegex(
            self.html,
            r"\.date-filter-popover::before \{[^}]*right:40px;[^}]*left:auto;",
        )
        self.assertNotIn(".date-filter-popover { position:absolute; left:0;", self.html)

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

    def test_header_shows_collector_status_and_removes_quota_update_controls(self):
        date_filter_pos = self.html.index('class="date-filter"')
        status_pos = self.html.index('id="collectorStatus"')

        self.assertLess(status_pos, date_filter_pos)
        self.assertIn('class="collector-status"', self.html)
        self.assertIn('/api/collector-status', self.html)
        self.assertNotIn('id="updated"', self.html)
        self.assertNotIn('id="quota"', self.html)
        self.assertNotIn('更新于 ', self.html)

    def test_quota_refresh_lives_in_account_quota_panel_as_icon_button(self):
        quota_panel_pos = self.html.index('账号余量')
        quota_button_pos = self.html.index('id="quotaRefresh"')

        self.assertGreater(quota_button_pos, quota_panel_pos)
        self.assertIn('class="icon-button quota-refresh"', self.html)
        self.assertIn('aria-label="刷新账号余量"', self.html)
        self.assertIn('class="refresh-icon"', self.html)
        self.assertIn('quota-refreshing', self.html)
        self.assertIn("$('quotaRefresh').onclick", self.html)
        self.assertIn("refreshQuota()", self.html)

    def test_api_and_quota_panel_titles_show_dynamic_counts(self):
        self.assertIn('API 详细统计<span class="heading-count" id="apiKeyCount">（0）</span>', self.html)
        self.assertIn('账号余量<span class="heading-count" id="quotaAccountCount">（0）</span>', self.html)
        self.assertIn(".heading-count", self.html)
        self.assertIn("$('apiKeyCount').textContent = `（${summary.apis.length}）`;", self.html)
        self.assertIn("$('quotaAccountCount').textContent = `（${quota.quotas.length}）`;", self.html)

    def test_quota_refresh_keeps_four_hour_auto_and_manual_force(self):
        self.assertIn("getJSON('/api/quota')", self.html)
        self.assertIn("getJSON('/api/quota?force=1')", self.html)
        self.assertNotIn("load(true)", self.html)
        self.assertEqual(usage_dashboard.DEFAULT_CONFIG["quota_refresh_seconds"], 14400)

    def test_collector_refreshes_quota_every_four_hours(self):
        source = Path(usage_dashboard.__file__).read_text(encoding="utf-8")
        collect_body = source.split("def collect_forever():", 1)[1].split("\ndef range_bounds", 1)[0]
        latest_quotas_body = source.split("def latest_quotas", 1)[1].split("\ndef recent_requests", 1)[0]

        self.assertIn("last_quota", collect_body)
        self.assertIn('cfg["quota_refresh_seconds"]', collect_body)
        self.assertIn("refresh_quota(force=True)", collect_body)
        self.assertIn("refresh_quota(force=force)", latest_quotas_body)

    def test_collector_status_api_helpers_exist(self):
        self.assertTrue(hasattr(usage_dashboard, "collector_status"))
        self.assertTrue(hasattr(usage_dashboard, "mark_collector_success"))
        self.assertTrue(hasattr(usage_dashboard, "mark_collector_error"))


class StartDashboardCmdTest(unittest.TestCase):
    def test_start_dashboard_runs_single_python_supervisor_command(self):
        content = (ROOT / "start_dashboard.cmd").read_text(encoding="utf-8")

        self.assertIn("usage_dashboard.py run", content)
        self.assertNotIn("usage_dashboard.py collect", content)
        self.assertNotIn("usage_dashboard.py serve", content)
        self.assertNotIn("Start-Process", content)
        self.assertNotIn("-WindowStyle Hidden", content)
        self.assertNotIn("dashboard.pids", content)
        self.assertNotIn('if /I "%~1"=="stop"', content)

    def test_dashboard_has_run_command_for_single_process_supervision(self):
        self.assertTrue(hasattr(usage_dashboard, "run"))


class ConfigLoadTest(unittest.TestCase):
    def test_load_config_accepts_utf8_bom_config_files(self):
        original_base_dir = usage_dashboard.BASE_DIR
        original_config_path = usage_dashboard.CONFIG_PATH

        with tempfile.TemporaryDirectory() as tmp:
            usage_dashboard.BASE_DIR = tmp
            usage_dashboard.CONFIG_PATH = str(Path(tmp) / "config.json")
            Path(usage_dashboard.CONFIG_PATH).write_text(
                '{"management_key":"secret","quota_refresh_seconds":14400}',
                encoding="utf-8-sig",
            )

            cfg = usage_dashboard.load_config()

        usage_dashboard.BASE_DIR = original_base_dir
        usage_dashboard.CONFIG_PATH = original_config_path

        self.assertEqual(cfg["management_key"], "secret")
        self.assertEqual(cfg["quota_refresh_seconds"], 14400)


if __name__ == "__main__":
    unittest.main()
