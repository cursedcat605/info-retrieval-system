/* ==========================================================================
   静态检索内核：让 GitHub Pages 上的纯静态站点拥有与 FastAPI 后端一致的检索能力
   --------------------------------------------------------------------------
   思路
   ----
   后端 7 个只读接口的数据在导出时已全部预渲染进 ``api/bootstrap.json``，
   本文件在浏览器里重新实现「筛选 / 排序 / 分页 / 高亮 / 分面计数」，
   并接管 ``fetch``，把 ``/api/*`` 请求就地应答。

   之所以拦截 ``fetch`` 而不是改 ``IRS.getJSON``：``common.js`` 里的
   ``fillStamp`` 直接用模块内私有的 ``getJSON``，外部改不到。

   与后端逐条对齐的逻辑见 ``src/search/engine.py``、``src/database/repo.py``、
   ``src/search/highlight.py``；改动其中任何一处，这里都要同步。
   ========================================================================== */
(function (global) {
  "use strict";

  /** 后端 ``schema.SEARCHABLE_COLUMNS``（LIKE 兜底时逐列匹配） */
  var SEARCHABLE_COLUMNS = [
    "name", "cohort", "college", "major", "destination_org",
    "city", "province", "position", "degree", "raw_text"
  ];

  /**
   * 后端 FTS 索引的列：``SEARCHABLE_COLUMNS`` + ``notice_title``。
   * 库里的列名是 ``ocr_text``，但建索引时取的就是 ``r.raw_text``
   * （见 ``repo._rebuild_fts``），所以这里直接用 ``raw_text``，
   * 值与 FTS 索引里的 ``ocr_text`` 完全一致。
   */
  var FTS_COLUMNS = SEARCHABLE_COLUMNS.concat(["notice_title"]);

  /** 后端 ``highlight.FIELD_LABELS``：顺序即详情页字段顺序 */
  var FIELD_LABELS = [
    ["name", "姓名"], ["cohort", "届别"], ["college", "学院"], ["major", "专业"],
    ["degree", "学历"], ["province", "省份"], ["city", "城市"],
    ["destination_org", "单位"], ["position", "岗位"], ["notice_title", "文章标题"]
  ];

  /** 后端 ``repo.FILTER_COLUMNS`` 的键，顺序沿用 ``FACET_DIMENSIONS`` */
  var DIMENSION_KEYS = [
    "province", "position_category", "degree_level", "cohort_year",
    "college", "major", "city"
  ];

  /** 查询串分隔符，与 ``engine._SPLIT_RE`` 一致 */
  var SPLIT_RE = /[\s,，、;；+|/]+/;

  /** 后端 ``engine._TERM_CANDIDATE_LIMIT`` */
  var TERM_CANDIDATE_LIMIT = 2000;

  /** 后端 ``repo.facet_counts`` 的默认 limit */
  var FACET_LIMIT = 300;

  /** 后端 ``highlight.SNIPPET_WIDTH`` */
  var SNIPPET_WIDTH = 160;

  var SITE_DIR = (function () {
    // bootstrap.json 与本文件同级；用当前脚本地址定位，兼容任意子目录部署
    var script = document.currentScript;
    if (!script) {
      var all = document.getElementsByTagName("script");
      for (var i = all.length - 1; i >= 0; i -= 1) {
        if (/static-api\.js/.test(all[i].src || "")) { script = all[i]; break; }
      }
    }
    var src = (script && script.src) || "";
    return src.replace(/[^/]*$/, "") || "./";
  })();

  var STATE = {
    records: [],
    byId: {},
    details: {},
    bootstrap: null,
    fuzzyIndex: null,
    loading: null
  };

  var fetchNative = typeof global.fetch === "function"
    ? global.fetch.bind(global)
    : function () { return Promise.reject(new Error("当前环境不支持 fetch")); };

  // ------------------------------------------------------------------ 文本 //
  function escapeHTML(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#x27;");
  }

  function uniqueKeepOrder(items) {
    var seen = Object.create(null);
    var out = [];
    for (var i = 0; i < items.length; i += 1) {
      var key = (items[i] === null || items[i] === undefined ? "" : String(items[i])).trim();
      if (!key || seen[key]) continue;
      seen[key] = true;
      out.push(key);
    }
    return out;
  }

  /** ``utils/text.is_latin``：只含 ASCII 字母数字标点，且至少一个字母 */
  var LATIN_RE = /^[A-Za-z0-9\s\-.'’]+$/;
  function isLatin(text) {
    var t = String(text === null || text === undefined ? "" : text).trim();
    if (!t || !LATIN_RE.test(t)) return false;
    return /[A-Za-z]/.test(t);
  }

  /** ``utils/text.normalize_latin``：``Zhang San`` → ``zhangsan`` */
  function normalizeLatin(text) {
    return String(text === null || text === undefined ? "" : text)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "");
  }

  /** ``utils/text.shorten_region``：``湖北省`` → ``湖北`` */
  function shortenRegion(name) {
    var text = String(name === null || name === undefined ? "" : name).trim();
    var suffixes = ["特别行政区", "自治区", "省", "市"];
    for (var i = 0; i < suffixes.length; i += 1) {
      var suffix = suffixes[i];
      if (text.length > suffix.length && text.slice(-suffix.length) === suffix) {
        return text.slice(0, text.length - suffix.length);
      }
    }
    return text;
  }

  function normalizeFuzzyTerm(text) {
    return String(text === null || text === undefined ? "" : text)
      .normalize("NFKC")
      .replace(/\s+/g, "")
      .toLowerCase();
  }

  function containsIgnoreCase(haystack, needle) {
    if (haystack === null || haystack === undefined) return false;
    return String(haystack).toLowerCase().indexOf(needle) >= 0;
  }

  // -------------------------------------------------------------- 模糊词表 //
  /** ``fuzzy._build_index``：``归一化写法 → 所在词组的全部写法`` */
  function buildFuzzyIndex(groups) {
    var index = Object.create(null);
    (groups || []).forEach(function (terms) {
      terms.forEach(function (term) {
        var key = normalizeFuzzyTerm(term);
        if (!index[key]) index[key] = [];
        index[key].push(terms);
      });
    });
    return index;
  }

  /** ``fuzzy.expand``：用户原词在最前，其余按词表顺序 */
  function expandTerm(term) {
    var text = String(term === null || term === undefined ? "" : term).trim();
    if (!text) return [];
    var index = STATE.fuzzyIndex || Object.create(null);
    var merged = [text];
    (index[normalizeFuzzyTerm(text)] || []).forEach(function (terms) {
      merged = merged.concat(terms);
    });
    return uniqueKeepOrder(merged);
  }

  /** ``fuzzy.expand_terms`` */
  function expandTerms(terms) {
    return terms.map(function (term) {
      var variants = expandTerm(term);
      return {
        term: term,
        variants: variants,
        added: variants.filter(function (v) { return v !== term; })
      };
    });
  }

  // --------------------------------------------------------------- 查询解析 //
  function parseQuery(raw) {
    var terms = [];
    String(raw === null || raw === undefined ? "" : raw).trim().split(SPLIT_RE).forEach(function (part) {
      var term = part.trim();
      if (term && terms.indexOf(term) < 0) terms.push(term);
    });
    return terms;
  }

  function parseFilters(params) {
    var result = {};
    DIMENSION_KEYS.forEach(function (dimension) {
      var values = params.getAll(dimension).map(function (v) { return String(v); });
      var kept = uniqueKeepOrder(values);
      if (kept.length) result[dimension] = kept;
    });
    return result;
  }

  function displayValue(dimension, value) {
    if (dimension === "cohort_year") return String(value) + "届";
    if (dimension === "province") return shortenRegion(value);
    return String(value);
  }

  function filterTags(filters, dimensions) {
    var tags = [];
    dimensions.forEach(function (dim) {
      (filters[dim.key] || []).forEach(function (value) {
        tags.push({
          dimension: dim.key,
          value: String(value),
          label: dim.label,
          display: displayValue(dim.key, value)
        });
      });
    });
    return tags;
  }

  // ------------------------------------------------------------------ 匹配 //
  function matchFilters(record, filters, excludeDimension) {
    for (var i = 0; i < DIMENSION_KEYS.length; i += 1) {
      var dimension = DIMENSION_KEYS[i];
      if (dimension === excludeDimension) continue;
      var values = filters && filters[dimension];
      if (!values || !values.length) continue;
      var actual = record[dimension];
      var hit = false;
      for (var j = 0; j < values.length; j += 1) {
        if (String(actual) === String(values[j])) { hit = true; break; }
      }
      if (!hit) return false;
    }
    return true;
  }

  /**
   * ``repo._keyword_clause`` 的三条路径：
   * 1. 中文 ≥3 字 → FTS5 trigram，等价于在 FTS 列里做子串匹配；
   * 2. 拉丁输入 → 关键列 LIKE，并叠加姓名全拼/声母；
   * 3. 其余（1~2 字短词）→ 关键列 LIKE 兜底。
   */
  function keywordMatch(record, keyword) {
    var kw = String(keyword === null || keyword === undefined ? "" : keyword).trim();
    if (!kw) return true;
    var latin = isLatin(kw);
    var columns = (kw.length >= 3 && !latin) ? FTS_COLUMNS : SEARCHABLE_COLUMNS;
    for (var i = 0; i < columns.length; i += 1) {
      if (containsIgnoreCase(record[columns[i]], kw)) return true;
    }
    if (!latin) return false;
    var normalized = normalizeLatin(kw);
    if (!normalized) return false;
    var initials = String(record.name_initials || "").toLowerCase();
    if (normalized.length <= 2) return initials.indexOf(normalized) === 0;
    return containsIgnoreCase(record.name_pinyin, normalized) || initials === normalized;
  }

  /** 关键词是否走 FTS 路径（决定相关度排序方式，见 ``repo._order_sql``） */
  function usesFts(keyword) {
    var kw = String(keyword || "").trim();
    return kw.length >= 3 && !isLatin(kw);
  }

  /**
   * FTS5 ``bm25(selects_fts)`` 的逐行复刻（见 ``ext/fts5/fts5_aux.c``）。
   *
   * 关键点：bm25 是**按整行**算的，不是按列。源码里
   *
   *   * ``nHit`` = 全表包含该短语的行数（跨所有列）；
   *   * ``avgdl`` = 全表 token 总数 / 行数，token 按整行统计；
   *   * ``D`` = 当前行的 token 总数（``xColumnSize(-1)``）；
   *   * ``aFreq`` = 该短语在当前行**所有列**出现次数之和（列权重默认 1）。
   *
   * 因此只有一个 idf、一个 tf，公式为
   *
   *   score = idf · tf·(k1+1) / (tf + k1·(1 - b + b·D/avgdl))
   *   idf = ln((N - nHit + 0.5) / (nHit + 0.5))，非正时取 1e-6
   *
   * 索引用 trigram 分词器，一个字段的 token 数等于「字符数 - 2」。
   * 实测该实现与 SQLite 的返回值完全一致（误差 ~1e-16）。
   */
  var BM25_K1 = 1.2;
  var BM25_B = 0.75;
  var bm25Stats = null;

  function columnTokens(value) {
    if (value === null || value === undefined) return 0;
    return Math.max(0, String(value).length - 2);
  }

  function rowTokens(record) {
    var total = 0;
    for (var i = 0; i < FTS_COLUMNS.length; i += 1) {
      total += columnTokens(record[FTS_COLUMNS[i]]);
    }
    return total;
  }

  function bm25Baseline() {
    if (bm25Stats) return bm25Stats;
    var sum = 0;
    STATE.records.forEach(function (record) { sum += rowTokens(record); });
    var n = STATE.records.length || 1;
    bm25Stats = { n: n, avgdl: (sum / n) || 1 };
    return bm25Stats;
  }

  /** 子串出现次数（= trigram 短语在该列命中的实例数） */
  function occurrences(value, needle) {
    if (value === null || value === undefined) return 0;
    var text = String(value).toLowerCase();
    var count = 0;
    var index = text.indexOf(needle);
    while (index >= 0) {
      count += 1;
      index = text.indexOf(needle, index + needle.length);
    }
    return count;
  }

  /** 返回 ``{记录 id: bm25 分数}``，与后端一样是负值，越小越相关 */
  function bm25Scores(keyword) {
    var needle = String(keyword || "").toLowerCase();
    var stats = bm25Baseline();
    var hits = [];
    STATE.records.forEach(function (record) {
      var freq = 0;
      for (var i = 0; i < FTS_COLUMNS.length; i += 1) {
        freq += occurrences(record[FTS_COLUMNS[i]], needle);
      }
      if (freq) hits.push({ record: record, freq: freq });
    });

    var nHit = hits.length;
    var idf = Math.log((stats.n - nHit + 0.5) / (nHit + 0.5));
    if (!(idf > 0)) idf = 1e-6;

    var scores = Object.create(null);
    hits.forEach(function (entry) {
      var freq = entry.freq;
      var dl = rowTokens(entry.record);
      var norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / stats.avgdl);
      scores[entry.record.id] = -idf * (freq * (BM25_K1 + 1)) / (freq + norm);
    });
    return scores;
  }

  // ------------------------------------------------------------------ 排序 //
  /** 单字段比较；``null``/空串按 SQLite 语义视作最小值。 */
  function compareField(a, b, field, direction) {
    var left = a[field];
    var right = b[field];
    var leftEmpty = left === null || left === undefined || left === "";
    var rightEmpty = right === null || right === undefined || right === "";
    if (leftEmpty && rightEmpty) return 0;
    if (leftEmpty) return direction === "asc" ? -1 : 1;
    if (rightEmpty) return direction === "asc" ? 1 : -1;
    if (typeof left === "number" && typeof right === "number") {
      if (left === right) return 0;
      return direction === "asc" ? left - right : right - left;
    }
    var x = String(left);
    var y = String(right);
    if (x === y) return 0;
    var ascending = x < y;
    return (direction === "asc") === ascending ? -1 : 1;
  }

  function chain(comparators) {
    return function (a, b) {
      for (var i = 0; i < comparators.length; i += 1) {
        var result = comparators[i](a, b);
        if (result) return result;
      }
      return 0;
    };
  }

  function byField(field, direction) {
    return function (a, b) { return compareField(a, b, field, direction); };
  }

  var SORT_COMPARATORS = {
    cohort_desc: chain([byField("cohort_year", "desc"), byField("confidence", "desc"), byField("id", "asc")]),
    cohort_asc: chain([byField("cohort_year", "asc"), byField("confidence", "desc"), byField("id", "asc")]),
    name_pinyin: chain([byField("name_pinyin", "asc"), byField("name", "asc"), byField("id", "asc")]),
    confidence_desc: chain([byField("confidence", "desc"), byField("cohort_year", "desc"), byField("id", "asc")])
  };

  function orderInPlace(list, sort, keyword) {
    var comparator = SORT_COMPARATORS[sort];
    if (comparator) {
      list.sort(comparator);
      return list;
    }
    // ``relevance`` 与未知排序都落到相关度：FTS 走 bm25，LIKE 走置信度
    var kw = String(keyword || "").trim();
    if (usesFts(kw)) {
      var scores = bm25Scores(kw);
      list.sort(chain([
        // bm25 是负值，越小越相关（与后端 ``ORDER BY bm25(...)`` 升序一致）
        function (a, b) { return (scores[a.id] || 0) - (scores[b.id] || 0); },
        byField("confidence", "desc"),
        byField("id", "asc")
      ]));
    } else {
      list.sort(chain([
        byField("confidence", "desc"),
        byField("cohort_year", "desc"),
        byField("id", "asc")
      ]));
    }
    return list;
  }

  // ------------------------------------------------------------------ 检索 //
  function candidatePool(recordIds) {
    if (!recordIds) return STATE.records;
    var allow = Object.create(null);
    recordIds.forEach(function (id) { allow[id] = true; });
    return STATE.records.filter(function (record) { return allow[record.id]; });
  }

  /** ``repo.search`` 的等价实现 */
  function dbSearch(keyword, filters, sort, limit, offset, recordIds) {
    var kw = String(keyword === null || keyword === undefined ? "" : keyword).trim();
    var matched = candidatePool(recordIds).filter(function (record) {
      if (!matchFilters(record, filters, null)) return false;
      return kw ? keywordMatch(record, kw) : true;
    });
    var mode = kw ? (usesFts(kw) ? "fts" : "like") : "all";
    if (kw && isLatin(kw)) mode = "pinyin";
    orderInPlace(matched, sort, kw);
    return {
      total: matched.length,
      items: matched.slice(offset, offset + limit),
      mode: mode,
      sort: sort
    };
  }

  /** ``engine._match_ids`` */
  function matchIds(keyword, filters, limit) {
    return dbSearch(keyword, filters, "relevance", limit, 0).items.map(function (row) { return row.id; });
  }

  /** ``engine._page_by_ids``：``relevance`` 保留传入顺序，其余交给排序 */
  function pageByIds(ids, filters, sort, size, offset) {
    var pool = ids.map(function (id) { return STATE.byId[id]; }).filter(Boolean);
    if (sort !== "relevance") orderInPlace(pool, sort, "");
    return pool.slice(offset, offset + size);
  }

  /** ``engine._fuzzy_supplement``：原词精确命中在前，词表别名的命中补在后 */
  function fuzzySupplement(base, searchTerms, filters) {
    var exact = matchIds(base, filters, TERM_CANDIDATE_LIMIT);
    var seen = Object.create(null);
    exact.forEach(function (id) { seen[id] = true; });
    var extra = [];
    var groups = [{ term: base, total: exact.length }];
    searchTerms.forEach(function (variant) {
      if (variant === base) return;
      var ids = matchIds(variant, filters, TERM_CANDIDATE_LIMIT);
      groups.push({ term: variant, total: ids.length });
      ids.forEach(function (id) {
        if (seen[id]) return;
        seen[id] = true;
        extra.push(id);
      });
    });
    if (!extra.length) return null;
    return { ids: exact.concat(extra), groups: groups };
  }

  // ------------------------------------------------------------------ 分面 //
  /** ``repo.facet_counts``：每个维度计数时排除该维度自身的选择 */
  function facetCounts(keyword, filters, recordIds) {
    var kw = String(keyword || "").trim();
    var pool = candidatePool(recordIds);
    var result = {};
    DIMENSION_KEYS.forEach(function (dimension) {
      var counts = Object.create(null);
      var order = [];
      for (var i = 0; i < pool.length; i += 1) {
        var record = pool[i];
        if (!matchFilters(record, filters, dimension)) continue;
        if (!recordIds && kw && !keywordMatch(record, kw)) continue;
        var value = record[dimension];
        if (value === null || value === undefined || value === "") continue;
        var key = String(value);
        if (counts[key] === undefined) { counts[key] = 0; order.push(key); }
        counts[key] += 1;
      }
      order.sort(function (a, b) {
        if (counts[b] !== counts[a]) return counts[b] - counts[a];
        return a < b ? -1 : (a > b ? 1 : 0);
      });
      result[dimension] = order.slice(0, FACET_LIMIT).map(function (key) {
        return {
          value: dimension === "cohort_year" ? Number(key) : key,
          count: counts[key]
        };
      });
    });
    return result;
  }

  /** ``engine._mark_facets``：补展示名与 selected 标记，固定维度顺序 */
  function markFacets(facetData, filters, dimensions) {
    var result = {};
    dimensions.forEach(function (dim) {
      var chosen = Object.create(null);
      (filters[dim.key] || []).forEach(function (value) { chosen[String(value)] = true; });
      result[dim.key] = (facetData[dim.key] || []).map(function (option) {
        return {
          value: option.value,
          count: option.count,
          label: displayValue(dim.key, option.value),
          selected: Boolean(chosen[String(option.value)])
        };
      });
    });
    return result;
  }

  // ------------------------------------------------------------------ 高亮 //
  function escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /** ``pinyin.matches``：靠库里预存的 ``name_pinyin`` / ``name_initials`` 判定 */
  function pinyinMatches(record, query) {
    if (!record || !record.name || !isLatin(query)) return false;
    if (!record.name_pinyin) return false;
    var q = normalizeLatin(query);
    if (!q) return false;
    var initials = String(record.name_initials || "");
    if (q.length <= 2) return initials.indexOf(q) === 0;
    return String(record.name_pinyin).indexOf(q) >= 0 || initials.indexOf(q) === 0;
  }

  /** ``highlight.effective_terms``：拼音命中时把整个姓名也纳入高亮 */
  function effectiveTerms(record, terms) {
    var expanded = uniqueKeepOrder(terms);
    expanded.slice().forEach(function (term) {
      if (pinyinMatches(record, term)) expanded.push(String(record.name));
    });
    return uniqueKeepOrder(expanded);
  }

  /** ``highlight._compile``：去重 → 按词长降序 → 转义 → 拼接 */
  function compilePattern(terms) {
    var cleaned = uniqueKeepOrder(terms);
    if (!cleaned.length) return null;
    cleaned.sort(function (a, b) { return b.length - a.length; });
    return new RegExp(cleaned.map(escapeRegExp).join("|"), "gi");
  }

  /** ``highlight.mark``：先转义再插 ``<mark>``，返回 ``[HTML, 是否命中]`` */
  function markText(text, pattern) {
    var escaped = escapeHTML(text);
    if (!escaped || !pattern) return [escaped, false];
    var hits = 0;
    pattern.lastIndex = 0;
    var html = escaped.replace(pattern, function (match) {
      hits += 1;
      return "<mark>" + match + "</mark>";
    });
    return [html, hits > 0];
  }

  /** ``highlight.snippet``：截取包含首个命中词、宽 160 的窗口 */
  function buildSnippet(rawText, pattern) {
    var body = String(rawText === null || rawText === undefined ? "" : rawText).trim();
    if (!body) return null;
    if (!pattern) return escapeHTML(body.slice(0, SNIPPET_WIDTH));
    pattern.lastIndex = 0;
    var match = pattern.exec(body);
    if (!match) return null;
    var half = Math.max(0, Math.floor((SNIPPET_WIDTH - match[0].length) / 2));
    var start = Math.max(0, match.index - half);
    var end = Math.min(body.length, start + SNIPPET_WIDTH);
    var windowText = body.slice(start, end);
    pattern.lastIndex = 0;
    return (start > 0 ? "…" : "") + markText(windowText, pattern)[0] + (end < body.length ? "…" : "");
  }

  /** ``highlight.build`` */
  function buildHighlight(record, terms) {
    var effective = effectiveTerms(record, terms);
    var pattern = compilePattern(effective);
    var fields = {};
    var matchedFields = [];
    FIELD_LABELS.forEach(function (pair) {
      var result = markText(record[pair[0]], pattern);
      fields[pair[0]] = result[0];
      if (result[1]) matchedFields.push(pair[0]);
    });
    return {
      fields: fields,
      matched_fields: matchedFields,
      snippet: buildSnippet(record.raw_text, pattern),
      matched_terms: effective
    };
  }

  // -------------------------------------------------------------- 列表项 //
  /** ``app._decorate_list_item``：列表页不下发正文与 OCR 证据 */
  function toListItem(record, terms) {
    var item = {};
    Object.keys(record).forEach(function (key) {
      if (key === "raw_text" || key === "evidence") return;
      item[key] = record[key];
    });
    item.highlight = buildHighlight(record, terms);
    return item;
  }

  // ------------------------------------------------------------- 接口实现 //
  function apiSearch(params, bootstrap) {
    var terms = parseQuery(params.get("q") || "");
    var active = parseFilters(params);
    var dimensions = bootstrap.dimensions;
    var defaultPageSize = bootstrap.config.default_page_size || 20;
    var maxPageSize = bootstrap.config.max_page_size || 200;

    var expanded = expandTerms(terms);
    var searchTerms = uniqueKeepOrder(
      expanded.reduce(function (all, group) { return all.concat(group.variants); }, [])
    );
    var termGroups = expanded.filter(function (group) { return group.added.length; });

    var pageNumber = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
    var size = parseInt(params.get("page_size") || String(defaultPageSize), 10);
    if (!isFinite(size) || size <= 0) size = defaultPageSize;
    size = Math.max(1, Math.min(size, maxPageSize));
    var offset = (pageNumber - 1) * size;

    var resolvedSort = String(params.get("sort") || "").trim()
      || (searchTerms.length ? "relevance" : "cohort_desc");

    var keyword = "";
    var groups = [];
    var candidateIds = null;
    var mode = "all";
    var items = [];
    var total = 0;

    if (terms.length <= 1) {
      var base = terms.length ? terms[0] : "";
      var supplement = (base && termGroups.length) ? fuzzySupplement(base, searchTerms, active) : null;
      if (supplement === null) {
        keyword = base;
        var fast = dbSearch(base, active, resolvedSort, size, offset);
        total = fast.total;
        items = fast.items;
        mode = fast.mode;
        termGroups = [];
      } else {
        mode = "fuzzy";
        candidateIds = supplement.ids;
        groups = supplement.groups;
        total = candidateIds.length;
        items = pageByIds(candidateIds, active, resolvedSort, size, offset);
      }
    } else {
      var perTerm = expanded.map(function (group) {
        var ids = [];
        var seen = Object.create(null);
        group.variants.forEach(function (variant) {
          matchIds(variant, active, TERM_CANDIDATE_LIMIT).forEach(function (id) {
            if (seen[id]) return;
            seen[id] = true;
            ids.push(id);
          });
        });
        groups.push({ term: group.term, total: ids.length });
        return ids;
      });

      var common = perTerm.length
        ? perTerm.slice(1).reduce(function (acc, ids) {
          var allowed = Object.create(null);
          ids.forEach(function (id) { allowed[id] = true; });
          return acc.filter(function (id) { return allowed[id]; });
        }, perTerm[0].slice())
        : [];

      if (common.length) {
        mode = "and";
        candidateIds = common.slice().sort(function (a, b) { return a - b; });
        total = candidateIds.length;
        // 后端 ``_db_search(keyword="", record_ids=...)``：空关键词下的
        // ``relevance`` 落到 ``_RELEVANCE_LIKE``（置信度 → 届别 → id）
        items = dbSearch("", active, resolvedSort, size, offset, candidateIds).items;
      } else {
        mode = "or";
        var hits = Object.create(null);
        perTerm.forEach(function (ids) {
          ids.forEach(function (id) { hits[id] = (hits[id] || 0) + 1; });
        });
        candidateIds = Object.keys(hits).map(Number).sort(function (a, b) {
          if (hits[b] !== hits[a]) return hits[b] - hits[a];
          return a - b;
        });
        total = candidateIds.length;
        items = pageByIds(candidateIds, active, resolvedSort, size, offset);
      }
    }

    var facetData = candidateIds === null
      ? facetCounts(keyword, active, null)
      : facetCounts("", active, candidateIds);

    var listItems = items.map(function (record) { return toListItem(record, searchTerms); });

    var pages = size > 0 ? Math.ceil(total / size) : 0;
    var fuzzySummary = bootstrap.config.fuzzy || {};
    return {
      query: params.get("q") || "",
      terms: terms,
      search_terms: searchTerms,
      term_groups: termGroups,
      fuzzy: fuzzySummary,
      mode: mode,
      total: total,
      page: pageNumber,
      page_size: size,
      pages: pages,
      has_prev: pageNumber > 1,
      has_next: pageNumber < pages,
      sort: resolvedSort,
      sort_options: bootstrap.sort_options,
      filters: active,
      filter_tags: filterTags(active, dimensions),
      facets: markFacets(facetData, active, dimensions),
      dimensions: dimensions,
      groups: groups,
      pinyin: fuzzySummary.available !== false,
      items: listItems
    };
  }

  /**
   * 后端 ``GET /api/record/{id}`` 的完整字段清单（``_decorate(get_detail())``）。
   * 静态站的数据为了省体积会把取值为空的字段直接省掉，前端读 ``null`` 与读
   * ``undefined`` 行为一致，但保持与在线版同构更便于对拍，故按这份清单补 ``null``。
   */
  var DETAIL_KEYS = [
    "id", "image", "block_index", "name", "cohort", "cohort_year", "college",
    "major", "destination_org", "city", "province", "position", "degree",
    "position_category", "degree_level", "name_pinyin", "name_initials",
    "confidence", "field_count", "evidence", "raw_text", "notice_id",
    "notice_title", "organization", "release_time", "image_url", "stem",
    "local_path", "notice_type", "alt", "size_bytes", "ocr_engine",
    "ocr_line_count", "ocr_mean_score", "ocr_elapsed", "ocr_error",
    "highlight", "field_labels", "source_text", "image_local_url", "source_url"
  ];

  function apiRecord(id) {
    var key = String(id);
    var record = STATE.byId[key] || STATE.byId[Number(key)];
    if (!record) return null;
    var item = {};
    DETAIL_KEYS.forEach(function (name) { item[name] = null; });
    Object.keys(record).forEach(function (name) { item[name] = record[name]; });
    var extra = STATE.details[key];
    if (extra) Object.keys(extra).forEach(function (name) { item[name] = extra[name]; });
    item.id = record.id;
    item.highlight = buildHighlight(record, []);
    item.field_labels = FIELD_LABELS.map(function (pair) {
      return { key: pair[0], label: pair[1] };
    });
    return item;
  }

  function apiStats(params, bootstrap) {
    var requested = parseInt(params.get("top_n") || "15", 10);
    var topN = isFinite(requested) && requested > 0 ? requested : 15;
    var source = bootstrap.stats;
    var result = {};
    Object.keys(source).forEach(function (key) { result[key] = source[key]; });
    ["province", "college", "city"].forEach(function (key) {
      if (Array.isArray(source[key])) result[key] = source[key].slice(0, topN);
    });
    return result;
  }

  // ------------------------------------------------------------- fetch 接管 //
  function jsonResponse(payload, status) {
    var body = JSON.stringify(payload);
    return {
      ok: status < 400,
      status: status,
      statusText: status < 400 ? "OK" : "Error",
      url: "",
      headers: { get: function () { return "application/json"; } },
      json: function () { return Promise.resolve(JSON.parse(body)); },
      text: function () { return Promise.resolve(body); },
      clone: function () { return jsonResponse(payload, status); }
    };
  }

  function safeDecode(text) {
    try { return decodeURIComponent(text); } catch (err) { return text; }
  }

  function resolveApi(rawUrl) {
    var text = String(rawUrl || "");
    var question = text.indexOf("?");
    var path = question >= 0 ? text.slice(0, question) : text;
    var query = question >= 0 ? text.slice(question + 1) : "";
    var match = /(?:^|\/)api\/([A-Za-z]+)(\/[^?#]*?)?\/?$/.exec(path);
    if (!match) return null;
    return {
      name: match[1].toLowerCase(),
      rest: match[2] ? safeDecode(match[2].replace(/^\//, "")) : "",
      params: new URLSearchParams(query)
    };
  }

  function handleApi(route, bootstrap) {
    switch (route.name) {
      case "config":
        return jsonResponse(bootstrap.config, 200);
      case "suggest":
        return jsonResponse(bootstrap.suggest, 200);
      case "health":
        return jsonResponse(bootstrap.health, 200);
      case "stats":
        return jsonResponse(apiStats(route.params, bootstrap), 200);
      case "facets": {
        // 后端 ``/api/facets`` 直接回 ``facet_counts`` 的原始结构，
        // 没有 ``label`` / ``selected``（那是 ``/api/search`` 才补的）
        var raw = apiSearch(route.params, bootstrap).facets;
        var plain = {};
        Object.keys(raw).forEach(function (dim) {
          plain[dim] = raw[dim].map(function (option) {
            return { value: option.value, count: option.count };
          });
        });
        return jsonResponse({ facets: plain }, 200);
      }
      case "search":
        return jsonResponse(apiSearch(route.params, bootstrap), 200);
      case "record": {
        var item = apiRecord(route.rest);
        if (!item) return jsonResponse({ detail: "记录不存在：" + route.rest }, 404);
        return jsonResponse(item, 200);
      }
      default:
        return null;
    }
  }

  function boot() {
    if (STATE.loading) return STATE.loading;
    STATE.loading = fetchNative(SITE_DIR + "api/bootstrap.json", {
      headers: { Accept: "application/json" }
    })
      .then(function (response) {
        if (!response.ok) throw new Error("数据加载失败（" + response.status + "）");
        return response.json();
      })
      .then(function (bootstrap) {
        STATE.bootstrap = bootstrap;
        STATE.records = bootstrap.records || [];
        STATE.details = bootstrap.details || {};
        STATE.fuzzyIndex = buildFuzzyIndex(bootstrap.fuzzy_groups || []);
        STATE.records.forEach(function (record) {
          STATE.byId[record.id] = record;
          STATE.byId[String(record.id)] = record;
        });
        return bootstrap;
      });
    return STATE.loading;
  }

  global.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (/\/api\/bootstrap\.json(\?|$)/.test(url)) return fetchNative(input, init);
    var route = resolveApi(url);
    if (!route) return fetchNative(input, init);
    return boot().then(function (bootstrap) {
      var response = null;
      try {
        response = handleApi(route, bootstrap);
      } catch (err) {
        return jsonResponse({ detail: String((err && err.message) || err) }, 500);
      }
      return response || fetchNative(input, init);
    });
  };

  global.IRS_STATIC = {
    ready: boot,
    state: STATE,
    parseQuery: parseQuery,
    isLatin: isLatin,
    normalizeLatin: normalizeLatin
  };
})(window);
