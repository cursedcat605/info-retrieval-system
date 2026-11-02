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

  var API = "api";

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
      '<a class="mini primary" href="detail.html?id=' + esc(item.id) + '">查看档案 ↗</a>' +
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
      '<td class="nowrap"><a href="detail.html?id=' + esc(item.id) + '">档案 ↗</a></td>' +
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
    location.href = "search.html" + (p.toString() ? "?" + p.toString() : "");
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
      // stats.data_updated 来自 config.yaml 的 web.data_updated（人工标注的数据截止日），
      // 没配才回退到库中最新通知的发布时间
      var raw = String(stats.data_updated || stats.latest_release_time || "").trim();
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

  // ------------------------------------------------------- 胶囊下拉选择器 //
  /* 原生 <select> 的展开面板由浏览器/系统绘制（灰底高亮、系统字体），配色与站内
     对不上。这里保留 <select> 作为取值与 change 事件的载体，另外渲染
     「胶囊按钮 + 浮层列表」；用户选中后回写 <select> 并派发 change，
     页面原有的 select.addEventListener("change", …) 无需改动。 */
  var selectSeq = 0;

  var CARET = '<svg class="pillsel-caret" viewBox="0 0 12 8" aria-hidden="true" focusable="false">' +
    '<path d="M1.2 1.6 6 6.4l4.8-4.8" fill="none" stroke="currentColor" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var CHECK = '<svg class="pillsel-check" viewBox="0 0 12 10" aria-hidden="true" focusable="false">' +
    '<path d="M1 5.2 4.3 8.4 11 1.6" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function pillSelect(select) {
    if (!select) return null;
    if (select._pillsel) return select._pillsel;

    var name = "pillsel" + (++selectSeq);
    var expanded = false;
    var activeIndex = -1;

    var wrap = document.createElement("div");
    wrap.className = "pillsel";

    var labelEl = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    var title = labelEl ? labelEl.textContent.trim() : "";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pillsel-btn";
    btn.setAttribute("role", "combobox");
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", name + "-menu");
    if (title) btn.setAttribute("aria-label", title);

    var label = document.createElement("span");
    label.className = "pillsel-label";
    btn.appendChild(label);
    btn.insertAdjacentHTML("beforeend", CARET);

    var menu = document.createElement("ul");
    menu.className = "pillsel-menu";
    menu.id = name + "-menu";
    menu.setAttribute("role", "listbox");
    if (title) menu.setAttribute("aria-label", title);

    select.classList.add("pillsel-native");
    select.setAttribute("tabindex", "-1");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    wrap.appendChild(select);

    function items() {
      return Array.prototype.slice.call(menu.children);
    }

    /** 把 <select> 当前值同步到按钮文案与列表的选中态。 */
    function sync() {
      var opt = select.options[select.selectedIndex];
      label.textContent = opt ? opt.textContent : "";
      items().forEach(function (li) {
        var on = li.getAttribute("data-value") === select.value;
        li.classList.toggle("is-selected", on);
        li.setAttribute("aria-selected", on ? "true" : "false");
        var mark = li.querySelector(".pillsel-check");
        if (on && !mark) li.insertAdjacentHTML("beforeend", CHECK);
        else if (!on && mark) mark.parentNode.removeChild(mark);
      });
    }

    function render() {
      menu.innerHTML = Array.prototype.map.call(select.options, function (opt, i) {
        return '<li class="pillsel-option" role="option" id="' + name + "-opt-" + i + '"' +
          ' data-value="' + esc(opt.value) + '" aria-selected="false">' + esc(opt.textContent) + "</li>";
      }).join("");
      activeIndex = -1;
      sync();
      // 选项还没拿到（例如排序项要等 /api/config）时先不显示空胶囊
      wrap.hidden = !select.options.length;
    }

    function markActive() {
      var list = items();
      list.forEach(function (li, i) { li.classList.toggle("is-active", i === activeIndex); });
      if (list[activeIndex]) btn.setAttribute("aria-activedescendant", list[activeIndex].id);
      else btn.removeAttribute("aria-activedescendant");
    }

    function moveTo(index) {
      var list = items();
      if (!list.length) return;
      activeIndex = Math.max(0, Math.min(list.length - 1, index));
      markActive();
      var node = list[activeIndex];
      if (node.offsetTop < menu.scrollTop) menu.scrollTop = node.offsetTop;
      else if (node.offsetTop + node.offsetHeight > menu.scrollTop + menu.clientHeight) {
        menu.scrollTop = node.offsetTop + node.offsetHeight - menu.clientHeight;
      }
    }

    function onOutside(ev) {
      if (!wrap.contains(ev.target)) close();
    }

    function open() {
      if (expanded || !select.options.length) return;
      expanded = true;
      wrap.classList.add("open");
      btn.setAttribute("aria-expanded", "true");
      // 贴近视口右缘时改为右对齐，避免浮层溢出被裁切
      wrap.classList.remove("align-end");
      if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
        wrap.classList.add("align-end");
        if (menu.getBoundingClientRect().left < 8) wrap.classList.remove("align-end");
      }
      document.addEventListener("pointerdown", onOutside, true);
      moveTo(Math.max(0, select.selectedIndex));
    }

    function close(focus) {
      if (!expanded) return;
      expanded = false;
      wrap.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
      btn.removeAttribute("aria-activedescendant");
      items().forEach(function (li) { li.classList.remove("is-active"); });
      activeIndex = -1;
      document.removeEventListener("pointerdown", onOutside, true);
      if (focus) btn.focus();
    }

    function choose(index) {
      var node = items()[index];
      if (!node) return;
      var value = node.getAttribute("data-value");
      close(true);
      if (select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sync();
    }

    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (expanded) close(true);
      else open();
    });

    btn.addEventListener("keydown", function (ev) {
      var key = ev.key;
      if (!expanded) {
        if (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" ||
            key === " " || key === "Spacebar") {
          ev.preventDefault();
          open();
        }
        return;
      }
      if (key === "ArrowDown") { ev.preventDefault(); moveTo(activeIndex + 1); }
      else if (key === "ArrowUp") { ev.preventDefault(); moveTo(activeIndex - 1); }
      else if (key === "Home") { ev.preventDefault(); moveTo(0); }
      else if (key === "End") { ev.preventDefault(); moveTo(items().length - 1); }
      else if (key === "Enter" || key === " " || key === "Spacebar") { ev.preventDefault(); choose(activeIndex); }
      else if (key === "Escape") { ev.preventDefault(); close(true); }
      else if (key === "Tab") { close(); }
    });

    menu.addEventListener("click", function (ev) {
      var node = ev.target.closest ? ev.target.closest(".pillsel-option") : null;
      if (node) choose(items().indexOf(node));
    });

    menu.addEventListener("mousemove", function (ev) {
      var node = ev.target.closest ? ev.target.closest(".pillsel-option") : null;
      if (!node) return;
      var index = items().indexOf(node);
      if (index !== activeIndex) { activeIndex = index; markActive(); }
    });

    var view = { element: wrap, refresh: render, sync: sync, open: open, close: close };
    select._pillsel = view;
    render();
    return view;
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
    pillSelect: pillSelect,
    ready: ready
  };
})(window);
