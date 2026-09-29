/* ==========================================================================
   统计分析页
   --------------------------------------------------------------------------
   ECharts 从 CDN 异步加载：
   * 不能写成阻塞式 <script>，否则断网 / CDN 被墙时整页卡住；
   * 2.5s 内没加载出来就放弃，改为渲染纯 CSS 条形列表（.fallback-list）。
   ========================================================================== */
(function () {
  "use strict";

  var IRS = window.IRS;
  var $ = function (sel) { return document.querySelector(sel); };

  var ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";
  var CDN_TIMEOUT = 2500;
  var BLUES = ["#2866a5", "#4c8fca", "#7db4dd", "#a9cbe4", "#1d4f78", "#dfaa61", "#73ae8b"];
  var TEXT = "#637b91";
  var LINE = "#e7edf2";

  var stats = null;
  var charts = [];
  var chartsDone = false;
  var pendingScript = null;

  // ------------------------------------------------------------ ECharts 加载 //
  function loadECharts() {
    return new Promise(function (resolve, reject) {
      if (window.echarts) { resolve(window.echarts); return; }
      var timeout = window.setTimeout(function () {
        reject(new Error("ECharts 加载超时"));
      }, CDN_TIMEOUT);
      var script = document.createElement("script");
      pendingScript = script;
      script.src = ECHARTS_CDN;
      script.async = true;
      script.onload = function () {
        window.clearTimeout(timeout);
        if (window.echarts) resolve(window.echarts);
        else reject(new Error("ECharts 未就绪"));
      };
      script.onerror = function () {
        window.clearTimeout(timeout);
        reject(new Error("ECharts 加载失败"));
      };
      document.head.appendChild(script);
    });
  }

  function baseOption() {
    return {
      color: BLUES,
      textStyle: { fontFamily: "DM Sans, PingFang SC, Microsoft YaHei, sans-serif", color: TEXT },
      tooltip: { trigger: "item", confine: true, borderColor: LINE },
      grid: { left: 8, right: 18, top: 20, bottom: 6, containLabel: true },
      animationDuration: 650,
      animationDurationUpdate: 420,
      animationEasing: "cubicOut",
      animationEasingUpdate: "cubicOut",
      stateAnimation: { duration: 220, easing: "cubicOut" }
    };
  }

  function axisLabel() {
    return { color: TEXT, fontSize: 11 };
  }

  function splitLine() {
    return { lineStyle: { color: LINE, type: "dashed" } };
  }

  function initChart(id, option) {
    var el = document.getElementById(id);
    if (!el || !window.echarts) return null;
    el.hidden = false;                 // 降级时被隐藏过，初始化前必须还原，否则尺寸为 0
    if (el.clientHeight === 0) return null;
    var chart = window.echarts.init(el, null, { renderer: "canvas" });
    chart.setOption(option);
    charts.push(chart);
    return chart;
  }

  // -------------------------------------------------------------- 图表配置 //
  function cohortOption(rows) {
    var option = baseOption();
    option.grid = { left: 8, right: 24, top: 24, bottom: 6, containLabel: true };
    option.tooltip = { trigger: "axis", confine: true, borderColor: LINE };
    option.xAxis = {
      type: "category",
      data: rows.map(function (r) { return r.value + "届"; }),
      axisLabel: axisLabel(),
      axisLine: { lineStyle: { color: LINE } }
    };
    option.yAxis = { type: "value", axisLabel: axisLabel(), splitLine: splitLine() };
    option.series = [{
      type: "line",
      smooth: true,
      symbolSize: 7,
      showSymbol: true,
      data: rows.map(function (r) { return r.count; }),
      lineStyle: { width: 2.4, color: "#2866a5" },
      itemStyle: { color: "#2866a5" },
      emphasis: {
        focus: "series",
        scale: 1.8,
        itemStyle: { color: "#dfaa61", borderColor: "#fff", borderWidth: 2 }
      },
      areaStyle: {
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: "rgba(40,102,165,.24)" },
            { offset: 1, color: "rgba(40,102,165,.02)" }
          ]
        }
      }
    }];
    return option;
  }

  function barOption(rows, horizontal) {
    var option = baseOption();
    option.grid = { left: 8, right: 30, top: 16, bottom: 6, containLabel: true };
    option.tooltip = { trigger: "axis", confine: true, borderColor: LINE };
    var names = rows.map(function (r) { return r.value; });
    var counts = rows.map(function (r) { return r.count; });
    var category = {
      type: "category",
      data: names,
      axisLabel: axisLabel(),
      axisLine: { lineStyle: { color: LINE } }
    };
    var value = { type: "value", axisLabel: axisLabel(), splitLine: splitLine() };
    option.xAxis = horizontal ? value : category;
    option.yAxis = horizontal
      ? Object.assign({}, category, { data: names.slice().reverse(), inverse: false })
      : value;
    option.series = [{
      type: "bar",
      barMaxWidth: 16,
      data: horizontal ? counts.slice().reverse() : counts,
      itemStyle: { borderRadius: horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0], color: "#4c8fca" },
      emphasis: {
        focus: "self",
        itemStyle: {
          color: "#2866a5",
          shadowBlur: 12,
          shadowColor: "rgba(40,102,165,.28)"
        }
      }
    }];
    return option;
  }

  function pieOption(rows) {
    var option = baseOption();
    option.tooltip = { trigger: "item", confine: true, borderColor: LINE, formatter: "{b}：{c} 条（{d}%）" };
    option.legend = {
      bottom: 0,
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: TEXT, fontSize: 11 }
    };
    option.series = [{
      type: "pie",
      radius: ["46%", "72%"],
      center: ["50%", "44%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#fff", borderWidth: 2 },
      label: { color: TEXT, fontSize: 11, formatter: "{b}\n{d}%" },
      labelLine: { lineStyle: { color: LINE } },
      emphasis: {
        focus: "self",
        scale: true,
        scaleSize: 8,
        itemStyle: { shadowBlur: 14, shadowColor: "rgba(29,79,120,.24)" }
      },
      data: rows.map(function (r) { return { name: r.value, value: r.count }; })
    }];
    return option;
  }

  // -------------------------------------------------------- CSS 降级渲染 //
  function fallbackHTML(rows) {
    var max = rows.reduce(function (acc, r) { return Math.max(acc, r.count); }, 1);
    return '<div class="rank">' + rows.map(function (row) {
      var width = Math.max(6, Math.round((row.count / max) * 100));
      return '<div class="rank-row">' +
        '<span class="nm" title="' + IRS.esc(row.value) + '">' + IRS.esc(row.value) + "</span>" +
        '<span class="track"><span class="fill" style="width:' + width + '%"></span></span>' +
        '<span class="cnt">' + IRS.num(row.count) + "</span>" +
        "</div>";
    }).join("") + "</div>";
  }

  function hideFallback() {
    ["cohort", "province", "position", "degree", "city", "college"].forEach(function (key) {
      var box = document.getElementById("fallback-" + key);
      if (box) box.hidden = true;
    });
  }

  function showFallback(rows) {
    var pairs = [
      ["cohort", rows.cohort, "届别"],
      ["province", rows.province, "省份"],
      ["position", rows.position_category, "岗位类别"],
      ["degree", rows.degree_level, "学历层次"],
      ["city", rows.city, "城市"],
      ["college", rows.college, "学院"]
    ];
    pairs.forEach(function (pair) {
      var key = pair[0];
      var canvas = document.getElementById("chart-" + key);
      var box = document.getElementById("fallback-" + key);
      if (canvas) canvas.hidden = true;
      if (!box) return;
      var list = (pair[1] || []).map(function (item) {
        return {
          value: key === "cohort" ? item.value + " 届" : item.value,
          count: item.count
        };
      });
      if (!list.length) {
        box.hidden = false;
        box.innerHTML = '<p class="muted">暂无数据</p>';
        return;
      }
      box.hidden = false;
      box.innerHTML = fallbackHTML(list);
    });
    IRS.toast("图表库未加载，已切换为简易条形图", 3000);
  }

  // ---------------------------------------------------------------- 渲染 //
  function renderKpis(data) {
    var box = $("#kpis");
    if (!box) return;
    var s = data.summary || {};
    var cards = [
      { k: "收录记录", v: s.records, unit: "条", note: "来自公开通知公告的画像记录" },
      { k: "通知原图", v: s.images, unit: "张", note: "OCR 原始图片数" },
      { k: "去向省份", v: s.provinces, unit: "个", note: "识别到省份的记录覆盖" },
      { k: "覆盖学院", v: s.colleges, unit: "个", note: "出现过的学院数" },
      { k: "去向城市", v: s.cities, unit: "个", note: "出现过的城市数" },
      { k: "覆盖届别", v: s.cohorts, unit: "届", note: "数据覆盖的年份跨度" }
    ];
    box.innerHTML = cards.map(function (card) {
      return '<div class="kpi">' +
        '<span class="k">' + IRS.esc(card.k) + "</span>" +
        '<div class="v">' + IRS.num(card.v || 0) + '<span class="unit">' + card.unit + "</span></div>" +
        '<div class="note">' + IRS.esc(card.note) + "</div>" +
        "</div>";
    }).join("");
  }

  var FILL_LABELS = {
    name: "姓名", cohort: "届别", college: "学院", major: "专业",
    destination_org: "录用单位", city: "城市", position: "岗位方向", province: "省份",
    degree: "学历（原文）", position_category: "岗位类别", degree_level: "学历层次"
  };

  function renderFillRate(fill) {
    var box = $("#fill-bars");
    if (!box) return;
    var entries = Object.keys(fill || {}).map(function (key) {
      return { key: key, label: FILL_LABELS[key] || key, rate: Number(fill[key]) || 0 };
    }).sort(function (a, b) { return b.rate - a.rate; });
    if (!entries.length) {
      box.innerHTML = '<p class="muted">暂无数据</p>';
      return;
    }
    box.innerHTML = entries.map(function (entry) {
      var width = Math.max(2, Math.round(entry.rate * 100));
      return '<div class="bar-row">' +
        '<span class="label">' + IRS.esc(entry.label) + "</span>" +
        '<span class="track"><span class="fill" style="width:' + width + '%"></span></span>' +
        '<span class="val">' + Math.round(entry.rate * 100) + "%</span>" +
        "</div>";
    }).join("");
  }

  function renderCharts(data) {
    if (chartsDone || !window.echarts) return;
    chartsDone = true;
    hideFallback();
    initChart("chart-cohort", cohortOption(data.cohort || []));
    initChart("chart-province", barOption(data.province || [], true));
    initChart("chart-position", pieOption(data.position_category || []));
    initChart("chart-degree", pieOption(data.degree_level || []));
    initChart("chart-city", barOption(data.city || [], true));
    initChart("chart-college", barOption(data.college || [], true));
    window.addEventListener("resize", IRS.debounce(function () {
      charts.forEach(function (chart) { chart.resize(); });
    }, 180));
  }

  // ---------------------------------------------------------------- 启动 //
  IRS.ready(function () {
    IRS.getJSON(IRS.API + "/stats", { top_n: 15 })
      .then(function (data) {
        stats = data;
        renderKpis(data);
        renderFillRate(data.fill_rate);
        return loadECharts()
          .then(function () { renderCharts(data); })
          .catch(function () {
            // CDN 慢到超时不代表一定失败：真的加载成功就用图表，否则降级。
            if (window.echarts) { renderCharts(data); return; }
            showFallback(data);
            if (pendingScript) {
              pendingScript.addEventListener("load", function () {
                if (window.echarts) { renderCharts(data); IRS.toast("图表库已就绪，已切换为图表", 2000); }
              });
            }
          });
      })
      .catch(function (err) {
        var box = $("#kpis");
        if (box) {
          box.innerHTML = '<div class="kpi"><span class="k">加载失败</span>' +
            '<div class="v">—</div><div class="note">' + IRS.esc(err.message) + "</div></div>";
        }
        IRS.toast("统计数据加载失败：" + err.message, 3200);
      });
  });
})();
