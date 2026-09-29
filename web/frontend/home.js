/* ==========================================================================
   首页（preview.svg 版式）
   - 上方主视觉 + 快速检索入口
   - 中部「数据概览 / 去向流向」两张面板，数据来自 /api/stats
   - 下部「最近收录」记录列表，数据来自 /api/search（按届别倒序）
   本页只读不写，所有筛选动作都跳到 /search.html 交给 app.js 处理。
   ========================================================================== */
(function () {
  "use strict";

  var IRS = window.IRS;
  var $ = function (sel) { return document.querySelector(sel); };
  var RECENT_LIMIT = 6;
  var PROVINCE_CENTERS = {
    "新疆": [87.62, 43.82], "西藏": [91.13, 29.65], "青海": [101.78, 36.62], "甘肃": [103.83, 36.06],
    "宁夏": [106.23, 38.48], "内蒙古": [111.75, 40.84], "黑龙江": [126.64, 45.75], "吉林": [125.32, 43.90],
    "辽宁": [123.43, 41.80], "北京": [116.40, 39.90], "天津": [117.20, 39.12], "河北": [114.50, 38.04],
    "山西": [112.55, 37.87], "陕西": [108.94, 34.34], "四川": [104.07, 30.67], "重庆": [106.55, 29.56],
    "云南": [102.71, 25.04], "贵州": [106.71, 26.57], "广西": [108.32, 22.82], "湖南": [112.94, 28.23],
    "湖北": [114.30, 30.59], "河南": [113.62, 34.75], "山东": [117.00, 36.65], "江苏": [118.78, 32.04],
    "安徽": [117.23, 31.82], "上海": [121.47, 31.23], "浙江": [120.15, 30.27], "福建": [119.30, 26.08],
    "江西": [115.89, 28.68], "广东": [113.26, 23.13], "海南": [110.32, 20.04], "台湾": [121.50, 25.03],
    "香港": [114.17, 22.30], "澳门": [113.54, 22.20]
  };

  // ------------------------------------------------------------ 快捷筛选条 //
  function renderChips(stats, side) {
    var box = $("#home-chips");
    if (!box) return;
    var chips = ['<a class="chip on" href="/search.html">全部记录</a>'];
    var cohorts = (stats.cohort || []).slice();
    cohorts
      .sort(function (a, b) { return Number(b.value) - Number(a.value); })
      .slice(0, 2)
      .forEach(function (row) {
        chips.push(
          '<a class="chip" href="/search.html?cohort_year=' + encodeURIComponent(row.value) + '">' +
          IRS.esc(row.value) + "届 <span class=\"n\">" + IRS.num(row.count) + "</span></a>"
        );
      });
    (stats.position_category || []).slice(0, 2).forEach(function (row) {
      chips.push(
        '<a class="chip" href="/search.html?position_category=' + encodeURIComponent(row.value) + '">' +
        IRS.esc(row.value) + " <span class=\"n\">" + IRS.num(row.count) + "</span></a>"
      );
    });
    if (side) chips.push('<a class="chip" href="/search.html">多维筛选 ↗</a>');
    box.innerHTML = chips.join("");
  }

  // ---------------------------------------------------------- 主视觉搜索区 //
  function bindHeroSuggestions(stats) {
    var hot = $("#hero-hot");
    if (!hot) return;
    var picks = [];
    var topProvince = (stats.province || [])[0];
    var topCollege = (stats.college || [])[0];
    var latest = (stats.cohort || []).slice().sort(function (a, b) {
      return Number(b.value) - Number(a.value);
    })[0];
    if (topProvince) picks.push(topProvince.value);
    if (topCollege) picks.push(topCollege.value);
    if (latest) picks.push(latest.value + "届");
    if (!picks.length) picks = ["计算机学院", "硕士", "江苏"];

    picks.slice(0, 4).forEach(function (word) {
      var a = document.createElement("a");
      a.href = "/search.html?q=" + encodeURIComponent(word);
      a.textContent = word;
      hot.appendChild(a);
    });
  }

  function bindHeroSearch() {
    var form = $("#hero-search");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var q = $("#hero-q").value.trim();
      IRS.goSearch({ filters: {} }, q ? { q: q } : null);
    });
  }

  // -------------------------------------------------------------- 数据概览 //
  function renderMetrics(stats) {
    var box = $("#home-metrics");
    if (!box) return;
    var s = stats.summary || {};
    var years = (stats.cohort || []).map(function (r) { return String(r.value); }).sort();
    var span = years.length ? (years[0] + " — " + years[years.length - 1]) : "—";
    box.innerHTML = [
      '<div class="metric"><div class="k">收录记录</div>' +
      '<div class="v">' + IRS.num(s.records) + '<span class="unit">条</span></div>' +
      '<div class="note">来自 ' + IRS.num(s.images) + ' 张公告原图</div></div>',
      '<div class="metric"><div class="k">覆盖届别</div>' +
      '<div class="v">' + IRS.num(s.cohorts) + '<span class="unit">届</span></div>' +
      '<div class="note">' + IRS.esc(span) + "</div></div>",
      '<div class="metric"><div class="k">去向地区</div>' +
      '<div class="v">' + IRS.num(s.provinces) + '<span class="unit">省</span></div>' +
      '<div class="note">' + IRS.num(s.cities) + ' 个城市 · ' + IRS.num(s.colleges) + " 个学院</div></div>"
    ].join("");
  }

  function renderTrend(stats) {
    var box = $("#home-bars");
    if (!box) return;
    var rows = (stats.cohort || []).slice();
    if (!rows.length) {
      box.innerHTML = '<p class="meta-note">暂无届别数据。</p>';
      return;
    }
    rows.sort(function (a, b) { return Number(a.value) - Number(b.value); });
    var recent = rows.slice(-7);
    var max = recent.reduce(function (acc, r) { return Math.max(acc, Number(r.count) || 0); }, 0) || 1;
    box.innerHTML = recent.map(function (r) {
      var width = Math.max(4, Math.round(((Number(r.count) || 0) / max) * 100));
      return (
        '<div class="bar-row"><span class="label">' + IRS.esc(r.value) + '届</span>' +
        '<span class="track"><i class="fill" style="width:' + width + '%"></i></span>' +
        '<span class="val">' + IRS.num(r.count) + "</span></div>"
      );
    }).join("");
    var foot = $("#home-bars-foot");
    if (foot) {
      foot.textContent = "毕业届别收录趋势 · 最近 " + recent.length + " 届，柱长按最大届别记录数等比缩放。";
    }
  }

  function renderProvinceMap(stats) {
    var pins = $("#province-map-pins");
    if (!pins) return;
    function provinceKey(value) {
      return String(value || "")
        .replace(/壮族自治区|维吾尔自治区|回族自治区|特别行政区/g, "")
        .replace(/省|市|自治区/g, "");
    }
    var rows = (stats.province || []).filter(function (row) {
      return PROVINCE_CENTERS.hasOwnProperty(provinceKey(row.value));
    });
    var max = rows.reduce(function (n, row) { return Math.max(n, Number(row.count) || 0); }, 0) || 1;
    pins.innerHTML = rows.map(function (row) {
      var name = provinceKey(row.value);
      var point = PROVINCE_CENTERS[name];
      var x = 32 + ((point[0] - 73) / 62) * 455;
      var y = 15 + ((54 - point[1]) / 36) * 300;
      var count = Number(row.count) || 0;
      var radius = 3.5 + Math.sqrt(count / max) * 5;
      var label = name + "：" + IRS.num(count) + " 条记录";
      return '<a class="map-pin" href="/search.html?province=' + encodeURIComponent(row.value) + '"' +
        ' aria-label="' + IRS.esc(label) + '"><title>' + IRS.esc(label) + "</title>" +
        '<circle class="map-halo" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + (radius + 5).toFixed(1) + '"/>' +
        '<circle class="map-bubble" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + radius.toFixed(1) + '"/>' +
        "</a>";
    }).join("");
  }

  function rankHTML(rows, total, linkable) {
    var max = rows.reduce(function (acc, r) { return Math.max(acc, Number(r.count) || 0); }, 0) || 1;
    return rows.map(function (r, i) {
      var width = Math.max(4, Math.round(((Number(r.count) || 0) / max) * 100));
      var name = IRS.esc(r.value);
      var label = linkable
        ? '<a class="nm" href="/search.html?province=' + encodeURIComponent(r.value) + '">' + name + "</a>"
        : '<span class="nm">' + name + "</span>";
      return (
        '<div class="rank-row" title="' + name + '：' + IRS.num(r.count) + ' 条（占全部记录 ' + IRS.pct(r.count, total) + '%）"><span class="no">' + (i + 1) + "</span>" + label +
        '<span class="track"><i class="fill" style="width:' + width + '%"></i></span>' +
        '<span class="cnt">' + IRS.pct(r.count, total) + "%</span></div>"
      );
    }).join("");
  }

  function renderRank(stats) {
    var total = (stats.summary || {}).records || 0;
    var top = (stats.province || []).slice(0, 5);
    var flow = $("#home-rank");
    if (flow) {
      flow.innerHTML = top.length
        ? rankHTML(top, total, false)
        : '<p class="meta-note">暂无省份分布数据。</p>';
    }
    var side = $("#home-side-rank");
    if (side) {
      side.innerHTML = top.length
        ? rankHTML(top.slice(0, 4), total, true)
        : '<p class="meta-note">暂无数据。</p>';
    }
  }

  // -------------------------------------------------------------- 最近收录 //
  function renderRecent() {
    var box = $("#home-records");
    var empty = $("#home-empty");
    if (!box) return;
    IRS.getJSON(IRS.API + "/search", { sort: "cohort_desc", page: 1, page_size: RECENT_LIMIT })
      .then(function (data) {
        var items = data.items || [];
        var count = $("#home-count");
        if (count) {
          count.textContent = "共 " + IRS.num(data.total) + " 条可检索档案";
        }
        if (!items.length) {
          if (empty) empty.hidden = false;
          return;
        }
        box.innerHTML = items.map(IRS.cardHTML).join("");
      })
      .catch(function (err) {
        if (empty) {
          empty.hidden = false;
          empty.querySelector(".hint").textContent = err.message;
        }
      });
  }

  // ------------------------------------------------------------ 失败可见 //
  function escHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  // 统计拿不到数据时也要看得见原因，不能整块留白
  function showStatsFailure(message) {
    var note = '<p class="meta-note">统计数据不可用：' + escHtml(message) +
      '　<a href="/">重新加载</a></p>';
    var boxes = ["#home-metrics", "#home-bars", "#home-rank", "#home-side-rank",
      "#home-chips", "#home-records"];
    boxes.forEach(function (sel) {
      var box = $(sel);
      if (box && !box.children.length) box.innerHTML = note;
    });
    var foot = $("#home-bars-foot");
    if (foot) foot.textContent = "请确认后台服务正在运行（python -m web.backend.main）。";
  }

  // ---------------------------------------------------------------- 启动 //
  function boot() {
    bindHeroSearch();
    IRS.getJSON(IRS.API + "/stats", { top_n: 50 })
      .then(function (stats) {
        // 每块统计各自兜底：一处渲染失败不该把整块面板拖成空白
        [
          ["数据概览", function () { renderMetrics(stats); }],
          ["届别趋势", function () { renderTrend(stats); }],
          ["省份排行", function () { renderRank(stats); }],
          ["去向地图", function () { renderProvinceMap(stats); }],
          ["快捷筛选", function () { renderChips(stats, true); }],
          ["搜索建议", function () { bindHeroSuggestions(stats); }]
        ].forEach(function (job) {
          try {
            job[1]();
          } catch (err) {
            if (window.console && console.warn) console.warn("[home] " + job[0] + " 渲染失败", err);
            showStatsFailure(job[0] + "渲染失败：" + ((err && err.message) || err));
          }
        });
      })
      .catch(function (err) {
        IRS.toast("统计数据加载失败：" + err.message, 4000);
        showStatsFailure((err && err.message) || "接口无响应");
      });
    renderRecent();
  }

  if (!IRS || typeof IRS.ready !== "function") {
    // common.js 没加载成功，或浏览器还在用旧版缓存（旧版没有 IRS 命名空间）：
    // 以前这种情况表现为「统计面板整块空白」，现在直接把原因写在页面上
    onReady(function () {
      showStatsFailure("前端脚本 common.js 版本过旧，请按 Ctrl+F5 强制刷新");
    });
    return;
  }

  IRS.ready(boot);
})();
