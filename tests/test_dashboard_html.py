from pathlib import Path
import datetime as dt
import hashlib
import json
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

    def test_dashboard_uses_management_center_visual_tokens(self):
        self.assertIn("--bg:#faf9f5", self.html)
        self.assertIn("--panel:#f0eee8", self.html)
        self.assertIn("--surface:#fffdf9", self.html)
        self.assertIn("--layer-1:var(--bg)", self.html)
        self.assertIn("--layer-2:var(--panel)", self.html)
        self.assertIn("--layer-3:var(--surface)", self.html)
        self.assertIn("--primary:#8b8680", self.html)
        self.assertIn("--green:#10b981", self.html)
        self.assertIn("--amber:#e0aa14", self.html)
        self.assertIn("--red:#c65746", self.html)
        self.assertRegex(
            self.html,
            r"body \{[^}]*background:linear-gradient\(180deg, var\(--bg\), var\(--surface-soft\)\);",
        )
        self.assertRegex(
            self.html,
            r"\.panel \{[^}]*background:var\(--panel\);[^}]*border:1px solid var\(--line\);[^}]*border-radius:12px;",
        )
        self.assertRegex(
            self.html,
            r"button, select \{[^}]*background:var\(--panel\);[^}]*border-radius:8px;[^}]*font-weight:600;",
        )
        self.assertRegex(
            self.html,
            r"h1 \{[^}]*font-family:\"Arial Black\"[^}]*font-weight:900;[^}]*letter-spacing:0;",
        )
        self.assertIn("font-size:22px;", self.html)
        self.assertIn("--row-hover:rgba(139, 134, 128, .08)", self.html)
        self.assertIn(".api-card { position:relative; border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--surface); }", self.html)

    def test_api_cards_show_name_and_metrics_on_one_line(self):
        self.assertRegex(
            self.html,
            r"\.api-card \{[^}]*display:flex;[^}]*align-items:center;[^}]*gap:8px;[^}]*flex-wrap:wrap;",
        )
        self.assertRegex(
            self.html,
            r"\.api-key \{[^}]*font-weight:700;[^}]*margin-bottom:0;",
        )

    def test_api_details_and_quota_panels_swap_positions(self):
        quota_panel_pos = self.html.index('id="quotaAccountCount"')
        model_panel_pos = self.html.index('id="modelChart"')
        accounts_panel_pos = self.html.index('id="accounts"')
        api_panel_pos = self.html.index('id="apiDetails"')

        self.assertLess(quota_panel_pos, model_panel_pos)
        self.assertLess(accounts_panel_pos, api_panel_pos)
        self.assertLess(quota_panel_pos, api_panel_pos)

    def test_quota_percentages_use_red_yellow_green_status_colors(self):
        self.assertIn("const cls = v < 30 ? 'bad' : (v < 70 ? 'warn' : 'good');", self.html)
        self.assertNotIn("v <= 20 ? 'bad'", self.html)
        self.assertNotIn("const used = 100 - v;", self.html)
        self.assertIn('class="quota-percent ${cls}"', self.html)
        self.assertIn(".quota-percent.good { color:var(--green); }", self.html)
        self.assertIn(".quota-percent.warn { color:var(--amber); }", self.html)
        self.assertIn(".quota-percent.bad { color:var(--red); }", self.html)

    def test_quota_table_shows_remaining_days_without_horizontal_scroll(self):
        self.assertIn("<th>天数</th>", self.html)
        self.assertNotIn("<th>剩余天数</th>", self.html)
        self.assertIn('class="scroll quota-scroll"', self.html)
        self.assertIn('class="quota-table"', self.html)
        self.assertIn(".quota-scroll { overflow-x:hidden;", self.html)
        self.assertIn(".quota-table { table-layout:fixed;", self.html)
        self.assertIn(".quota-table th:nth-child(5), .quota-table td:nth-child(5) { width:16%; }", self.html)
        self.assertIn(".quota-table th:nth-child(6), .quota-table td:nth-child(6) { width:7%; text-align:center; }", self.html)
        self.assertIn("formatRemainingDays(q.subscription_remaining_days)", self.html)
        self.assertIn("function formatRemainingDays(days)", self.html)
        self.assertIn("return String(Math.max(0, Math.ceil(n)));", self.html)
        self.assertNotIn("}天`", self.html)

    def test_account_consumption_panel_shows_count_days_and_uses_compact_scroll(self):
        self.assertIn(
            '<h2>账号消耗<span class="heading-count" id="accountCount">（0）</span></h2>',
            self.html,
        )
        self.assertIn('class="panel table-panel accounts-panel"', self.html)
        self.assertIn('class="scroll accounts-scroll"', self.html)
        self.assertIn('class="accounts-table"', self.html)
        self.assertIn("<th>天数</th>", self.html)
        self.assertIn(".accounts-panel { min-height:302px; }", self.html)
        self.assertIn(".accounts-scroll { max-height:260px; overflow:auto; }", self.html)
        self.assertIn(".accounts-table { table-layout:fixed; font-size:12px; }", self.html)
        self.assertIn("$('accountCount').textContent = `（${summary.accounts.length}）`;", self.html)
        self.assertIn("formatRemainingDays(a.subscription_remaining_days)", self.html)

    def test_tables_and_statuses_follow_management_pill_style(self):
        self.assertIn('<div class="panel table-panel accounts-panel"><h2>账号消耗', self.html)
        self.assertIn('<div class="panel table-panel"><div class="panel-head"><h2>账号余量', self.html)
        self.assertIn('<section class="panel table-panel" style="margin-top:14px">', self.html)
        self.assertIn(".table-panel { background:var(--panel); }", self.html)
        self.assertIn("thead, tbody, tr, th, td { background:var(--surface); }", self.html)
        self.assertRegex(
            self.html,
            r"th, td \{[^}]*background:var\(--surface\);[^}]*background-clip:padding-box;",
        )
        self.assertIn("th { color:var(--muted); font-weight:700; }", self.html)
        self.assertIn("tbody tr:hover td { background:var(--row-hover); }", self.html)
        self.assertRegex(
            self.html,
            r"\.request-status \{[^}]*display:inline-flex;[^}]*border:1px solid var\(--line\);[^}]*border-radius:9999px;",
        )
        self.assertIn(".status { display:inline-flex; align-items:center; gap:6px; color:var(--green); background:transparent; border:0; border-radius:0; padding:0; font-weight:700; }", self.html)
        self.assertIn(".status.bad { color:var(--red); }", self.html)
        self.assertIn('class="status ${q.allowed ? \'\' : \'bad\'}"', self.html)
        self.assertIn(".request-status.success { color:var(--green-text); background:var(--green-bg); border-color:var(--green-border); }", self.html)
        self.assertIn(".request-status.failed { color:var(--red-text); background:var(--red-bg); border-color:var(--red-border); }", self.html)

    def test_chart_colors_use_management_center_palette(self):
        self.assertIn("drawBars($('hourChart'), summary.hours, 'label', 'total_tokens', '#10b981');", self.html)
        self.assertIn("ctx.fillStyle = muted ? 'rgba(16, 185, 129, .38)' : '#10b981';", self.html)
        self.assertIn("ctx.fillStyle = '#10b981'; fillRoundedRect(ctx, labelW, y+6, barW, 14, 4);", self.html)

    def test_chart_bars_are_rounded(self):
        self.assertIn("function fillRoundedRect(ctx, x, y, width, height, radius)", self.html)
        self.assertIn("ctx.roundRect(x, y, width, height, r);", self.html)
        self.assertIn("ctx.fillStyle = color; fillRoundedRect(ctx, x, y, bw, bh, 3);", self.html)
        self.assertIn("fillRoundedRect(ctx, x, y, bw, bh, 3);", self.html)
        self.assertIn("fillRoundedRect(ctx, labelW, y+6, barW, 14, 4);", self.html)

    def test_date_filter_view_tabs_are_centered_normal_weight_with_neutral_active(self):
        self.assertRegex(
            self.html,
            r"\.date-filter-menu button \{[^}]*display:flex;[^}]*align-items:center;[^}]*justify-content:center;[^}]*color:var\(--text\);[^}]*font-weight:400;",
        )
        self.assertRegex(
            self.html,
            r"\.date-filter-menu button\.active \{[^}]*background:rgba\(139, 134, 128, \.14\);",
        )
        self.assertNotIn(".date-filter-menu button.active { border-color:#d9dee7;", self.html)

    def test_date_filter_day_month_year_selected_cells_share_style(self):
        self.assertIn(".date-cell.selected {", self.html)
        self.assertRegex(
            self.html,
            r"\.date-cell\.selected \{[^}]*color:#fff;[^}]*border-color:var\(--primary\);[^}]*background:var\(--primary\);",
        )
        self.assertNotIn(".date-filter-grid.day .date-cell.selected", self.html)
        self.assertNotIn(".date-filter-grid.month .date-cell.selected", self.html)
        self.assertNotIn(".date-filter-grid.year .date-cell.selected", self.html)

    def test_date_filter_outside_days_are_dim_without_fill(self):
        self.assertIn(".date-cell.outside { color:var(--muted-soft); background:transparent; }", self.html)
        self.assertNotIn(".date-cell.outside { color:var(--muted-soft); background:var(--surface-soft); }", self.html)

    def test_date_filter_today_cell_is_neutral_when_another_day_is_selected(self):
        self.assertRegex(
            self.html,
            r"\.date-cell\.today:not\(\.selected\) \{[^}]*color:var\(--text\);[^}]*font-weight:400;",
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

    def test_manual_refreshes_show_animated_toast_feedback(self):
        self.assertIn('id="toastStack"', self.html)
        self.assertIn("function showToast(type, message)", self.html)
        self.assertIn("@keyframes toast-in", self.html)
        self.assertIn("@keyframes toast-out", self.html)
        self.assertIn("toast.remove()", self.html)
        self.assertIn("async function refreshDashboard()", self.html)
        self.assertIn("showToast('success', '刷新成功');", self.html)
        self.assertIn("showToast('error', '刷新失败');", self.html)
        self.assertIn("showToast('success', '账号余量刷新成功');", self.html)
        self.assertIn("showToast('error', '账号余量刷新失败');", self.html)
        self.assertIn("$('refresh').onclick = () => refreshDashboard();", self.html)

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

    def test_top_refresh_button_forces_quota_refresh_with_dashboard_data(self):
        self.assertIn("$('refresh').onclick = () => refreshDashboard();", self.html)
        self.assertRegex(self.html, r"async function refreshDashboard\(\)[\s\S]+await load\(\{forceQuota: true\}\);")
        self.assertRegex(
            self.html,
            r"async function load\(\{forceQuota = false\} = \{\}\)[\s\S]+forceQuota \? getJSON\('/api/quota\?force=1'\) : getJSON\('/api/quota'\)",
        )
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

    def test_recent_request_latency_displays_seconds(self):
        self.assertIn("function formatLatencySeconds(ms)", self.html)
        self.assertIn("${formatLatencySeconds(r.latency_ms)}", self.html)
        self.assertNotIn("${fmt(r.latency_ms)}ms", self.html)

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
        self.original_auth_dir = usage_dashboard.AUTH_DIR
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        usage_dashboard.BASE_DIR = self.tmp.name
        usage_dashboard.DB_PATH = str(Path(self.tmp.name) / "usage.sqlite")
        usage_dashboard.CONFIG_PATH = str(Path(self.tmp.name) / "config.json")
        usage_dashboard.AUTH_DIR = str(Path(self.tmp.name) / "auth")
        Path(usage_dashboard.AUTH_DIR).mkdir()
        Path(usage_dashboard.CONFIG_PATH).write_text(
            '{"management_key":"","cliproxy_config_path":""}',
            encoding="utf-8",
        )
        usage_dashboard.init_db()

    def tearDown(self):
        usage_dashboard.BASE_DIR = self.original_base_dir
        usage_dashboard.DB_PATH = self.original_db_path
        usage_dashboard.CONFIG_PATH = self.original_config_path
        usage_dashboard.AUTH_DIR = self.original_auth_dir
        self.tmp.cleanup()

    def write_auth(self, email, filename="codex-current.json", token="token", expired=None):
        payload = {"email": email, "access_token": token}
        if expired is not None:
            payload["expired"] = expired
        Path(usage_dashboard.AUTH_DIR, filename).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

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

    def insert_quota_snapshot(self, email, ts_epoch=None):
        ts_epoch = usage_dashboard.time.time() if ts_epoch is None else ts_epoch
        with usage_dashboard.db_connect() as conn:
            conn.execute(
                """
                INSERT INTO quota_snapshots (
                  timestamp,ts_epoch,email,plan,allowed,limit_reached,
                  primary_used_percent,primary_remaining_percent,primary_reset_at,
                  secondary_used_percent,secondary_remaining_percent,secondary_reset_at,
                  credits_balance,raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-05-20T00:00:00+00:00",
                    ts_epoch,
                    email,
                    "plus",
                    1,
                    0,
                    10,
                    90,
                    "2026-05-20 12:00:00",
                    20,
                    80,
                    "2026-05-27 12:00:00",
                    "0",
                    '{"internal":"not-for-api"}',
                ),
            )

    def test_insert_usage_redacts_api_key_from_raw_json(self):
        secret_key = "test-api-key-value"
        raw = json.dumps(
            {
                "timestamp": "2026-05-19T08:30:00Z",
                "request_id": "request-with-key",
                "source": "account-1",
                "model": "gpt-test",
                "auth_type": "api_key",
                "api_key": secret_key,
                "tokens": {"total_tokens": 42},
            },
            ensure_ascii=False,
        )

        inserted = usage_dashboard.insert_usage([raw])

        self.assertEqual(inserted, 1)
        with usage_dashboard.db_connect() as conn:
            row = conn.execute(
                "SELECT api_key_hash, raw_json FROM usage_events WHERE event_key = ?",
                ("request-with-key",),
            ).fetchone()
        self.assertEqual(row["api_key_hash"], hashlib.sha256(secret_key.encode()).hexdigest()[:12])
        self.assertNotIn(secret_key, row["raw_json"])
        self.assertEqual(json.loads(row["raw_json"])["api_key"], "[redacted]")

    def test_latest_quotas_do_not_expose_raw_json(self):
        self.write_auth("account@example.test")
        self.insert_quota_snapshot("account@example.test")

        quotas = usage_dashboard.latest_quotas()

        self.assertEqual(len(quotas), 1)
        self.assertNotIn("raw_json", quotas[0])

    def test_latest_quotas_include_remaining_days_from_oauth_expired(self):
        expires_at = dt.datetime.now(usage_dashboard.LOCAL_TZ) + dt.timedelta(days=3, minutes=5)
        self.write_auth(
            "account@example.test",
            expired=expires_at.isoformat(timespec="seconds"),
        )
        self.insert_quota_snapshot("account@example.test")

        quotas = usage_dashboard.latest_quotas()

        self.assertEqual(quotas[0]["subscription_expired_at"], expires_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(quotas[0]["subscription_remaining_days"], 4)

    def test_latest_quotas_only_return_current_auth_accounts(self):
        self.write_auth("current@example.test")
        self.insert_quota_snapshot("current@example.test")
        self.insert_quota_snapshot("removed@example.test")

        quotas = usage_dashboard.latest_quotas()

        self.assertEqual([quota["email"] for quota in quotas], ["current@example.test"])

    def test_latest_quota_age_requires_snapshots_for_all_current_accounts(self):
        self.write_auth("with-snapshot@example.test", filename="codex-with-snapshot.json")
        self.write_auth("missing-snapshot@example.test", filename="codex-missing-snapshot.json")
        self.insert_quota_snapshot("with-snapshot@example.test")

        age = usage_dashboard.latest_quota_age(usage_dashboard.current_quota_account_names())

        self.assertIsNone(age)

    def test_summary_accounts_include_remaining_days_from_matching_oauth_account(self):
        expires_at = dt.datetime.now(usage_dashboard.LOCAL_TZ) + dt.timedelta(days=2, minutes=5)
        self.write_auth(
            "account-1",
            expired=expires_at.isoformat(timespec="seconds"),
        )
        self.insert_usage(dt.datetime(2026, 5, 19, 8, 30), 100, "account-days")

        summary = usage_dashboard.query_summary("day", "2026-05-19")

        self.assertEqual(summary["accounts"][0]["account"], "account-1")
        self.assertEqual(
            summary["accounts"][0]["subscription_expired_at"],
            expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.assertEqual(summary["accounts"][0]["subscription_remaining_days"], 3)

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
        self.assertIn('if not exist "%PY%" set "PY=python"', content)
        self.assertNotIn("usage_dashboard.py collect", content)
        self.assertNotIn("usage_dashboard.py serve", content)
        self.assertNotIn("Start-Process", content)
        self.assertNotIn("-WindowStyle Hidden", content)
        self.assertNotIn("dashboard.pids", content)
        self.assertNotIn('if /I "%~1"=="stop"', content)

    def test_dashboard_has_run_command_for_single_process_supervision(self):
        self.assertTrue(hasattr(usage_dashboard, "run"))

    def test_run_uses_collector_watchdog(self):
        source = Path(usage_dashboard.__file__).read_text(encoding="utf-8")
        run_body = source.split("def run():", 1)[1].split("\ndef print_report", 1)[0]

        self.assertTrue(hasattr(usage_dashboard, "start_collector_watchdog"))
        self.assertIn("start_collector_watchdog()", run_body)
        self.assertNotIn("threading.Thread(target=collect_forever", run_body)

    def test_collector_watchdog_restarts_exited_target(self):
        stop_event = usage_dashboard.threading.Event()
        attempts = []

        def target():
            attempts.append(1)
            if len(attempts) >= 2:
                stop_event.set()

        returned_event, watchdog = usage_dashboard.start_collector_watchdog(
            restart_delay_seconds=0,
            target=target,
            stop_event=stop_event,
        )
        watchdog.join(1)

        returned_event.set()
        watchdog.join(1)

        self.assertIs(returned_event, stop_event)
        self.assertGreaterEqual(len(attempts), 2)
        self.assertFalse(watchdog.is_alive())


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
