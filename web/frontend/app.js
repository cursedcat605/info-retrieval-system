/* =========================================================================
   检索主页面逻辑（需求十 / 十一 / 十二 / 十三）
   - 十：多条件筛选（同维度 OR、跨维度 AND、计数、标签）
   - 十一：排序（默认届别新→旧、姓名拼音）与分页（默认 20/页）
   - 十二：结果高亮（服务端已转义并包 <mark>，前端直接渲染）
   - 十三：卡片视图 / 表格视图切换
   ========================================================================= */
(function () {
  'use strict';
  const IRS = window.IRS;
  const $ = (sel) => document.querySelector(sel);

  const state = {
    q: '', sort: '', page: 1, page_size: 20, view: 'card',
    filters: {},        // {dimension: [value, ...]}
    dimensions: [],     // [{key, label}] 由 /api/config 决定顺序
    collapsed: true,    // 分面组是否折叠（默认展开前几个）
  };
  let dimLabels = {};
  const expandedGroups = {};   // 记录哪些维度点了「展开更多」

  /* ------------------------------------------------------------------ */
  /* 启动                                                                */
  /* ------------------------------------------------------------------ */
  async function boot() {
    try {
      const cfg = await IRS.getJSON('/config');
      state.dimensions = (cfg.dimensions || []).map((d) => d.key);
      dimLabels = Object.fromEntries((cfg.dimensions || []).map((d) => [d.key, d.label]));

      const sortSel = $('#sort');
      sortSel.innerHTML = (cfg.sorts || [])
        .map((s) => `<option value="${IRS.esc(s.value)}">${IRS.esc(s.label)}</option>`)
        .join('');

      const saved = IRS.readState(state.dimensions);
      Object.assign(state, saved);
      if (saved.page_size) $('#page-size').value = String(saved.page_size);
      state.page_size = saved.page_size || 20;
      if (!state.sort) state.sort = state.q ? 'relevance' : 'cohort_desc';
      sortSel.value = state.sort;
      $('#q').value = state.q;

      syncViewSwitch();
      bindEvents();
      await run(1);
      loadSuggestions();
    } catch (err) {
      $('#results').innerHTML = emptyBlock('系统初始化失败', err.message);
      IRS.toast('初始化失败：' + err.message, 4000);
    }
  }

  function bindEvents() {
    // 顶部搜索框旁与左侧筛选面板底部的「搜索」按钮行为一致：
    // 都用 run(1) 回到第一页重新检索（关键词在 run() 里从 #q 读回来）。
    document.querySelectorAll('[data-search]').forEach((btn) => {
      btn.addEventListener('click', () => run(1));
    });
    $('#q').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { closeSuggest(); run(1); }
    });
    $('#q').addEventListener('input', onTypeSuggest);
    $('#q').addEventListener('focus', () => { if (lastSuggest) openSuggest(); });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.searchbar')) closeSuggest();
    });

    $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; run(1); });
    $('#page-size').addEventListener('change', (e) => {
      state.page_size = parseInt(e.target.value, 10);
      run(1);
    });
    $('#viewswitch').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-view]');
      if (!btn) return;
      state.view = btn.dataset.view;
      syncViewSwitch();
      IRS.writeState(state, state.dimensions);
      render(state.lastResult);
    });
    $('#tagbar').addEventListener('click', onTagClick);
    $('#facets').addEventListener('change', onFacetChange);
    $('#facets').addEventListener('click', onFacetClick);
    $('#pager').addEventListener('click', onPagerClick);
  }

  function syncViewSwitch() {
    document.querySelectorAll('#viewswitch button').forEach((b) => {
      b.classList.toggle('active', b.dataset.view === state.view);
    });
  }

  /* ------------------------------------------------------------------ */
  /* 检索主流程                                                          */
  /* ------------------------------------------------------------------ */
  async function run(page) {
    if (page) state.page = page;
    // 搜索框是关键词的唯一真相来源：不读回来就会永远用 boot() 时的空值，
    // 导致无论输入什么都只返回全部记录（q 根本不会出现在请求 URL 里）。
    const qEl = $('#q');
    if (qEl) state.q = qEl.value.trim();
    const payload = IRS.searchParams(state, state.dimensions);
    payload.page = state.page;
    payload.page_size = state.page_size;
    $('#results').classList.add('loading');
    $('#summary').textContent = '检索中…';
    try {
      const data = await IRS.getJSON('/search', payload);
      state.lastResult = data;
      // 后端会把未知/越界页码归一化，回写保持一致
      state.page = data.page;
      state.sort = data.sort;
      $('#sort').value = data.sort;
      IRS.writeState(state, state.dimensions);
      renderFacets(data);
      renderTags(data);
      render(data);
      renderPager(data);
    } catch (err) {
      $('#summary').textContent = '检索失败';
      $('#results').innerHTML = emptyBlock('检索失败', err.message);
      $('#pager').innerHTML = '';
    } finally {
      $('#results').classList.remove('loading');
    }
  }

  /* ------------------------------------------------------------------ */
  /* 分面筛选面板                                                        */
  /* ------------------------------------------------------------------ */
  const FACET_PREVIEW = 8;
  const groupOpen = {};   // {dim: bool} 分面组的展开状态，以 DOM 为准

  function renderFacets(data) {
    // 重建 DOM 会丢掉 <details> 的 open 状态。以前只用「是否有已选条件」决定是否展开，
    // 于是用户点「展开更多」后整组立刻被折叠，看起来像按钮点了没反应。
    // 这里在渲染前把用户当前的展开/收起状态快照下来，下一轮渲染原样还原。
    document.querySelectorAll('#facets details.facet-group').forEach((el) => {
      const dim = el.querySelector('.facet-options')?.dataset.dim;
      if (dim) groupOpen[dim] = el.open;
    });
    const groups = state.dimensions.map((dim) => {
      const options = IRS.sortOptions(data.facets[dim] || []);
      const selectedCount = (state.filters[dim] || []).length;
      const expanded = !!expandedGroups[dim];
      const visible = expanded ? options : options.slice(0, FACET_PREVIEW);
      const rows = visible.map((opt) => facetRow(dim, opt)).join('');
      const more = options.length > FACET_PREVIEW
        ? `<button type="button" class="expand-btn" data-expand="${dim}">${expanded
            ? '收起' : `展开更多（共 ${options.length} 项）`}</button>`
        : '';
      if (!options.length) return '';
      // 首次渲染（groupOpen 里还没有该维度）：有已选条件就自动展开，否则折叠
      const open = groupOpen[dim] !== undefined ? groupOpen[dim] : selectedCount > 0;
      return `
        <details class="facet-group" ${open ? 'open' : ''}>
          <summary>${IRS.esc(dimLabels[dim] || dim)}
            ${selectedCount ? `<span class="badge">已选 ${selectedCount}</span>` : ''}
          </summary>
          <div class="facet-options" data-dim="${dim}">${rows}${more}</div>
        </details>`;
    }).join('');
    $('#facets').innerHTML = groups || emptyBlock('暂无可选条件', '', true);
  }

  function facetRow(dim, opt) {
    const cls = ['facet-option'];
    if (opt.selected) cls.push('selected');
    if (!opt.count) cls.push('zero');
    return `
      <label class="${cls.join(' ')}">
        <input type="checkbox" data-dim="${dim}" value="${IRS.esc(opt.value)}" ${opt.selected ? 'checked' : ''} />
        <span class="name" title="${IRS.esc(opt.value)}">${IRS.esc(opt.label || opt.value)}</span>
        <span class="count">（${IRS.num(opt.count)}）</span>
      </label>`;
  }

  function onFacetChange(e) {
    const box = e.target.closest('input[type=checkbox][data-dim]');
    if (!box) return;
    const dim = box.dataset.dim;
    const value = box.value;
    const list = new Set(state.filters[dim] || []);
    if (box.checked) list.add(value); else list.delete(value);
    if (list.size) state.filters[dim] = Array.from(list); else delete state.filters[dim];
    run(1);
  }

  function onFacetClick(e) {
    const btn = e.target.closest('button[data-expand]');
    if (!btn) return;
    expandedGroups[btn.dataset.expand] = !expandedGroups[btn.dataset.expand];
    renderFacets(state.lastResult);
  }

  /* ------------------------------------------------------------------ */
  /* 已选条件标签（需求十：可移除标签 + 清空全部）                        */
  /* ------------------------------------------------------------------ */
  function renderTags(data) {
    const tags = data.filter_tags || [];
    if (!tags.length) {
      $('#tagbar').innerHTML = '<span style="color:#9aa1ab;font-size:12.5px;">未选择任何筛选条件</span>';
      return;
    }
    $('#tagbar').innerHTML = tags.map((t) => `
      <span class="tag">${IRS.esc(t.display || t.value)}
        <button type="button" data-dim="${IRS.esc(t.dimension)}" data-value="${IRS.esc(t.value)}"
                title="移除该条件" aria-label="移除 ${IRS.esc(t.display || t.value)}">×</button>
      </span>`).join('') + '<button type="button" class="clear-all" id="clear-all">清空全部</button>';
  }

  function onTagClick(e) {
    if (e.target.closest('#clear-all')) {
      state.filters = {};
      run(1);
      return;
    }
    const btn = e.target.closest('button[data-dim]');
    if (!btn) return;
    const dim = btn.dataset.dim;
    const list = (state.filters[dim] || []).filter((v) => v !== btn.dataset.value);
    if (list.length) state.filters[dim] = list; else delete state.filters[dim];
    run(1);
  }

  /* ------------------------------------------------------------------ */
  /* 结果渲染（需求十二 高亮 / 需求十三 卡片·表格）                       */
  /* ------------------------------------------------------------------ */
  function render(data) {
    if (!data) return;
    const items = data.items || [];
    const modeText = { fts: '全文索引', like: '模糊匹配', all: '全部记录', pinyin: '拼音匹配', and: '多词·与', or: '多词·或' }[data.mode] || data.mode;
    $('#summary').innerHTML = items.length
      ? `共找到 <b>${IRS.num(data.total)}</b> 条结果 · 第 ${data.page}/${data.pages} 页 · 匹配方式：${IRS.esc(modeText)}`
      : `未找到匹配结果`;

    if (!items.length) {
      $('#results').innerHTML = emptyBlock('没有匹配的记录',
        '试试减少筛选条件，或换用拼音（如 zhangsan）、去掉部分关键词。');
      return;
    }
    $('#results').innerHTML = state.view === 'table' ? tableView(items) : cardView(items);
  }

  function cardView(items) {
    return '<div class="cards">' + items.map((it) => {
      const snip = it.highlight && it.highlight.snippet;
      const cat = it.position_category ? `<span class="badge-cat">${IRS.esc(it.position_category)}</span>` : '';
      const cohort = IRS.cohort(it);
      return `
        <article class="card">
          <div class="head">
            <span class="name">${IRS.hi(it, 'name')}</span>
            ${cohort ? `<span class="badge-cohort">${IRS.esc(cohort)}</span>` : ''}
            ${cat}
          </div>
          <div class="line"><span class="k">学院专业</span><span class="v">${IRS.hi(it, 'college')} · ${IRS.hi(it, 'major')}</span></div>
          <div class="line"><span class="k">学历</span><span class="v">${IRS.hi(it, 'degree')}</span></div>
          <div class="line"><span class="k">去向</span><span class="v">${IRS.hi(it, 'province')}${(it.city || it.province) ? ' · ' : ''}${IRS.hi(it, 'city')} ${IRS.hi(it, 'destination_org')}</span></div>
          <div class="line"><span class="k">岗位</span><span class="v">${IRS.hi(it, 'position')}</span></div>
          ${snip ? `<div class="snippet">${snip}</div>` : ''}
          <div class="foot">
            <span class="src" title="${IRS.esc(it.notice_title || '')}">来源：${IRS.esc(IRS.dash(it.notice_title))}</span>
            <a class="detail" href="/detail.html?id=${it.id}">查看详情 →</a>
          </div>
        </article>`;
    }).join('') + '</div>';
  }

  function tableView(items) {
    const rows = items.map((it) => `
      <tr>
        <td class="name">${IRS.hi(it, 'name')}</td>
        <td><div class="ellip">${IRS.hi(it, 'college')}</div></td>
        <td><div class="ellip">${IRS.hi(it, 'major')}</div></td>
        <td class="nowrap">${IRS.esc(IRS.cohort(it) || '—')}</td>
        <td class="nowrap">${IRS.hi(it, 'city')}</td>
        <td><div class="ellip">${IRS.hi(it, 'destination_org')}</div></td>
        <td><div class="ellip">${IRS.hi(it, 'position')}</div></td>
        <td class="nowrap"><a href="/detail.html?id=${it.id}">详情</a></td>
      </tr>`).join('');
    return `
      <div class="tablewrap">
        <table class="result">
          <thead><tr>
            <th>姓名</th><th>学院</th><th>专业</th><th>届别</th><th>城市</th><th>单位</th><th>岗位</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function emptyBlock(title, hint, small) {
    return `<div class="empty" style="${small ? 'padding:20px;' : ''}">
      <span class="big">🔍</span><div style="font-weight:600;color:#374151;">${IRS.esc(title)}</div>
      ${hint ? `<div style="margin-top:6px;font-size:12.5px;">${IRS.esc(hint)}</div>` : ''}
    </div>`;
  }

  /* ------------------------------------------------------------------ */
  /* 分页（需求 11.3：< 上一页 1 2 3 4 5 下一页 >）                        */
  /* ------------------------------------------------------------------ */
  function renderPager(data) {
    if (!data || !data.pages) { $('#pager').innerHTML = ''; return; }
    const cur = data.page;
    const total = data.pages;
    const windowSize = 5;
    let start = Math.max(1, cur - Math.floor(windowSize / 2));
    let end = Math.min(total, start + windowSize - 1);
    start = Math.max(1, end - windowSize + 1);

    const parts = [];
    parts.push(`<button data-page="${cur - 1}" ${data.has_prev ? '' : 'disabled'}>‹ 上一页</button>`);
    if (start > 1) {
      parts.push(`<button data-page="1">1</button>`);
      if (start > 2) parts.push('<span class="gap">…</span>');
    }
    for (let p = start; p <= end; p++) {
      parts.push(`<button data-page="${p}" class="${p === cur ? 'active' : ''}">${p}</button>`);
    }
    if (end < total) {
      if (end < total - 1) parts.push('<span class="gap">…</span>');
      parts.push(`<button data-page="${total}">${total}</button>`);
    }
    parts.push(`<button data-page="${cur + 1}" ${data.has_next ? '' : 'disabled'}>下一页 ›</button>`);
    parts.push(`<span class="jump">跳至 <input id="jump" type="number" min="1" max="${total}" value="${cur}" /> 页</span>`);
    $('#pager').innerHTML = parts.join('');

    const jump = $('#jump');
    jump.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const p = Math.min(total, Math.max(1, parseInt(e.target.value, 10) || 1));
      run(p);
    });
  }

  function onPagerClick(e) {
    const btn = e.target.closest('button[data-page]');
    if (!btn || btn.disabled) return;
    run(parseInt(btn.dataset.page, 10));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* ------------------------------------------------------------------ */
  /* 搜索框自动补全                                                      */
  /* ------------------------------------------------------------------ */
  const KIND_LABEL = { colleges: '学院', majors: '专业', provinces: '省份', cities: '城市' };
  let suggestData = null;
  let lastSuggest = null;
  let suggestIndex = -1;

  async function loadSuggestions() {
    try {
      suggestData = await IRS.getJSON('/suggest');
    } catch (_) { /* 补全失败不影响检索 */ }
  }

  const onTypeSuggest = IRS.debounce(() => {
    if (!suggestData) { loadSuggestions(); return; }
    const kw = $('#q').value.trim().toLowerCase();
    if (kw.length < 1) { closeSuggest(); return; }
    const flat = [];
    Object.keys(KIND_LABEL).forEach((kind) => {
      (suggestData[kind] || []).forEach((value) => {
        if (String(value).toLowerCase().includes(kw)) flat.push({ value: value, kind: KIND_LABEL[kind] });
      });
    });
    const seen = new Set();
    const picked = [];
    flat.forEach((f) => {
      const key = f.kind + '|' + f.value;
      if (seen.has(key)) return;
      seen.add(key);
      if (picked.length < 12) picked.push(f);
    });
    if (!picked.length) { closeSuggest(); return; }
    lastSuggest = picked;
    suggestIndex = -1;
    $('#suggest').innerHTML =
      `<div class="group-title">建议关键词（点击填入并搜索）</div>` +
      picked.map((f) => `<div class="item" data-value="${IRS.esc(f.value)}">
          <span>${IRS.esc(f.value)}</span><span class="kind">${f.kind}</span></div>`).join('');
    $('#suggest').querySelectorAll('.item').forEach((el) => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        commitSuggest(el.dataset.value);
      });
    });
    openSuggest();
  }, 120);

  function commitSuggest(value) {
    $('#q').value = value;
    closeSuggest();
    run(1);
  }

  function openSuggest() { $('#suggest').classList.add('open'); }
  function closeSuggest() { $('#suggest').classList.remove('open'); }

  document.addEventListener('DOMContentLoaded', boot);
})();
