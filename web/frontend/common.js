/* =========================================================================
   公共工具：接口调用、状态与 URL 同步、字段格式化、Toast
   以全局对象 window.IRS 暴露（前端不引入打包器，保持零构建）。
   ========================================================================= */
(function (global) {
  'use strict';

  const API = '/api';

  /* ----------------------------- HTTP ----------------------------- */
  async function getJSON(path, params) {
    const url = new URL(path.startsWith('http') ? path : API + path, location.origin);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v === undefined || v === null || v === '') continue;
        if (Array.isArray(v)) {
          v.filter((x) => x !== '' && x !== null && x !== undefined).forEach((x) => url.searchParams.append(k, x));
        } else {
          url.searchParams.append(k, v);
        }
      }
    }
    const res = await fetch(url.toString(), { headers: { Accept: 'application/json' } });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* 非 JSON 响应 */ }
      throw new Error(detail);
    }
    return res.json();
  }

  /* --------------------------- 展示格式化 --------------------------- */
  /** 空值统一显示为「—」，避免出现 null/undefined */
  function dash(value) {
    if (value === null || value === undefined) return '—';
    const s = String(value).trim();
    return s === '' ? '—' : s;
  }

  /** 已由后端转义并加 <mark> 的高亮 HTML，直接插入 DOM（服务端已 escape） */
  function hi(item, field) {
    const h = item && item.highlight && item.highlight.fields;
    const v = h && h[field];
    if (v !== undefined && v !== null && v !== '') return v;
    return esc(dash(item ? item[field] : ''));
  }

  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 届别展示：优先 cohort_year，其次 cohort 原始文本 */
  function cohort(item) {
    if (item.cohort_year) return item.cohort_year + '届';
    return item.cohort ? String(item.cohort) : '';
  }

  /** 省/市拼接：去掉重复（如「重庆市 重庆市」）*/
  function region(item) {
    const parts = [];
    [item.province, item.city].forEach((p) => {
      const s = (p || '').trim();
      if (s && !parts.includes(s)) parts.push(s);
    });
    return parts.join(' · ');
  }

  function num(value) {
    return (value === null || value === undefined) ? '0' : Number(value).toLocaleString('zh-CN');
  }

  /* --------------------------- URL ⇄ 状态 --------------------------- */
  const DIMENSIONS = ['province', 'position_category', 'degree_level', 'cohort_year', 'college', 'major', 'city'];

  /** 从 location.search 解析出 {q, sort, page, page_size, filters} */
  function readState(dimensions) {
    const sp = new URLSearchParams(location.search);
    const dims = dimensions || DIMENSIONS;
    const filters = {};
    dims.forEach((d) => {
      const values = sp.getAll(d).filter((v) => v !== '');
      if (values.length) filters[d] = values;
    });
    return {
      q: sp.get('q') || '',
      sort: sp.get('sort') || '',
      page: Math.max(1, parseInt(sp.get('page') || '1', 10) || 1),
      page_size: parseInt(sp.get('page_size') || '0', 10) || 0,
      view: sp.get('view') === 'table' ? 'table' : 'card',
      filters: filters,
    };
  }

  /** 把状态写回地址栏（replace，避免污染历史） */
  function writeState(state, dimensions) {
    const dims = dimensions || DIMENSIONS;
    const sp = new URLSearchParams();
    if (state.q) sp.set('q', state.q);
    if (state.sort && state.sort !== 'relevance') sp.set('sort', state.sort);
    if (state.page > 1) sp.set('page', String(state.page));
    if (state.page_size) sp.set('page_size', String(state.page_size));
    if (state.view === 'table') sp.set('view', 'table');
    dims.forEach((d) => {
      (state.filters[d] || []).forEach((v) => sp.append(d, v));
    });
    const qs = sp.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
  }

  function searchParams(state, dimensions) {
    const dims = dimensions || DIMENSIONS;
    const params = { q: state.q, sort: state.sort, page: state.page };
    if (state.page_size) params.page_size = state.page_size;
    dims.forEach((d) => { if ((state.filters[d] || []).length) params[d] = state.filters[d]; });
    return params;
  }

  /* ----------------------------- 其他 ----------------------------- */
  function toast(message, ms) {
    let el = document.querySelector('.toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'toast';
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), ms || 2200);
  }

  /** 稳定排序：选中项优先，其余按计数倒序 */
  function sortOptions(options) {
    return options.slice().sort((a, b) => {
      if (!!a.selected !== !!b.selected) return a.selected ? -1 : 1;
      if (b.count !== a.count) return b.count - a.count;
      return String(a.label || a.value).localeCompare(String(b.label || b.value), 'zh-Hans-CN');
    });
  }

  function debounce(fn, wait) {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  global.IRS = {
    API, DIMENSIONS,
    getJSON, dash, hi, esc, cohort, region, num,
    readState, writeState, searchParams,
    toast, sortOptions, debounce,
  };
})(window);
