/* =========================================================================
   统计分析页面（需求十五）
   15.1 省份分布（柱状图）  15.2 届别趋势（折线图）
   15.3 岗位类别（饼图）    15.4 学院分布（横向柱状图）
   + 学历层次（环形图） + 去向城市（横向柱状图） + 字段填充率
   ECharts 通过 CDN 加载；若不可用（离线环境）自动降级为纯 CSS 条形图。
   ========================================================================= */
(function () {
  'use strict';
  const IRS = window.IRS;
  const $ = (sel) => document.querySelector(sel);

  const PALETTE = ['#1d4ed8', '#6d8dfb', '#4f9cf9', '#38bdf8', '#2dd4bf',
    '#a3e635', '#fbbf24', '#fb923c', '#f87171', '#c084fc'];
  const chartRefs = [];

  /* ------------------------- ECharts 按需异步加载 -------------------------
     不要用阻塞 <script src="cdn...">：CDN 挂起时 DOMContentLoaded 永不触发，
     整页会一直停在「加载中」。这里异步注入 + 超时，失败就走降级渲染。 */
  const ECHARTS_URL = 'https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js';
  const ECHARTS_TIMEOUT = 2500;
  let hasECharts = typeof window.echarts !== 'undefined';

  function loadECharts(timeout) {
    if (hasECharts) return Promise.resolve(true);
    return new Promise((resolve) => {
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        hasECharts = ok;
        resolve(ok);
      };
      const timer = setTimeout(() => finish(false), timeout);
      const script = document.createElement('script');
      script.src = ECHARTS_URL;
      script.async = true;
      script.onload = () => { clearTimeout(timer); finish(typeof window.echarts !== 'undefined'); };
      script.onerror = () => { clearTimeout(timer); finish(false); };
      document.head.appendChild(script);
    });
  }

  /* --------------------------- 通用图表封装 --------------------------- */
  function makeChart(domId, title) {
    const dom = document.getElementById(domId);
    if (!dom) return null;
    if (!hasECharts) return null;
    const chart = window.echarts.init(dom, null, { renderer: 'canvas' });
    chartRefs.push(chart);
    dom._title = title;
    return chart;
  }

  function resizeAll() { chartRefs.forEach((c) => c && c.resize()); }

  const AXIS_LABEL = { color: '#4b5563', fontSize: 12 };
  const SPLIT_LINE = { lineStyle: { color: '#eef1f5' } };
  const GRID = { left: 12, right: 20, top: 34, bottom: 8, containLabel: true };

  /* --------------------------- 降级渲染 --------------------------- */
  function fallbackBar(domId, rows, unit) {
    const dom = document.getElementById(domId);
    if (!dom) return;
    const max = Math.max(1, ...rows.map((r) => r.count));
    dom.innerHTML = '<div class="fallback-list">' + (rows.length ? rows.map((r) => `
      <div class="fallback-row">
        <span class="fl" title="${IRS.esc(r.value)}">${IRS.esc(r.value)}</span>
        <span class="fb"><i style="width:${(r.count / max) * 100}%"></i></span>
        <span class="fv">${IRS.num(r.count)}${unit || ''}</span>
      </div>`).join('') : '<div style="color:#9aa1ab;padding:12px 0;">暂无数据</div>') + '</div>';
  }

  /* --------------------------- 各图表 --------------------------- */
  function renderProvince(rows) {
    const chart = makeChart('chart-province');
    const data = rows.slice().reverse();          // 横向柱状图：从下往上由大到小
    if (!chart) return fallbackBar('chart-province', rows);
    chart.setOption({
      color: PALETTE,
      grid: GRID,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      yAxis: {
        type: 'category',
        data: data.map((r) => r.value),
        axisLabel: Object.assign({}, AXIS_LABEL, { interval: 0 }),
        axisLine: { lineStyle: { color: '#e3e6ea' } },
      },
      series: [{
        type: 'bar',
        data: data.map((r) => r.count),
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: '#6b7280', fontSize: 11 },
      }],
    });
  }

  function renderCohort(rows) {
    const chart = makeChart('chart-cohort');
    if (!chart) return fallbackBar('chart-cohort', rows, ' 人');
    chart.setOption({
      color: ['#1d4ed8'],
      grid: GRID,
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: rows.map((r) => r.value + '届'),
        axisLabel: AXIS_LABEL,
        axisLine: { lineStyle: { color: '#e3e6ea' } },
      },
      yAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      series: [{
        type: 'line',
        smooth: true,
        symbolSize: 7,
        data: rows.map((r) => r.count),
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: 'rgba(29,78,216,.28)' }, { offset: 1, color: 'rgba(29,78,216,.02)' }],
          },
        },
        label: { show: true, position: 'top', color: '#6b7280', fontSize: 11 },
      }],
    });
  }

  function renderCategory(rows) {
    const chart = makeChart('chart-category');
    if (!chart) return fallbackBar('chart-category', rows, ' 人');
    chart.setOption({
      color: PALETTE,
      tooltip: { trigger: 'item', formatter: '{b}：{c} 人（{d}%）' },
      legend: { bottom: 0, icon: 'circle', textStyle: { color: '#4b5563', fontSize: 12 } },
      series: [{
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}\n{d}%', color: '#4b5563', fontSize: 12 },
        data: rows.map((r) => ({ name: r.value, value: r.count })),
      }],
    });
  }

  function renderCollege(rows) {
    const chart = makeChart('chart-college');
    const data = rows.slice().reverse();
    if (!chart) return fallbackBar('chart-college', rows);
    chart.setOption({
      grid: GRID,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      yAxis: {
        type: 'category',
        data: data.map((r) => r.value),
        axisLabel: Object.assign({}, AXIS_LABEL, { interval: 0 }),
        axisLine: { lineStyle: { color: '#e3e6ea' } },
      },
      series: [{
        type: 'bar',
        data: data.map((r) => r.count),
        barMaxWidth: 16,
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: {
            type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [{ offset: 0, color: '#6d8dfb' }, { offset: 1, color: '#1d4ed8' }],
          },
        },
        label: { show: true, position: 'right', color: '#6b7280', fontSize: 11 },
      }],
    });
  }

  function renderDegree(rows) {
    const chart = makeChart('chart-degree');
    if (!chart) return fallbackBar('chart-degree', rows, ' 人');
    chart.setOption({
      color: ['#38bdf8', '#6d8dfb', '#1d4ed8'],
      tooltip: { trigger: 'item', formatter: '{b}：{c} 人（{d}%）' },
      legend: { bottom: 0, icon: 'circle', textStyle: { color: '#4b5563', fontSize: 12 } },
      series: [{
        type: 'pie',
        radius: ['50%', '70%'],
        center: ['50%', '44%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b} {c}', color: '#4b5563', fontSize: 12 },
        data: rows.map((r) => ({ name: r.value, value: r.count })),
      }],
    });
  }

  function renderCity(rows) {
    const chart = makeChart('chart-city');
    const data = rows.slice().reverse();
    if (!chart) return fallbackBar('chart-city', rows);
    chart.setOption({
      color: ['#2dd4bf'],
      grid: GRID,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      yAxis: {
        type: 'category',
        data: data.map((r) => r.value),
        axisLabel: Object.assign({}, AXIS_LABEL, { interval: 0 }),
        axisLine: { lineStyle: { color: '#e3e6ea' } },
      },
      series: [{
        type: 'bar',
        data: data.map((r) => r.count),
        barMaxWidth: 16,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: '#6b7280', fontSize: 11 },
      }],
    });
  }

  function renderFillRate(fill) {
    const labels = Object.keys(fill || {});
    const values = labels.map((k) => Math.round((fill[k] || 0) * 1000) / 10);
    const chart = makeChart('chart-fill');
    const rows = labels.map((k) => ({ value: k, count: values[labels.indexOf(k)] }));
    if (!chart) return fallbackBar('chart-fill', rows, '%');
    chart.setOption({
      grid: { left: 12, right: 30, top: 20, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', formatter: (p) => `${p[0].name}：${p[0].value}%` },
      xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', color: '#4b5563', fontSize: 12 }, splitLine: SPLIT_LINE },
      yAxis: {
        type: 'category',
        data: labels,
        axisLabel: Object.assign({}, AXIS_LABEL, { interval: 0 }),
        axisLine: { lineStyle: { color: '#e3e6ea' } },
      },
      series: [{
        type: 'bar',
        data: values,
        barMaxWidth: 14,
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: (p) => (p.value >= 80 ? '#34d399' : p.value >= 50 ? '#fbbf24' : '#f87171'),
        },
        label: { show: true, position: 'right', formatter: '{c}%', color: '#6b7280', fontSize: 11 },
      }],
    });
  }

  /* --------------------------- KPI 卡片 --------------------------- */
  function renderKpis(data) {
    const s = data.summary || {};
    const cards = [
      { label: '去向记录', value: s.records },
      { label: '通知图片', value: s.images },
      { label: '涉及省份', value: s.provinces },
      { label: '涉及学院', value: s.colleges },
      { label: '去向城市', value: s.cities },
      { label: '届别数量', value: s.cohorts },
    ];
    $('#kpis').innerHTML = cards.map((c) => `
      <div class="kpi">
        <div class="label">${IRS.esc(c.label)}</div>
        <div class="value">${IRS.num(c.value || 0)}</div>
      </div>`).join('');
  }

  function renderLegends() {
    document.querySelectorAll('.legend').forEach((el) => el.remove());
  }

  /* --------------------------- 启动 --------------------------- */
  async function boot() {
    try {
      // 数据请求与 ECharts 加载并行：即使 CDN 完全不可达，也最多等 ECHARTS_TIMEOUT 后降级渲染。
      const [data] = await Promise.all([
        IRS.getJSON('/stats', { top_n: 15 }),
        loadECharts(ECHARTS_TIMEOUT),
      ]);
      renderKpis(data);
      renderProvince(data.province || []);
      renderCohort(data.cohort || []);
      renderCategory(data.position_category || []);
      renderCollege(data.college || []);
      renderDegree(data.degree_level || []);
      renderCity(data.city || []);
      renderFillRate(data.fill_rate || {});

      const s = data.summary || {};
      $('#stats-sub').textContent = hasECharts
        ? `数据范围：全部 ${IRS.num(s.records)} 条记录 / ${IRS.num(s.images)} 张通知图片`
        : `数据范围：全部 ${IRS.num(s.records)} 条记录 / ${IRS.num(s.images)} 张通知图片`
          + '（ECharts CDN 不可用，已降级为内置条形图）';

      window.addEventListener('resize', IRS.debounce(resizeAll, 150));
    } catch (err) {
      $('#stats-sub').textContent = '加载失败：' + err.message;
      IRS.toast('统计加载失败：' + err.message, 4000);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();   // 脚本被延迟/晚于 DOMContentLoaded 执行时的兜底
  }
})();
