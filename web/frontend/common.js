/* ==========================================================================
   公共工具：所有页面共用的 window.IRS 命名空间
   --------------------------------------------------------------------------
   约定
   - 只暴露一个小工具集，不引入框架；页面脚本各自 IIFE，互不干扰。
   - 列表项里的高亮 HTML 由后端 ``src/search/highlight.py`` 生成并已转义，
     前端只负责插入，**不要**再 escape 一遍（否则 ``<mark>`` 会显示成文本）。
   ========================================================================== */
(function (global) {
  "use strict";

  var API = "/api";

  /** 与后端 FACET_DIMENSIONS 保持同序，作为缺省维度表。 */
  var DIMENSIONS = [
    { key: "province", label: "省份" },
    { key: "position_category", label: "岗位类别" },
    { key: "degree_level", label: "学历" },
    { key: "cohort_year", label: "届别" },
    { key: "college", label: "学院" },
    { key: "major", label: "专业" },
    { key: "city", label: "城市" }
  ];

  var DIMENSION_KEYS = DIMENSIONS.map(function (d) { return d.key; });

  // ------------------------------------------------------------------ 基础 //
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** 空值统一显示成破折号，避免表格里出现空洞。 */
  function dash(value) {
    var text = value === null || value === undefined ? "" : String(value).trim();
    return text === "" ? "—" : text;
  }

  function num(value) {
    var n = Number(value);
    return isFinite(n) ? n.toLocaleString("zh-CN") : "0";
  }

  function pct(part, whole) {
    var a = Number(part) || 0;
    var b = Number(whole) || 0;
    return b > 0 ? Math.round((a / b) * 100) : 0;
  }

  /** 取姓名首字做头像，兼容 "李 明" 这类带空格的写法。 */
  function initial(name) {
    var text = String(name || "").replace(/\s+/g, "");
    return text ? text.slice(0, 1) : "·";
  }

  function regionShort(value) {
    var text = String(value || "").trim();
    return text.replace(/(省|市|自治区|特别行政区)$/, "").replace(/^内蒙古自治区$/, "内蒙古");
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      var self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait || 200);
    };
  }

  // -------------------------------------------------------------- 网络请求 //
  function buildQuery(params) {
    if (!params) return "";
    if (params instanceof URLSearchParams) return params.toString();
    var usp = new URLSearchParams();
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value === null || value === undefined || value === "") return;
      if (Array.isArray(value)) {
        value.forEach(function (v) { if (v !== "" && v !== null) usp.append(key, v); });
      } else {
        usp.set(key, value);
      }
    });
    return usp.toString();
  }

  function getJSON(path, params) {
    var query = buildQuery(params);
    var url = path + (query ? "?" + query : "");
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (resp) {
      if (!resp.ok) {
        return resp.text().then(function (text) {
          var message = "请求失败：" + resp.status;
          try {
            var data = JSON.parse(text);
            if (data && data.detail) message = String(data.detail);
          } catch (err) { /* 非 JSON 错误体，保持默认文案 */ }
          throw new Error(message);
        });
      }
      return resp.json();
    });
  }

  // ------------------------------------------------------------------ 展示 //
  /** 列表项在某个字段上的高亮 HTML（后端已转义，直接插入）。 */
  function hi(item, field) {
    var hl = item && item.highlight;
    if (hl && hl.fields && typeof hl.fields[field] === "string") return hl.fields[field];
    return esc(item ? item[field] : "");
  }

  /** 正文预览（后端只给列表项带 snippet，详情页走 evidence）。 */
  function snippet(item) {
    var hl = item && item.highlight;
    return (hl && hl.snippet) || "";
  }

  function cohort(item) {
    if (!item) return "";
    return item.cohort || (item.cohort_year ? item.cohort_year + "届" : "");
  }

  function cohortYear(item) {
    if (!item) return "";
    if (item.cohort_year) return String(item.cohort_year);
    var m = String(item.cohort || "").match(/\d{4}/);
    return m ? m[0] : "";
  }

  /** 去向地区：优先「省 · 市」，缺一就退化成能拿到的那个。 */
  function region(item) {
    if (!item) return "";
    var parts = [];
    if (item.province) parts.push(regionShort(item.province));
    if (item.city && regionShort(item.city) !== regionShort(item.province)) parts.push(item.city);
    return parts.join(" · ");
  }

  function modeLabel(mode) {
    return { and: "全部关键词命中", or: "任一关键词命中", like: "模糊匹配", none: "全部记录" }[mode] || "";
  }

  /** 排序选项：选中项置顶，其余按记录数多的在前。 */
  function sortOptions(options) {
    if (!Array.isArray(options)) return [];
    return options.slice().sort(function (a, b) {
      if (!!b.selected !== !!a.selected) return b.selected ? 1 : -1;
      return (b.count || 0) - (a.count || 0) || String(a.label).localeCompare(String(b.label), "zh-Hans-CN");
    });
  }

  function toast(message, ms) {
    var el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.classList.remove("show"); }, ms || 2200);
  }

  // ------------------------------------------------------------ 记录卡片 //
  /**
   * 「小标签在上、加粗值在下」的数据格子。
   * 注意：传入的 ``pair[1]`` **必须**是已经转义好的 HTML（来自 ``hi()``），
   * 这里再转义一遍会把 ``<mark>`` 高亮显示成文本。
   */
  function dataGrid(pairs) {
    return pairs
      .filter(function (pair) { return pair && pair[1] !== null && pair[1] !== undefined; })
      .map(function (pair) {
        return (
          '<div><span class="k">' + esc(pair[0]) + '</span>' +
          '<span class="v' + (pair[2] ? " wrap2" : "") + '">' + dash(pair[1]) + '</span></div>'
        );
      })
      .join("");
  }

  /** 列表项 → 记录卡片 HTML（首页与检索结果共用同一套版式）。 */
  function cardHTML(item) {
    var nameHtml = hi(item, "name") || "—";
    var cohortText = cohort(item);
    var degree = item.degree_level || item.degree || "";
    var meta = [item.college, item.major, degree]
      .filter(function (v) { return v; })
      .map(esc)
      .join(" · ");
    var place = hi(item, "province") || esc(region(item));
    var org = hi(item, "destination_org");
    var position = hi(item, "position");
    var snippetHtml = snippet(item);
    var source = item.notice_title || item.organization || item.notice_type || "";
    var time = item.release_time ? String(item.release_time).slice(0, 10) : "";
    var imageBtn = item.image_local_url
      ? '<a class="mini" href="' + esc(item.image_local_url) + '" target="_blank" rel="noopener">原始图片</a>'
      : '<span class="mini" aria-disabled="true">无原图</span>';

    return (
      '<article class="card" data-id="' + esc(item.id) + '">' +
      '<div class="avatar">' + esc(initial(item.name)) + "</div>" +
      '<div class="body">' +
      '<div class="head">' +
      '<span class="name">' + nameHtml + "</span>" +
      (cohortText ? '<span class="badge-cohort">' + esc(cohortText) + "</span>" : "") +
      (item.position_category ? '<span class="badge-cat">' + esc(item.position_category) + "</span>" : "") +
      (degree ? '<span class="more">' + esc(degree) + "</span>" : "") +
      "</div>" +
      (meta ? '<div class="meta">' + meta + "</div>" : "") +
      '<div class="rule"></div>' +
      '<div class="grid">' +
      dataGrid([
        ["选调省份", place],
        ["录用单位", org, true],
        ["岗位方向", position, true]
      ]) +
      "</div>" +
      (snippetHtml ? '<div class="snippet">' + snippetHtml + "</div>" : "") +
      '<div class="foot">' +
      '<span class="src">' + (source ? "来源：" + esc(source) : "") + (time ? " · " + esc(time) : "") + "</span>" +
      '<span class="acts">' + imageBtn +
      '<a class="mini primary" href="/detail.html?id=' + esc(item.id) + '">查看档案 ↗</a>' +
      "</span></div>" +
      "</div></article>"
    );
  }

  /** 表格视图的一行。 */
  function tableRowHTML(item) {
    return (
      "<tr>" +
      '<td class="name">' + (hi(item, "name") || "—") + "</td>" +
      // 届别「2024届」与学历「本科」这类短标签必须 nowrap：
      // 一旦列被挤窄，浏览器会拆成「2024 / 届」「本 / 科」两行竖排，非常难读
      '<td class="nowrap">' + esc(cohort(item) || "—") + "</td>" +
      "<td>" + (hi(item, "college") || "—") + "</td>" +
      "<td>" + (hi(item, "major") || "—") + "</td>" +
      '<td class="nowrap">' + esc(region(item) || "—") + "</td>" +
      '<td><div class="ellip">' + (hi(item, "destination_org") || "—") + "</div></td>" +
      "<td>" + (hi(item, "position") || "—") + "</td>" +
      "<td>" + esc(item.position_category || "—") + "</td>" +
      '<td class="nowrap">' + esc(item.degree_level || item.degree || "—") + "</td>" +
      '<td class="nowrap"><a href="/detail.html?id=' + esc(item.id) + '">档案 ↗</a></td>' +
      "</tr>"
    );
  }

  // ------------------------------------------------------- URL 状态（检索页）//
  function readState(dimensions) {
    var keys = dimensions || DIMENSION_KEYS;
    var p = new URLSearchParams(location.search);
    var filters = {};
    keys.forEach(function (key) {
      var values = p.getAll(key).filter(function (v) { return v !== ""; });
      if (values.length) filters[key] = values;
    });
    var size = parseInt(p.get("page_size") || "0", 10);
    return {
      q: p.get("q") || "",
      sort: p.get("sort") || "",
      page: Math.max(1, parseInt(p.get("page") || "1", 10) || 1),
      page_size: isFinite(size) && size > 0 ? size : 0,
      view: p.get("view") === "table" ? "table" : "card",
      filters: filters
    };
  }

  function searchParams(state, dimensions) {
    var keys = dimensions || DIMENSION_KEYS;
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.sort) p.set("sort", state.sort);
    if (state.page_size) p.set("page_size", String(state.page_size));
    keys.forEach(function (key) {
      (state.filters && state.filters[key] ? state.filters[key] : []).forEach(function (value) {
        p.append(key, value);
      });
    });
    return p;
  }

  function writeState(state, dimensions) {
    var p = searchParams(state, dimensions);
    if (state.view === "table") p.set("view", "table");
    p.set("page", String(state.page || 1));
    var query = p.toString();
    var url = location.pathname + (query ? "?" + query : "");
    history.replaceState(null, "", url);
  }

  /** 从当前 URL 跳转到检索页（首页 / 统计页的检索入口都走这里）。 */
  function goSearch(state, extra) {
    var p = searchParams(state || { filters: {} }, DIMENSION_KEYS);
    Object.keys(extra || {}).forEach(function (key) {
      if (extra[key] !== "" && extra[key] !== null && extra[key] !== undefined) {
        p.set(key, extra[key]);
      }
    });
    if (state && state.page) p.set("page", String(state.page));
    location.href = "/search.html" + (p.toString() ? "?" + p.toString() : "");
  }

  /** 把筛选对象压成一个可读的短标签列表（首页快捷筛选条用）。 */
  function filterSummary(filters) {
    var out = [];
    (filters ? Object.keys(filters) : []).forEach(function (key) {
      (filters[key] || []).forEach(function (value) { out.push(value); });
    });
    return out;
  }

  // ------------------------------------------------------------ 顶栏数据戳 //
  function fillStamp() {
    var slots = document.querySelectorAll("[data-records]");
    if (!slots.length) return;
    getJSON(API + "/health").then(function (data) {
      var stats = (data && data.stats) || {};
      var raw = String(stats.latest_release_time || "").trim();
      var dateMatch = raw.match(/(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})/);
      // 解析不出具体日期时（旧版接口/字段缺失）就只报收录量，绝不显示“暂无日期”
      var latest = dateMatch
        ? dateMatch[1] + "-" + ("0" + dateMatch[2]).slice(-2) + "-" + ("0" + dateMatch[3]).slice(-2)
        : "";
      var head = latest ? '数据更新至 <b class="stamp-date">' + esc(latest) + "</b> · " : "";
      slots.forEach(function (el) {
        el.innerHTML = head + "收录 <b>" + num(stats.records) + "</b> 条记录";
      });
    }).catch(function () { /* 顶栏信息是锦上添花，失败就留空 */ });
  }

  /** 首页/统计页顶栏那个紧凑检索框：回车或点按钮都跳到检索页。 */
  function bindQuickSearch() {
    var form = document.querySelector(".top .quick");
    if (!form) return;
    var input = form.querySelector("input");
    var submit = function () {
      var q = input.value.trim();
      goSearch({ filters: {} }, q ? { q: q } : null);
    };
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); submit(); }
    });
    var btn = form.querySelector("button");
    if (btn) btn.addEventListener("click", submit);
  }

  function bindHelpModal() {
    var overlay = document.querySelector(".help-overlay");
    var triggers = document.querySelectorAll(".js-help-open");
    if (!overlay || !triggers.length) return;
    var dialog = overlay.querySelector(".help-dialog");
    var closeButton = overlay.querySelector(".help-close");
    var doneButton = overlay.querySelector(".help-done");
    var closeTimer = null;
    var activeTrigger = null;

    function closeHelp() {
      if (overlay.hidden || overlay.classList.contains("closing")) return;
      overlay.classList.remove("open");
      overlay.classList.add("closing");
      document.body.classList.remove("help-open");
      triggers.forEach(function (trigger) {
        trigger.classList.remove("active");
        trigger.setAttribute("aria-expanded", "false");
      });
      closeTimer = window.setTimeout(function () {
        overlay.hidden = true;
        overlay.classList.remove("closing");
        if (activeTrigger) activeTrigger.focus();
      }, 280);
    }

    function openHelp(ev) {
      window.clearTimeout(closeTimer);
      activeTrigger = ev.currentTarget;
      overlay.hidden = false;
      overlay.classList.remove("closing");
      document.body.classList.add("help-open");
      triggers.forEach(function (trigger) {
        var active = trigger === activeTrigger;
        trigger.classList.toggle("active", active);
        trigger.setAttribute("aria-expanded", String(active));
      });
      window.requestAnimationFrame(function () { overlay.classList.add("open"); });
      if (closeButton) closeButton.focus();
    }

    triggers.forEach(function (trigger) { trigger.addEventListener("click", openHelp); });
    if (closeButton) closeButton.addEventListener("click", closeHelp);
    if (doneButton) doneButton.addEventListener("click", closeHelp);
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay) closeHelp();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !overlay.hidden) closeHelp();
      if (ev.key === "Tab" && !overlay.hidden && dialog) {
        var focusable = dialog.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])");
        if (!focusable.length) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
        else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
      }
    });
  }

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    fillStamp();
    bindQuickSearch();
    bindHelpModal();
  });

  global.IRS = {
    API: API,
    DIMENSIONS: DIMENSIONS,
    DIMENSION_KEYS: DIMENSION_KEYS,
    esc: esc,
    dash: dash,
    num: num,
    pct: pct,
    initial: initial,
    regionShort: regionShort,
    debounce: debounce,
    getJSON: getJSON,
    hi: hi,
    snippet: snippet,
    cohort: cohort,
    cohortYear: cohortYear,
    region: region,
    modeLabel: modeLabel,
    sortOptions: sortOptions,
    dataGrid: dataGrid,
    cardHTML: cardHTML,
    tableRowHTML: tableRowHTML,
    toast: toast,
    readState: readState,
    writeState: writeState,
    searchParams: searchParams,
    goSearch: goSearch,
    filterSummary: filterSummary,
    ready: ready
  };
})(window);
