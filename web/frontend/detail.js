/* ==========================================================================
   档案详情页（detail-preview.svg 版式）
   --------------------------------------------------------------------------
   版式：左侧「基本信息 / 选调去向 / 来源信息 / 命中证据」，右侧原图。
   约定：
   * 字段值优先取服务端 ``highlight.fields``（已转义），没有的键再本地转义；
   * 来源栏只保留对用户有意义的项（原文链接 / 发布时间 / 数据来源 / 通知标题），
     开发期信息不属于用户视角，不展示。
   ========================================================================== */
(function () {
  "use strict";

  var IRS = window.IRS;
  var $ = function (sel) { return document.querySelector(sel); };

  // 三栏字段：[字段名, 中文名]；中文名留空则由服务端 field_labels 兜底
  var BASIC_KEYS = [
    ["name", "姓名"], ["cohort", "届别"], ["college", "学院"],
    ["major", "专业"], ["degree", "学历"]
  ];
  var SELECTS_KEYS = [
    ["province", "省份"], ["city", "城市"],
    ["destination_org", "录用单位"], ["position", "岗位方向"],
    ["position_category", "岗位类别"], ["degree_level", "学历层次"],
    ["cohort_year", "届别年份"], ["name_pinyin", "姓名拼音"]
  ];

  /** 取值：先看服务端高亮字段（已转义），再退回本地转义。 */
  function valueHTML(item, key) {
    var fields = (item.highlight && item.highlight.fields) || {};
    if (fields.hasOwnProperty(key) && fields[key]) return fields[key];
    var raw = item[key];
    if (raw === null || raw === undefined || raw === "") return IRS.dash();
    return IRS.esc(raw);
  }

  function labelOf(item, key) {
    var labels = item.field_labels || [];
    for (var i = 0; i < labels.length; i += 1) {
      if (labels[i].key === key) return labels[i].label;
    }
    return key;
  }

  function rowsHTML(item, pairs) {
    return pairs.map(function (pair) {
      var key = pair[0];
      var label = pair[1] || labelOf(item, key);
      return "<div><dt>" + IRS.esc(label) + "</dt><dd>" + valueHTML(item, key) + "</dd></div>";
    }).join("");
  }

  // ------------------------------------------------------------------ 头部 //
  function factCell(label, html) {
    return '<div><span class="k">' + IRS.esc(label) + '</span><span class="v">' + html + "</span></div>";
  }

  function renderHero(item) {
    var hero = $("#detail-hero");
    if (!hero) return;
    var name = item.name || "未识别姓名";
    var chips = [
      item.cohort || (item.cohort_year ? item.cohort_year + "届" : null),
      item.degree_level || item.degree
    ].filter(Boolean)
      .map(function (text) { return '<span class="chip-cohort">' + IRS.esc(text) + "</span>"; })
      .join("");
    var meta = [item.college, item.major]
      .filter(Boolean)
      .map(IRS.esc)
      .join(' <span class="sep">·</span> ');

    var facts =
      factCell("录用单位", valueHTML(item, "destination_org")) +
      factCell("岗位方向", valueHTML(item, "position")) +
      factCell("岗位类别", item.position_category ? IRS.esc(item.position_category) : IRS.dash());

    var acts =
      '<a class="mini" href="/search.html">← 返回检索</a>' +
      (item.source_url
        ? '<a class="mini" href="' + IRS.esc(item.source_url) + '" target="_blank" rel="noopener">查看原文 ↗</a>'
        : "") +
      '<button class="mini" type="button" id="copy-link">复制链接</button>';

    var dest =
      '<span class="label">DESTINATION · 去向</span>' +
      '<span class="province">' + (item.province ? IRS.esc(IRS.regionShort(item.province)) : IRS.dash()) + "</span>" +
      '<span class="org">' + valueHTML(item, "city") + "</span>" +
      '<span class="dir">' + valueHTML(item, "destination_org") + "</span>" +
      '<div class="rule"></div>' +
      '<span class="fill">' + destNote(item) + "</span>";

    hero.innerHTML =
      '<div class="profile">' +
      '<div class="rings" aria-hidden="true"><i></i><i></i></div>' +
      '<div class="row">' +
      '<span class="pavatar" aria-hidden="true">' + IRS.esc(IRS.initial(name)) + "</span>" +
      "<div>" +
      "<h1>" + valueHTML(item, "name") + chips + "</h1>" +
      '<div class="meta">' + (meta || IRS.dash()) + "</div>" +
      "</div>" +
      "</div>" +
      '<div class="rule"></div>' +
      '<div class="facts">' + facts + "</div>" +
      '<div class="acts">' + acts + "</div>" +
      "</div>" +
      '<div class="dest">' + dest + "</div>";

    var copy = $("#copy-link");
    if (copy) {
      copy.addEventListener("click", function () {
        var url = window.location.href;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(function () {
            IRS.toast("已复制本页链接");
          }).catch(function () { IRS.toast("复制失败，请手动复制地址栏", 2600); });
        } else {
          IRS.toast("当前浏览器不支持自动复制", 2600);
        }
      });
    }
  }

  function destNote(item) {
    var bits = [];
    if (typeof item.confidence === "number") {
      bits.push("抽取置信度 <b>" + IRS.esc(IRS.pct(item.confidence, 1)) + "</b>");
    }
    if (item.field_count) bits.push("识别字段 <b>" + IRS.num(item.field_count) + "</b> 项");
    if (!bits.length) bits.push("档案来自公开通知公告");
    return bits.join(" · ");
  }

  // ------------------------------------------------------------ 三栏字段 //
  function renderPanels(item) {
    var basic = $("#basic-grid");
    if (basic) basic.innerHTML = rowsHTML(item, BASIC_KEYS);

    var selects = $("#selects-grid");
    if (selects) selects.innerHTML = rowsHTML(item, SELECTS_KEYS);

    var source = $("#source-grid");
    if (source) {
      var rows = [];
      if (item.notice_title) {
        var title = item.source_url
          ? '<a href="' + IRS.esc(item.source_url) + '" target="_blank" rel="noopener">' +
            IRS.esc(item.notice_title) + " ↗</a>"
          : IRS.esc(item.notice_title);
        rows.push("<div><dt>通知标题</dt><dd>" + title + "</dd></div>");
      }
      if (item.organization) {
        rows.push("<div><dt>数据来源</dt><dd>" + IRS.esc(item.organization) + "</dd></div>");
      }
      if (item.release_time) {
        rows.push("<div><dt>发布时间</dt><dd>" + IRS.esc(item.release_time) + "</dd></div>");
      }
      if (item.notice_type) {
        rows.push("<div><dt>公告类型</dt><dd>" + IRS.esc(item.notice_type) + "</dd></div>");
      }
      if (item.source_url) {
        rows.push('<div><dt>原文链接</dt><dd><a class="mono" href="' + IRS.esc(item.source_url) +
          '" target="_blank" rel="noopener">' + IRS.esc(item.source_url) + "</a></dd></div>");
      }
      if (!rows.length) rows.push("<div><dt>来源信息</dt><dd>" + IRS.dash() + "</dd></div>");
      source.innerHTML = rows.join("");
    }
  }

  // ------------------------------------------------------------ 识别依据 //
  function renderEvidence(item) {
    var box = $("#evidence-body");
    if (!box) return;
    box.textContent = item.evidence
      ? String(item.evidence)
      : "该条记录的字段来自通知公告正文，点击右侧原图可直接核对。";
  }

  // ------------------------------------------------------------ 底部动作 //
  function renderActions(item) {
    var bar = $("#detail-actions");
    if (!bar) return;
    var name = item.name || "这条档案";
    bar.innerHTML =
      '<span class="note">' + IRS.esc(name) + " 的去向信息来自公开通知公告，仅供参考。</span>" +
      '<span class="spacer"></span>' +
      '<a class="mini" href="/search.html">← 返回检索</a>' +
      '<a class="mini" href="/stats.html">看看整体分布</a>';
  }

  // ---------------------------------------------------------------- 原图 //
  function renderImage(item) {
    var frame = document.querySelector(".thumb-frame");
    var img = $("#image-thumb");
    var link = $("#image-link");
    var note = $("#image-note");
    var url = item.image_local_url;

    if (url) {
      if (img) {
        img.src = url;
        img.alt = (item.name || "记录") + " 的通知公告原图";
        img.hidden = false;
        img.addEventListener("error", function () {
          img.hidden = true;
          var ph = $("#image-ph");
          if (ph) { ph.hidden = false; ph.textContent = "原图加载失败"; }
        });
      }
      if (link) {
        link.href = url;
        link.hidden = false;
      }
      if (frame) frame.hidden = false;
      var ph = $("#image-ph");
      if (ph) ph.hidden = true;
      if (note) {
        var bits = [];
        if (item.size_bytes) bits.push("约 " + Math.round(Number(item.size_bytes) / 1024) + " KB");
        if (item.cohort_year) bits.push(item.cohort_year + " 届公告");
        note.textContent = bits.join(" · ");
      }
    } else {
      if (img) img.hidden = true;
      if (link) link.hidden = true;
      var ph2 = $("#image-ph");
      if (ph2) { ph2.hidden = false; ph2.textContent = "这条记录没有可用的原图"; }
      if (note) note.textContent = "";
    }
  }

  function renderError(message) {
    var hero = $("#detail-hero");
    if (hero) {
      hero.style.gridTemplateColumns = "minmax(0, 1fr)";
      hero.innerHTML =
        '<div class="empty"><span class="big" aria-hidden="true">◍</span>' +
        IRS.esc(message) +
        '<p class="hint">可以返回检索页重新找一条档案。<br>' +
        '<a class="mini" href="/search.html">← 回到检索</a></p></div>';
    }
    var main = $("#detail-main");
    if (main) main.hidden = true;
    var bar = $("#detail-actions");
    if (bar) bar.hidden = true;
  }

  // ---------------------------------------------------------------- 启动 //
  IRS.ready(function () {
    var id = new URLSearchParams(window.location.search).get("id");
    if (!id) {
      renderError("没有指定要查看的档案编号");
      return;
    }
    IRS.getJSON(IRS.API + "/record/" + encodeURIComponent(id))
      .then(function (item) {
        if (item.name) document.title = item.name + " · 档案详情 · 远帆";
        renderHero(item);
        renderPanels(item);
        renderEvidence(item);
        renderActions(item);
        renderImage(item);
      })
      .catch(function (err) {
        renderError(err.message || "记录加载失败");
      });
  });
})();
