from pathlib import Path
import datetime as dt
import re
import tempfile
import unittest

import usage_dashboard


ROOT = Path(__file__).resolve().parents[1]


class DashboardHtmlTest(unittest.TestCase):
    def setUp(self):
        self.html = usage_dashboard.DASHBOARD_HTML

    def test_date_filter_controls_render_without_legacy_range_select(self):
        self.assertIn('class="date-filter"', self.html)
        self.assertNotIn('id="range"', self.html)
        self.assertNotIn('最近 1 小时', self.html)
        self.assertNotIn('最近 5 小时', self.html)
        self.assertNotIn('最近 24 小时', self.html)
        self.assertNotIn('最近 7 天', self.html)
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

    def test_date_filter_drives_summary_api_and_reload(self):
        self.assertIn("let selectedPeriod", self.html)
        self.assertIn("function setCalendarView", self.html)
        self.assertIn("function summaryUrl()", self.html)
        self.assertIn("period_type=", self.html)
        self.assertIn("period_key=", self.html)
        self.assertIn("if (target.dataset.date) pickDay(target.dataset.date);", self.html)
        self.assertIn("if (target.dataset.month) pickMonth(Number(target.dataset.month));", self.html)
        self.assertIn("if (target.dataset.year) pickYear(Number(target.dataset.year));", self.html)
        self.assertRegex(self.html, r"function pickDay\(key\)[\s\S]+load\(\);")
        self.assertRegex(self.html, r"function pickMonth\(month\)[\s\S]+load\(\);")
        self.assertRegex(self.html, r"function pickYear\(year\)[\s\S]+load\(\);")

        self.assertNotIn("getJSON('/api/summary?range='", self.html)

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

    def test_top_refresh_button_runs_normal_load(self):
        self.assertIn("$('refresh').onclick = () => load();", self.html)
        self.assertNotIn("$('refresh').onclick = () => refreshQuota();", self.html)

    def test_day_chart_compresses_muted_hours_and_keeps_work_hours_normal(self):
        self.assertIn("function drawDayBars(canvas, rows)", self.html)
        self.assertIn("const pad = {l:44,r:26,t:30,b:44};", self.html)
        self.assertIn("const weakEnd = 8;", self.html)
        self.assertIn("const weakW = plotW * .2;", self.html)
        self.assertIn("const normalW = plotW - weakW;", self.html)
        self.assertIn("drawSegment(rows.slice(0, weakEnd), pad.l, weakW, true);", self.html)
        self.assertIn("drawSegment(rows.slice(weakEnd), pad.l + weakW, normalW, false);", self.html)
        self.assertNotIn("const focusW = plotW - sideW * 2;", self.html)
        self.assertNotIn("drawSegment(rows.slice(focusEnd + 1)", self.html)
        self.assertIn("summary.period?.type === 'day'", self.html)
        self.assertIn("if (value > 0) {", self.html)

    def test_clickable_controls_use_pointer_cursor(self):
        self.assertIn("button, select {", self.html)
        self.assertIn("button, select, .date-cell { cursor:pointer; }", self.html)
        self.assertIn("button:disabled, select:disabled { cursor:not-allowed; }", self.html)
        self.assertIn(".icon-button:disabled { cursor:wait; opacity:.7; }", self.html)

    def test_chart_value_labels_are_compact_and_collision_aware(self):
        self.assertIn("function chartValueLabel(value)", self.html)
        self.assertIn("function drawValueLabel(ctx, text, centerX, barTop, occupiedLabels)", self.html)
        self.assertIn("ctx.measureText(text)", self.html)
        self.assertIn("labelBoxOverlaps(box, occupiedLabels)", self.html)
        self.assertIn("drawValueLabel(ctx, chartValueLabel(value)", self.html)
        self.assertIn("drawValueLabel(ctx, chartValueLabel(r[valueKey])", self.html)
        self.assertNotIn("ctx.fillText(fmt(value)", self.html)
        self.assertNotIn("ctx.fillText(fmt(r[valueKey])", self.html)

    def test_recent_requests_follow_selected_period(self):
        self.assertIn("function requestsUrl()", self.html)
        self.assertIn("period_type=${encodeURIComponent(period.type)}", self.html)
        self.assertIn("period_key=${encodeURIComponent(period.key)}", self.html)
        self.assertIn("getJSON(requestsUrl())", self.html)
        self.assertNotIn("getJSON('/api/requests?limit=120')", self.html)

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


