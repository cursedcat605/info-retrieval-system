/* ==========================================================================
   检索页（filter-preview.svg 版式）
   --------------------------------------------------------------------------
   几条必须守住的既有行为（都踩过坑）：
   1. ``run()`` 开头必须重新读 ``#q`` 的值。筛选、排序、翻页都可能触发 run，
      若不重读，用户刚敲的关键词会被上一次的 state.q 覆盖 → 永远返回全部记录。
   2. 筛选是「提交式」的：勾选框 / 移除标签只改本地 state.filters 并给出待提交
      提示，**不自动检索**；只有点 [data-search] 或按 Enter 才 run(1)。
   3. 分面列表每次重绘前要把各 ``<details>`` 的展开状态快照下来再还原，
      否则 innerHTML 重建会把用户展开的组全部折叠回去（「点了没反应」的元凶）。
   4. 勾选状态一律以本地 state.filters 为准，不看服务端返回的 option.selected，
      否则待提交的勾选会被下次响应覆盖掉。
   ========================================================================== */
(function () {
  "use strict";

  var IRS = window.IRS;
  var $ = function (sel) { return document.querySelector(sel); };
  var FACET_PREVIEW = 8;

  var state = {
    q: "",
    sort: "",
    page: 1,
    page_size: 0,
    view: "card",
    filters: {},
    lastTotal: null,         // 最近一次检索的命中数（用于侧栏按钮文案）
    dimensions: IRS.DIMENSIONS.slice(),
    defaultPageSize: 20,
    maxPageSize: 100
  };

  var keys = IRS.DIMENSION_KEYS.slice();
  var groupOpen = {};        // 分面组展开状态（跨重绘保留）
  var expandedGroups = {};   // 「展开更多」状态
  var appliedKey = "";       // 已提交的筛选指纹
  var lastData = null;
  var suggestItems = [];
  var suggestIndex = -1;

  // ------------------------------------------------------------------ 工具 //
  /** 与后端 _display_value 一致：省份去后缀、届别补「届」。 */
  function displayValue(dim, value) {
    if (dim === "cohort_year") return String(value) + "届";
    if (dim === "province") return IRS.regionShort(value);
    return String(value);
  }

  function dimLabel(dim) {
    var found = state.dimensions.filter(function (d) { return d.key === dim; })[0];
    return found ? found.label : dim;
  }

  function filtersKey(filters) {
    var parts = [];
    keys.forEach(function (dim) {
      var values = (filters && filters[dim] ? filters[dim] : []).slice().sort();
      if (values.length) parts.push(dim + "=" + values.join(","));
    });
    return parts.join("&");
  }

  function selectedCount(filters) {
    return keys.reduce(function (acc, dim) {
      return acc + ((filters && filters[dim]) ? filters[dim].length : 0);
    }, 0);
  }

  // ------------------------------------------------------------ 首屏骨架 //
  function renderFacetSkeleton() {
    var box = $("#facets");
    if (!box) return;
    box.innerHTML = state.dimensions.map(function (dim, index) {
      return (
        '<details class="facet-group" data-dim="' + dim.key + '"' + (index < 3 ? " open" : "") + ">" +
        '<summary><span class="chev" aria-hidden="true"></span>' +
        '<span class="fname">' + IRS.esc(dim.label) + "</span>" +
        '<span class="badge" hidden>0</span></summary>' +
        '<div class="facet-options"><p class="meta-note">加载中…</p></div>' +
        "</details>"
      );
    }).join("");
  }

  // -------------------------------------------------------------- 分面渲染 //
  function snapshotGroups() {
    var nodes = document.querySelectorAll("#facets details.facet-group");
    Array.prototype.forEach.call(nodes, function (node) {
      groupOpen[node.getAttribute("data-dim")] = node.open;
    });
  }

  function optionsHTML(dim, options) {
    var chosen = state.filters[dim] || [];
    var list = options || [];
    var shown = expandedGroups[dim] ? list : list.slice(0, FACET_PREVIEW);
    var html = shown.map(function (opt) {
      var value = String(opt.value);
      var checked = chosen.indexOf(value) >= 0;
      var zero = !checked && Number(opt.count) === 0;
      return (
        '<label class="facet-option' + (checked ? " selected" : "") + (zero ? " zero" : "") + '">' +
        '<input type="checkbox" value="' + IRS.esc(value) + '" data-dim="' + dim + '"' +
        (checked ? " checked" : "") + ">" +
        '<span class="name" title="' + IRS.esc(opt.label || value) + '">' +
        IRS.esc(opt.label || value) + "</span>" +
        '<span class="count">' + IRS.num(opt.count) + "</span>" +
        "</label>"
      );
    }).join("");

    if (!shown.length) html = '<p class="meta-note">暂无数据</p>';
    if (list.length > FACET_PREVIEW) {
      html +=
        '<button class="expand-btn" type="button" data-expand="' + dim + '">' +
        (expandedGroups[dim] ? "收起" : "展开更多（共 " + list.length + " 项）") +
        "</button>";
    }
    return html;
  }

  function renderFacets(facets, dimensions) {
    var box = $("#facets");
    if (!box) return;
    snapshotGroups();
    if (dimensions && dimensions.length) state.dimensions = dimensions;

    box.innerHTML = state.dimensions.map(function (dim, index) {
      var options = (facets && facets[dim.key]) || [];
      var chosen = (state.filters[dim.key] || []).length;
      var open = groupOpen.hasOwnProperty(dim.key)
        ? groupOpen[dim.key]
        : chosen > 0 || index < 3;
      return (
        '<details class="facet-group" data-dim="' + dim.key + '"' + (open ? " open" : "") + ">" +
        '<summary><span class="chev" aria-hidden="true"></span>' +
        '<span class="fname">' + IRS.esc(dim.label) + "</span>" +
        '<span class="badge"' + (chosen ? "" : " hidden") + ">" + chosen + "</span></summary>" +
        '<div class="facet-options">' + optionsHTML(dim.key, options) + "</div>" +
        "</details>"
      );
    }).join("");
  }

  /** 单个维度的选项与角标就地重绘（不动 <details>，保留展开状态）。 */
  function refreshFacetGroup(dim) {
    var node = document.querySelector('#facets details.facet-group[data-dim="' + dim + '"]');
    if (!node) return;
    var options = (lastData && lastData.facets && lastData.facets[dim]) || [];
    var holder = node.querySelector(".facet-options");
    if (holder) holder.innerHTML = optionsHTML(dim, options);
    var chosen = (state.filters[dim] || []).length;
    var badge = node.querySelector("summary .badge");
    if (badge) {
      badge.textContent = chosen;
      badge.hidden = chosen === 0;
    }
  }

  // -------------------------------------------------------------- 条件标签 //
  function renderTags(tags) {
    var box = $("#tagbar");
    if (!box) return;
    var data = tags;
    if (!data) {
      // 本地合成（尚未提交时也要让用户看到自己勾了什么）
      data = [];
      state.dimensions.forEach(function (dim) {
        (state.filters[dim.key] || []).forEach(function (value) {
          data.push({
            dimension: dim.key,
            value: value,
            label: dim.label,
            display: displayValue(dim.key, value)
          });
        });
      });
    }
    if (!data.length) {
      box.innerHTML = "";
      return;
    }
    var html = data.map(function (tag) {
      return (
        '<span class="tag"><span class="dim">' + IRS.esc(tag.label) + "</span>" +
        IRS.esc(tag.display) +
        '<button type="button" data-dim="' + IRS.esc(tag.dimension) + '"' +
        ' data-value="' + IRS.esc(tag.value) + '"' +
        ' aria-label="移除 ' + IRS.esc(tag.label + tag.display) + '">×</button></span>'
      );
    }).join("");
    if (data.length > 1) {
      html += '<button class="clear-all" type="button" id="clear-all">清空全部</button>';
    }
    box.innerHTML = html;
  }

  // ------------------------------------------------------- 待提交状态提示 //
  function updateFilterHint() {
    var hint = $("#filter-hint");
    var pending = filtersKey(state.filters);
    var dirty = hint ? pending !== appliedKey : false;
    if (hint) {
      if (dirty) {
        var n = selectedCount(state.filters);
        hint.hidden = false;
        hint.textContent = n
          ? "已选择 " + n + " 个筛选条件，点右侧「检索」或左侧底部按钮才会生效。"
          : "已清空筛选条件，点「检索」后生效。";
      } else {
        hint.hidden = true;
      }
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-search]"), function (btn) {
      btn.classList.toggle("pending", dirty);
    });
    // 条件改了但还没检索时，按钮改成「应用筛选」，避免显示上一次的旧条数
    var cta = document.querySelector(".sidebar-actions .btn");
    if (cta && dirty) cta.textContent = "应用筛选";
  }

  // ------------------------------------------------------------------ 标签 //

  // -------------------------------------------------------------- 结果渲染 //
  function emptyBlock(title, hint) {
    return (
      '<div class="empty"><span class="big" aria-hidden="true">◍</span>' +
      IRS.esc(title) +
      (hint ? '<p class="hint">' + hint + "</p>" : "") +
      "</div>"
    );
  }

  function renderResults(data) {
    var box = $("#results");
    if (!box) return;
    var items = data.items || [];
    if (!items.length) {
      box.innerHTML = emptyBlock(
        "没有找到匹配的档案",
        "试试减少关键词，或点左侧「清空全部」后重新检索。<br>" +
        '<button class="mini" type="button" data-reset>清空关键词与筛选</button>'
      );
      return;
    }
    if (state.view === "table") {
      box.innerHTML =
        '<div class="tablewrap"><table class="result"><thead><tr>' +
        ["姓名", "届别", "学院", "专业", "去向地区", "录用单位", "岗位方向", "岗位类别", "学历", ""]
          .map(function (label) { return "<th>" + label + "</th>"; }).join("") +
        "</tr></thead><tbody>" +
        items.map(IRS.tableRowHTML).join("") +
        "</tbody></table></div>";
      return;
    }
    box.innerHTML = '<div class="cards">' + items.map(IRS.cardHTML).join("") + "</div>";
  }

  function renderSummary(data) {
    var box = $("#summary");
    if (!box) return;
    var mode = IRS.modeLabel(data.mode);
    box.innerHTML =
      "共 <b>" + IRS.num(data.total) + "</b> 条" +
      (mode ? ' <span class="muted">· ' + IRS.esc(mode) + "</span>" : "") +
      (data.page && data.pages ? ' <span class="muted">· 第 ' + data.page + " / " + data.pages + " 页</span>" : "");
  }

  function renderFuzzyHint(data) {
    var box = $("#fuzzy-hint");
    if (!box) return;
    var groups = data.term_groups || [];
    if (!groups.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    var lines = groups.map(function (group) {
      var added = (group.added || []).slice(0, 4).map(function (v) { return "「" + IRS.esc(v) + "」"; });
      if (!added.length) return "";
      return "「" + IRS.esc(group.term) + "」已按词表扩展为 " + added.join("、") +
        ((group.added || []).length > 4 ? " 等" : "");
    }).filter(Boolean);
    if (!lines.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = lines.join("<br>") +
      '<br><span class="muted">高亮标出的是系统实际用来匹配的词 —— 因此可能与你输入的原词不同。</span>';
  }

  function renderPager(data) {
    var box = $("#pager");
    if (!box) return;
    // 样式全部挂在 .pager 上：容器少了这个类就会退回浏览器默认按钮（曾经踩过一次），
    // 这里补一道保险，无论 HTML 怎么写都能拿到正确外观
    if (!box.classList.contains("pager")) box.classList.add("pager");
    var pages = data.pages || 0;
    if (pages <= 1) {
      box.innerHTML = "";
      return;
    }
    var current = data.page || 1;
    var windowSize = 2;
    var list = [];
    for (var p = 1; p <= pages; p += 1) {
      if (p === 1 || p === pages || Math.abs(p - current) <= windowSize) list.push(p);
      else if (list[list.length - 1] !== "…") list.push("…");
    }
    // 与全站一致的胶囊控件：分段控件（.viewswitch）+ 筛选标签（.chip）同一套外观
    var step = ' class="pager-step" type="button" data-page="';
    var html = '<div class="pager-group" role="group" aria-label="分页导航">';
    html += '<button' + step + (current - 1) + '"' +
      (data.has_prev ? "" : " disabled") + " aria-label=\"上一页\">上一页</button>";
    list.forEach(function (item) {
      if (item === "…") html += '<span class="gap" aria-hidden="true">…</span>';
      else {
        html += '<button type="button" data-page="' + item + '"' +
          (item === current ? ' class="active" aria-current="page"' : "") +
          ' aria-label="第 ' + item + ' 页">' + item + "</button>";
      }
    });
    html += '<button' + step + (current + 1) + '"' +
      (data.has_next ? "" : " disabled") + " aria-label=\"下一页\">下一页</button>";
    html += "</div>";
    html += '<span class="jump">跳到 <input type="number" min="1" max="' + pages +
      '" value="' + current + '" aria-label="跳到页码"> 页</span>';
    box.innerHTML = html;
  }

  function updateCTALabel(total) {
    state.lastTotal = (total || total === 0) ? total : null;
    var btn = document.querySelector(".sidebar-actions .btn");
    if (!btn) return;
    btn.textContent = state.lastTotal === null
      ? "查看结果"
      : "查看 " + IRS.num(state.lastTotal) + " 条结果";
  }

  function updateViewButtons() {
    Array.prototype.forEach.call(document.querySelectorAll("#viewswitch button"), function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-view") === state.view);
    });
  }

  function setLoading(on) {
    var box = $("#results");
    if (box) box.parentNode.classList.toggle("loading", !!on);
  }

  // ---------------------------------------------------------------- 检索 //
  function run(page) {
    // ① 关键词以输入框为唯一真相，每次检索都重新读取
    state.q = $("#q").value.trim();
    state.page = Math.max(1, page || 1);
    var sizeSel = $("#page-size");
    if (sizeSel && sizeSel.value) state.page_size = parseInt(sizeSel.value, 10) || state.page_size;
    var sortSel = $("#sort");
    if (sortSel && sortSel.value) state.sort = sortSel.value;

    var params = IRS.searchParams(state, keys);
    params.set("page", String(state.page));
    if (state.page_size) params.set("page_size", String(state.page_size));

    setLoading(true);
    return IRS.getJSON(IRS.API + "/search", params)
      .then(function (data) {
        lastData = data;
        appliedKey = filtersKey(state.filters);
        if (data.page) state.page = data.page;

        renderSummary(data);
        renderTags(data.filter_tags);
        renderFacets(data.facets, data.dimensions);
        renderFuzzyHint(data);
        renderResults(data);
        renderPager(data);
        updateCTALabel(data.total);
        updateFilterHint();
        IRS.writeState(state, keys);
        setLoading(false);
      })
      .catch(function (err) {
        setLoading(false);
        var box = $("#results");
        if (box) box.innerHTML = emptyBlock("检索失败", IRS.esc(err.message));
        IRS.toast("检索失败：" + err.message, 3200);
      });
  }

  // ------------------------------------------------------------ 交互绑定 //
  function commitFilters() {
    renderTags(null);
    updateFilterHint();
  }

  function onFacetChange(ev) {
    var input = ev.target;
    if (!input || input.type !== "checkbox") return;
    var dim = input.getAttribute("data-dim");
    var value = input.value;
    var list = (state.filters[dim] || []).slice();
    var at = list.indexOf(value);
    if (input.checked && at < 0) list.push(value);
    if (!input.checked && at >= 0) list.splice(at, 1);
    if (list.length) state.filters[dim] = list;
    else delete state.filters[dim];

    var label = input.closest(".facet-option");
    if (label) label.classList.toggle("selected", input.checked);

    renderTags(null);
    updateFilterHint();
  }

  function onExpandClick(ev) {
    var btn = ev.target.closest("button[data-expand]");
    if (!btn) return;
    var dim = btn.getAttribute("data-expand");
    expandedGroups[dim] = !expandedGroups[dim];
    refreshFacetGroup(dim);
  }

  function onTagClick(ev) {
    if (ev.target.id === "clear-all") {
      state.filters = {};
      keys.forEach(refreshFacetGroup);
      renderTags(null);
      updateFilterHint();
      return;
    }
    var btn = ev.target.closest("button[data-dim][data-value]");
    if (!btn) return;
    var dim = btn.getAttribute("data-dim");
    var value = btn.getAttribute("data-value");
    var list = (state.filters[dim] || []).filter(function (v) { return v !== value; });
    if (list.length) state.filters[dim] = list;
    else delete state.filters[dim];
    refreshFacetGroup(dim);
    renderTags(null);
    updateFilterHint();
  }

  // ------------------------------------------------------------ 输入建议 //
  function kindLabel(kind) {
    return { colleges: "学院", majors: "专业", provinces: "省份", cities: "城市", cohorts: "届别", alias: "简称" }[kind] || "";
  }

  function buildSuggestIndex(data) {
    var groups = [
      ["colleges", data.colleges || []],
      ["majors", data.majors || []],
      ["provinces", data.provinces || []],
      ["cities", data.cities || []],
      ["cohorts", (data.cohorts || []).map(function (v) { return String(v); })]
    ];
    var out = [];
    groups.forEach(function (pair) {
      pair[1].forEach(function (value) {
        out.push({ value: String(value), kind: pair[0], text: String(value) });
      });
    });
    (data.aliases || []).forEach(function (pair) {
      out.push({ value: pair.alias, kind: "alias", text: pair.alias, alias: pair.canonical });
    });
    return out;
  }

  function renderSuggest(keyword) {
    var box = $("#suggest");
    if (!box) return;
    var q = String(keyword || "").trim().toLowerCase();
    var matches = [];
    if (!q) {
      matches = suggestItems.slice(0, 8);
    } else {
      suggestItems.forEach(function (item) {
        if (item.text.toLowerCase().indexOf(q) >= 0 ||
            (item.alias && item.alias.toLowerCase().indexOf(q) >= 0)) {
          matches.push(item);
        }
      });
      matches = matches.slice(0, 12);
    }
    suggestIndex = -1;
    if (!matches.length) {
      box.classList.remove("open");
      box.innerHTML = "";
      return;
    }
    var html = "";
    var lastKind = "";
    matches.forEach(function (item, i) {
      if (item.kind !== lastKind) {
        html += '<div class="group-title">' + kindLabel(item.kind) + "</div>";
        lastKind = item.kind;
      }
      html += '<div class="item" role="option" data-index="' + i + '" data-value="' + IRS.esc(item.value) + '">' +
        IRS.esc(item.value) +
        (item.alias ? '<span class="alias-hint"> → ' + IRS.esc(item.alias) + "</span>" : "") +
        '<span class="kind">' + kindLabel(item.kind) + "</span></div>";
    });
    box.innerHTML = html;
    box.classList.add("open");
  }

  function highlightSuggest(dir) {
    var box = $("#suggest");
    var items = box ? box.querySelectorAll(".item") : [];
    if (!items.length) return;
    suggestIndex = (suggestIndex + dir + items.length) % items.length;
    Array.prototype.forEach.call(items, function (el, i) {
      el.classList.toggle("active", i === suggestIndex);
    });
  }

  function acceptSuggest(value) {
    $("#q").value = value;
    var box = $("#suggest");
    if (box) { box.classList.remove("open"); box.innerHTML = ""; }
    suggestIndex = -1;
    run(1);
  }

  function bindSuggest() {
    var input = $("#q");
    var box = $("#suggest");
    if (!input || !box) return;

    IRS.getJSON(IRS.API + "/suggest")
      .then(function (data) { suggestItems = buildSuggestIndex(data); })
      .catch(function () { suggestItems = []; });

    input.addEventListener("input", IRS.debounce(function () {
      renderSuggest(input.value);
    }, 120));

    input.addEventListener("focus", function () {
      if (input.value.trim()) renderSuggest(input.value);
    });

    input.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowDown") { ev.preventDefault(); highlightSuggest(1); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); highlightSuggest(-1); }
      else if (ev.key === "Escape") { box.classList.remove("open"); }
      else if (ev.key === "Enter") {
        var active = box.querySelector(".item.active");
        if (active) { ev.preventDefault(); acceptSuggest(active.getAttribute("data-value")); }
      }
    });

    box.addEventListener("mousedown", function (ev) {
      var item = ev.target.closest(".item");
      if (!item) return;
      ev.preventDefault();
      acceptSuggest(item.getAttribute("data-value"));
    });

    document.addEventListener("click", function (ev) {
      if (!ev.target.closest(".searchbar")) box.classList.remove("open");
    });
  }

  // ------------------------------------------------------------ 其它绑定 //
  function bindUI() {
    var form = $("#search-form");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        run(1);
      });
    }

    var sortSel = $("#sort");
    if (sortSel) sortSel.addEventListener("change", function () { run(1); });
    var sizeSel = $("#page-size");
    if (sizeSel) sizeSel.addEventListener("change", function () { run(1); });

    document.addEventListener("click", function (ev) {
      var search = ev.target.closest("[data-search]");
      // #btn-search 是 submit 按钮，交给 form 的 submit 事件处理，避免重复请求
      if (search && search.type !== "submit") {
        ev.preventDefault();
        run(1);
        return;
      }
      var reset = ev.target.closest("[data-reset]");
      if (reset) {
        $("#q").value = "";
        state.filters = {};
        state.q = "";
        keys.forEach(refreshFacetGroup);
        renderTags(null);
        run(1);
        return;
      }
      var view = ev.target.closest("#viewswitch button");
      if (view) {
        state.view = view.getAttribute("data-view");
        updateViewButtons();
        if (lastData) renderResults(lastData);
        IRS.writeState(state, keys);
        return;
      }
      var page = ev.target.closest("#pager button[data-page]");
      if (page) {
        var target = parseInt(page.getAttribute("data-page"), 10);
        if (target >= 1) run(target);
      }
    });

    var facets = $("#facets");
    if (facets) {
      facets.addEventListener("change", onFacetChange);
      facets.addEventListener("click", onExpandClick);
    }

    var tagbar = $("#tagbar");
    if (tagbar) tagbar.addEventListener("click", onTagClick);

    $("#pager") && $("#pager").addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter") return;
      var input = ev.target.closest("input[type=number]");
      if (!input) return;
      run(Math.max(1, parseInt(input.value, 10) || 1));
    });

    window.addEventListener("popstate", function () {
      var restored = IRS.readState(keys);
      state.q = restored.q;
      state.sort = restored.sort;
      state.page_size = restored.page_size;
      state.view = restored.view;
      state.filters = restored.filters;
      $("#q").value = restored.q;
      updateViewButtons();
      run(restored.page);
    });
  }

  // ---------------------------------------------------------------- 启动 //
  function loadConfig() {
    return IRS.getJSON(IRS.API + "/config")
      .then(function (config) {
        if (config.dimensions && config.dimensions.length) {
          state.dimensions = config.dimensions;
          keys = config.dimensions.map(function (d) { return d.key; });
        }
        state.defaultPageSize = config.default_page_size || state.defaultPageSize;
        state.maxPageSize = config.max_page_size || state.maxPageSize;

        var sortSel = $("#sort");
        if (sortSel && config.sorts) {
          sortSel.innerHTML = config.sorts.map(function (item) {
            return '<option value="' + IRS.esc(item.value) + '">' + IRS.esc(item.label) + "</option>";
          }).join("");
        }
        var sizeSel = $("#page-size");
        if (sizeSel) {
          var sizes = [state.defaultPageSize];
          [50, state.maxPageSize].forEach(function (n) {
            if (n > sizes[0] && sizes.indexOf(n) < 0) sizes.push(n);
          });
          sizeSel.innerHTML = sizes.map(function (n) {
            return '<option value="' + n + '">' + n + " 条 / 页</option>";
          }).join("");
        }
      })
      .catch(function (err) {
        IRS.toast("配置加载失败，使用默认设置：" + err.message, 3000);
      });
  }

  IRS.ready(function () {
    var restored = IRS.readState(keys);
    state.q = restored.q;
    state.sort = restored.sort;
    state.page_size = restored.page_size;
    state.view = restored.view;
    state.filters = restored.filters;

    var input = $("#q");
    if (input) input.value = state.q;
    updateViewButtons();
    renderFacetSkeleton();
    renderTags(null);
    bindUI();
    bindSuggest();

    loadConfig().then(function () {
      if (state.sort) {
        var sortSel = $("#sort");
        if (sortSel) sortSel.value = state.sort;
      }
      if (state.page_size) {
        var sizeSel = $("#page-size");
        if (sizeSel) sizeSel.value = String(state.page_size);
      }
      appliedKey = filtersKey(state.filters);
      run(restored.page || 1);
    });
  });
})();
