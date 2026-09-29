/* =========================================================================
   信息详情页面（需求十四）
   字段区（基本信息 / 选调生信息 / 来源信息）+ 原始图片
   另附：识别证据（LLM/规则抽取的原文出处）
   ========================================================================= */
(function () {
  'use strict';
  const IRS = window.IRS;
  const $ = (sel) => document.querySelector(sel);

  /** 由届别与学历推算「参考年级」（本科 4 年 / 硕士 3 年 / 博士 4 年） */
  function inferGrade(item) {
    const y = item.cohort_year;
    if (!y) return '';
    const offset = item.degree_level === '硕士' ? 3 : 4;
    return (y - offset) + '级';
  }

  function row(label, value, opts) {
    const o = opts || {};
    const v = IRS.dash(value);
    const empty = v === '—' ? ' emptyv' : '';
    const html = o.link
      ? `<a href="${IRS.esc(o.link)}" ${o.external ? 'target="_blank" rel="noopener"' : ''}>${IRS.esc(v)} ↗</a>`
      : IRS.esc(v);
    return `<dt>${IRS.esc(label)}</dt><dd class="${empty}">${html}</dd>`;
  }

  async function load(id) {
    let item;
    try {
      item = await IRS.getJSON('/record/' + encodeURIComponent(id));
    } catch (err) {
      $('#detail-sub').textContent = '加载失败：' + err.message;
      $('#detail-main').hidden = false;
      return;
    }

    const name = IRS.dash(item.name);
    const cohort = IRS.cohort(item);
    document.title = `${name} · 记录详情`;

    /* -------- 顶部 -------- */
    $('#detail-title').innerHTML =
      `${IRS.esc(name)}${cohort ? ` <span class="badge-cohort" style="font-size:13px;vertical-align:2px;">${IRS.esc(cohort)}</span>` : ''}`;
    $('#detail-sub').textContent =
      [item.college, item.major, item.degree, item.position_category, IRS.region(item)]
        .filter((x) => x && String(x).trim()).join(' · ') || '（结构化字段较少，请参考下方原文与证据）';

    $('#btn-back').addEventListener('click', () => {
      if (history.length > 1) history.back();
      else location.href = '/';
    });
    const srcBtn = $('#btn-source');
    srcBtn.href = item.source_url || '#';
    if (!item.source_url) { srcBtn.style.display = 'none'; }
    const imgBtn = $('#btn-image');
    imgBtn.href = item.image_local_url || item.image_url || '#';

    /* -------- 字段区 -------- */
    $('#col-basic').innerHTML = [
      row('姓名', item.name),
      row('学院', item.college),
      row('专业', item.major),
      row('学历', item.degree || item.degree_level),
      row('届别', cohort || item.cohort),
      row('年级', inferGrade(item)),
    ].join('') + `<dt></dt><dd style="font-size:12px;color:#9aa1ab;">年级由「届别 − 学制」推算，仅供参考</dd>`;

    $('#col-selects').innerHTML = [
      row('省份', item.province),
      row('城市', item.city),
      row('单位', item.destination_org),
      row('岗位', item.position),
      row('岗位类别', item.position_category),
    ].join('');

    $('#col-source').innerHTML = [
      row('原文链接', item.source_url ? '打开门户原文' : '', { link: item.source_url, external: true }),
      row('发布时间', item.release_time),
      row('数据来源', item.organization),
      row('通知标题', item.notice_title),
      row('通知编号', item.notice_id),
      row('信息类型', item.notice_type),
    ].join('');

    /* -------- 原图（详情页只展示可选中的证据）-------- */
    const imgUrl = item.image_local_url || item.image_url || '';
    const thumb = $('#image-thumb');
    if (imgUrl) {
      thumb.src = imgUrl;
      // 图片本身也是链接：点一下就在新窗口看原图（此前这里一直是 "#"，点了没反应）
      $('#image-link').href = imgUrl;
      thumb.onerror = () => {
        if (item.image_url && thumb.src !== item.image_url) thumb.src = item.image_url;
        else { $('#image-note').textContent = '本地缓存与门户图片均不可访问。'; }
      };
      $('#image-note').textContent = item.ocr_error
        ? `该图 OCR 识别异常：${item.ocr_error}`
        : '点击图片可在新窗口查看原图。';
    } else {
      thumb.style.display = 'none';
      $('#image-link').removeAttribute('href');
      $('#image-note').textContent = '该记录未关联图片。';
    }

    /* -------- 证据 -------- */
    $('#evidence').textContent = item.evidence || '（原始抽取过程未留存证据片段）';
    $('#evidence-note').textContent =
      `字段命中数：${item.field_count != null ? item.field_count : '—'}｜`
      + `抽取置信度：${item.confidence != null ? Number(item.confidence).toFixed(3) : '—'}｜`
      + `记录 ID：${item.id}｜文本块序号：${item.block_index != null ? item.block_index : '—'}`;

    $('#detail-main').hidden = false;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const id = new URLSearchParams(location.search).get('id');
    if (!id) {
      $('#detail-sub').textContent = '缺少参数 id，例如 /detail.html?id=1';
      return;
    }
    load(id);
  });
})();