class DashboardPeriodSummaryTest(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = usage_dashboard.BASE_DIR
        self.original_db_path = usage_dashboard.DB_PATH
        self.original_config_path = usage_dashboard.CONFIG_PATH
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        usage_dashboard.BASE_DIR = self.tmp.name
        usage_dashboard.DB_PATH = str(Path(self.tmp.name) / "usage.sqlite")
        usage_dashboard.CONFIG_PATH = str(Path(self.tmp.name) / "config.json")
        Path(usage_dashboard.CONFIG_PATH).write_text(
            '{"management_key":"","cliproxy_config_path":""}',
            encoding="utf-8",
        )
        usage_dashboard.init_db()

    def tearDown(self):
        usage_dashboard.BASE_DIR = self.original_base_dir
        usage_dashboard.DB_PATH = self.original_db_path
        usage_dashboard.CONFIG_PATH = self.original_config_path
        self.tmp.cleanup()

    def insert_usage(self, local_time, total_tokens, event_key):
        ts_local = local_time.replace(tzinfo=usage_dashboard.LOCAL_TZ)
        ts_utc = ts_local.astimezone(dt.timezone.utc)
        with usage_dashboard.db_connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                  event_key,timestamp,ts_epoch,local_date,local_hour,request_id,auth_index,source,
                  provider,model,endpoint,auth_type,api_key_hash,failed,latency_ms,input_tokens,
                  output_tokens,reasoning_tokens,cached_tokens,total_tokens,raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key,
                    ts_utc.isoformat(),
                    ts_utc.timestamp(),
                    ts_local.strftime("%Y-%m-%d"),
                    ts_local.strftime("%Y-%m-%d %H:00"),
                    event_key,
                    "auth-1",
                    "account-1",
                    "openai",
                    "gpt-test",
                    "/v1/chat/completions",
                    "api_key",
                    "hash0001",
                    0,
                    120,
                    total_tokens,
                    0,
                    0,
                    0,
                    total_tokens,
                    "{}",
                ),
            )

    def test_day_period_returns_24_hour_buckets_with_zero_fill(self):
        self.insert_usage(dt.datetime(2026, 5, 19, 8, 30), 100, "day-8")
        self.insert_usage(dt.datetime(2026, 5, 19, 18, 30), 200, "day-18")
        self.insert_usage(dt.datetime(2026, 5, 20, 8, 30), 900, "day-outside")

        summary = usage_dashboard.query_summary("day", "2026-05-19")

        self.assertEqual(summary["period"]["type"], "day")
        self.assertEqual(summary["period"]["key"], "2026-05-19")
        self.assertEqual(summary["summary"]["total_tokens"], 300)
        self.assertEqual(len(summary["hours"]), 24)
        self.assertEqual(summary["hours"][0]["bucket"], "2026-05-19 00:00")
        self.assertEqual(summary["hours"][0]["label"], "00:00")
        self.assertEqual(summary["hours"][7]["total_tokens"], 0)
        self.assertEqual(summary["hours"][8]["total_tokens"], 100)
        self.assertEqual(summary["hours"][18]["total_tokens"], 200)
        self.assertEqual(summary["hours"][23]["bucket"], "2026-05-19 23:00")

    def test_month_period_returns_natural_day_buckets_with_zero_fill(self):
        self.insert_usage(dt.datetime(2026, 5, 1, 9, 0), 100, "month-1")
        self.insert_usage(dt.datetime(2026, 5, 31, 9, 0), 200, "month-31")
        self.insert_usage(dt.datetime(2026, 4, 30, 9, 0), 900, "month-outside")

        summary = usage_dashboard.query_summary("month", "2026-05")

        self.assertEqual(summary["period"]["type"], "month")
        self.assertEqual(summary["period"]["key"], "2026-05")
        self.assertEqual(summary["summary"]["total_tokens"], 300)
        self.assertEqual(len(summary["hours"]), 31)
        self.assertEqual(summary["hours"][0]["bucket"], "2026-05-01")
        self.assertEqual(summary["hours"][0]["total_tokens"], 100)
        self.assertEqual(summary["hours"][1]["total_tokens"], 0)
        self.assertEqual(summary["hours"][30]["bucket"], "2026-05-31")
        self.assertEqual(summary["hours"][30]["total_tokens"], 200)

    def test_year_period_returns_12_month_buckets_with_zero_fill(self):
        self.insert_usage(dt.datetime(2026, 3, 10, 9, 0), 300, "year-3")
        self.insert_usage(dt.datetime(2027, 3, 10, 9, 0), 900, "year-outside")

        summary = usage_dashboard.query_summary("year", "2026")

        self.assertEqual(summary["period"]["type"], "year")
        self.assertEqual(summary["period"]["key"], "2026")
        self.assertEqual(summary["summary"]["total_tokens"], 300)
        self.assertEqual(len(summary["hours"]), 12)
        self.assertEqual(summary["hours"][0]["bucket"], "2026-01")
        self.assertEqual(summary["hours"][0]["total_tokens"], 0)
        self.assertEqual(summary["hours"][2]["bucket"], "2026-03")
        self.assertEqual(summary["hours"][2]["total_tokens"], 300)
        self.assertEqual(summary["hours"][11]["bucket"], "2026-12")

    def test_recent_requests_can_filter_by_day_period(self):
        self.insert_usage(dt.datetime(2026, 5, 19, 8, 30), 100, "request-inside")
        self.insert_usage(dt.datetime(2026, 5, 20, 8, 30), 900, "request-outside")

        requests = usage_dashboard.recent_requests(100, "day", "2026-05-19")

        self.assertEqual([row["request_id"] for row in requests], ["request-inside"])

    def test_recent_requests_can_filter_by_month_period(self):
        self.insert_usage(dt.datetime(2026, 5, 19, 8, 30), 100, "request-may")
        self.insert_usage(dt.datetime(2026, 6, 1, 8, 30), 900, "request-june")

        requests = usage_dashboard.recent_requests(100, "month", "2026-05")

        self.assertEqual([row["request_id"] for row in requests], ["request-may"])

    def test_recent_requests_can_filter_by_year_period(self):
        self.insert_usage(dt.datetime(2026, 5, 19, 8, 30), 100, "request-2026")
        self.insert_usage(dt.datetime(2027, 1, 1, 8, 30), 900, "request-2027")

        requests = usage_dashboard.recent_requests(100, "year", "2026")

        self.assertEqual([row["request_id"] for row in requests], ["request-2026"])


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
