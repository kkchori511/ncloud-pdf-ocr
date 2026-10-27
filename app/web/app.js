(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  let api = null;
  let cfg = {};
  let pdf = null; // {path, name, pages}
  let students = [];
  let sortMode = 'scan';
  let busy = false;

  // ---------- 유틸 ----------
  function isHangul(c) {
    return (c >= 0xac00 && c <= 0xd7a3) || (c >= 0x1100 && c <= 0x11ff) ||
      (c >= 0x3130 && c <= 0x318f) || (c >= 0xa960 && c <= 0xa97f) || (c >= 0xd7b0 && c <= 0xd7ff);
  }
  // 한글 3바이트, 그 밖 1바이트 (app/textutil.py와 같은 규칙)
  function countBytes(text) {
    let n = 0;
    for (const ch of text || '') n += isHangul(ch.codePointAt(0)) ? 3 : 1;
    return n;
  }
  function fmtCredit(v) {
    if (v === null || v === undefined || isNaN(v)) return '-';
    if (v === 0) return '0';
    return Math.abs(v) < 0.01 ? v.toFixed(5) : v.toFixed(4);
  }
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v === true ? '' : v);
    });
    (children || []).forEach((c) => c && e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return e;
  }
  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  // ---------- 모달 ----------
  function modal(message, withCancel) {
    return new Promise((resolve) => {
      $('modalMsg').textContent = message;
      $('modalCancel').hidden = !withCancel;
      $('modal').hidden = false;
      const done = (v) => {
        $('modal').hidden = true;
        $('modalOk').onclick = $('modalCancel').onclick = null;
        resolve(v);
      };
      $('modalOk').onclick = () => done(true);
      $('modalCancel').onclick = () => done(false);
    });
  }
  const alertBox = (m) => modal(m, false);
  const confirmBox = (m) => modal(m, true);

  // ---------- 상태 표시 ----------
  function setStatus(kind, text) {
    const s = $('status');
    s.hidden = !text;
    s.className = 'status' + (kind === 'error' ? ' error' : '');
    s.innerHTML = '';
    if (kind === 'progress') s.appendChild(el('span', { class: 'spinner' }));
    s.appendChild(document.createTextNode(text || ''));
  }
  function setBusy(v) {
    busy = v;
    ['stage1Btn', 'transformBtn', 'pickPdfBtn'].forEach((id) => { $(id).disabled = v; });
    document.querySelectorAll('.rerun').forEach((b) => { b.disabled = v; });
  }
  function setCredits(cost, balance) {
    if (cost !== undefined) $('runCost').textContent = fmtCredit(cost);
    if (balance !== undefined) $('balance').textContent = fmtCredit(balance);
  }
  async function refreshBalance() {
    if (!cfg.openrouter_key) { setCredits(undefined, null); return; }
    const r = await api.balance();
    setCredits(undefined, r.balance);
  }

  // ---------- 설정 ----------
  let pending = {};
  const flushCfg = debounce(async () => {
    const p = pending;
    pending = {};
    await api.save_config(p);
    $('savedNote').textContent = '저장됨';
    setTimeout(() => { $('savedNote').textContent = '입력하면 자동 저장됩니다.'; }, 1200);
  }, 400);
  function updateCfg(patch) {
    Object.assign(cfg, patch);
    Object.assign(pending, patch);
    flushCfg();
  }

  function fillSettings() {
    document.querySelectorAll('[data-cfg]').forEach((input) => {
      input.value = cfg[input.dataset.cfg] || '';
    });
    $('ocrLang').value = cfg.ocr_lang || 'ko';
    $('cfgPath').textContent = cfg.config_path || '';
    $('refineA').value = (cfg.refine_prompts || {}).A || '';
    $('refineB').value = (cfg.refine_prompts || {}).B || '';
    document.querySelectorAll('input[name="refineMode"]').forEach((r) => { r.checked = r.value === cfg.refine_mode; });
    $('perStudent').value = cfg.pages_per_student || 1;
    $('targetBytes').value = cfg.target_bytes ?? 1500;
    document.querySelectorAll('.model-row').forEach((row) => showPrice(row));
    renderPromptTabs();
  }

  async function showPrice(row) {
    const input = row.querySelector('.model-input');
    const price = row.querySelector('.price');
    const id = input.value.trim();
    price.classList.remove('error');
    if (!id) { price.textContent = '모델을 입력해 주세요.'; return; }
    price.textContent = '단가 확인 중…';
    const r = await api.model_info(id);
    if (!r.ok) {
      price.classList.add('error');
      price.textContent = r.error;
      return;
    }
    if (input.value.trim() !== r.id) {
      input.value = r.id;
      updateCfg({ [row.dataset.model]: r.id });
    }
    const p = (v) => (v === null || v === undefined ? '?' : v.toFixed(v < 1 ? 3 : 2));
    price.textContent = `${r.name} · 입력 ${p(r.prompt_per_m)} / 출력 ${p(r.completion_per_m)} 크레딧 (100만 토큰당)`;
  }

  function bindSettings() {
    document.querySelectorAll('[data-cfg]').forEach((input) => {
      input.addEventListener('input', () => updateCfg({ [input.dataset.cfg]: input.value.trim() }));
    });
    $('orKey').addEventListener('change', refreshBalance);
    document.querySelectorAll('.model-row').forEach((row) => {
      const input = row.querySelector('.model-input');
      input.addEventListener('change', () => showPrice(row));
      row.querySelector('.paste-btn').addEventListener('click', async () => {
        const r = await api.read_clipboard();
        if (!r.ok || !r.text) { alertBox('클립보드에 붙여넣을 내용이 없습니다.'); return; }
        input.value = r.text;
        updateCfg({ [row.dataset.model]: r.text });
        showPrice(row);
      });
    });
    document.querySelectorAll('.pw-toggle').forEach((b) => {
      b.addEventListener('click', () => {
        const t = $(b.dataset.target);
        const show = t.type === 'password';
        t.type = show ? 'text' : 'password';
        b.textContent = show ? '숨기기' : '표시';
      });
    });
    const saveRefine = () => updateCfg({ refine_prompts: { A: $('refineA').value, B: $('refineB').value } });
    $('refineA').addEventListener('input', saveRefine);
    $('refineB').addEventListener('input', saveRefine);
    $('resetRefineBtn').addEventListener('click', async () => {
      if (!(await confirmBox('정제 프롬프트 A, B를 기본값으로 되돌릴까요?'))) return;
      await api.save_config({ refine_prompts: null });
      cfg = await api.load_config();
      fillSettings();
    });
    document.querySelectorAll('input[name="refineMode"]').forEach((r) => {
      r.addEventListener('change', () => updateCfg({ refine_mode: r.value }));
    });
    $('resetKeysBtn').addEventListener('click', async () => {
      if (!(await confirmBox('저장된 키 3개(CLOVA URL, CLOVA Secret Key, OpenRouter 키)를 모두 지울까요?\n프롬프트와 모델 설정은 남습니다.'))) return;
      await api.reset_keys();
      cfg = await api.load_config();
      fillSettings();
      setCredits(undefined, null);
      alertBox('키를 지웠습니다.');
    });
  }

  // ---------- 프롬프트 탭 ----------
  function renderPromptTabs() {
    const bar = $('promptTabs');
    bar.innerHTML = '';
    const tabs = cfg.transform_tabs || [];
    tabs.forEach((t, i) => {
      bar.appendChild(el('button', {
        type: 'button',
        class: 'tab-btn' + (i === cfg.active_tab ? ' active' : ''),
        text: t.name || `프롬프트 ${i + 1}`,
        onclick: () => { updateCfg({ active_tab: i }); renderPromptTabs(); },
      }));
    });
    const cur = tabs[cfg.active_tab] || { name: '', prompt: '' };
    $('tabName').value = cur.name;
    $('tabPrompt').value = cur.prompt;
  }
  function bindPromptTabs() {
    const save = () => {
      const tabs = cfg.transform_tabs.map((t) => Object.assign({}, t));
      tabs[cfg.active_tab] = { name: $('tabName').value, prompt: $('tabPrompt').value };
      updateCfg({ transform_tabs: tabs });
      const btn = $('promptTabs').children[cfg.active_tab];
      if (btn) btn.textContent = $('tabName').value || `프롬프트 ${cfg.active_tab + 1}`;
    };
    $('tabName').addEventListener('input', save);
    $('tabPrompt').addEventListener('input', save);
    $('targetBytes').addEventListener('input', () => {
      updateCfg({ target_bytes: parseInt($('targetBytes').value, 10) || 0 });
      document.querySelectorAll('.student').forEach((card) => updateBytes(card));
    });
  }

  // ---------- 1단계 ----------
  function checkPages() {
    const per = parseInt($('perStudent').value, 10) || 1;
    const w = $('pageWarn');
    if (pdf && pdf.pages % per !== 0) {
      w.hidden = false;
      w.textContent = `전체 ${pdf.pages}페이지가 ${per}페이지씩 나누어떨어지지 않습니다. 마지막 학생은 ${pdf.pages % per}페이지만 들어갑니다.`;
    } else {
      w.hidden = true;
    }
  }
  function bindStage1() {
    $('pickPdfBtn').addEventListener('click', async () => {
      const r = await api.pick_pdf();
      if (r.error) { alertBox(r.error); return; }
      if (!r.ok) return;
      pdf = r;
      $('pdfName').textContent = `${r.name} (${r.pages}페이지)`;
      $('pdfName').classList.remove('muted');
      checkPages();
    });
    $('perStudent').addEventListener('input', () => {
      updateCfg({ pages_per_student: Math.max(1, parseInt($('perStudent').value, 10) || 1) });
      checkPages();
    });
    $('stage1Btn').addEventListener('click', async () => {
      if (!pdf) { alertBox('먼저 PDF를 선택해 주세요.'); return; }
      if (students.length && !(await confirmBox('새로 시작하면 지금 2단계 목록과 변환 결과가 모두 사라집니다. 계속할까요?'))) return;
      const per = Math.max(1, parseInt($('perStudent').value, 10) || 1);
      const r = await api.start_stage1(pdf.path, per);
      if (!r.ok) { setStatus('error', r.error); return; }
      setBusy(true);
      setStatus('progress', 'OCR 준비 중…');
    });
  }

  // ---------- 2단계: 목록 ----------
  function byIndex(i) { return students.find((s) => s.index === i); }

  function sortedStudents() {
    const list = students.slice();
    const cmpEmptyLast = (a, b, f) => {
      if (!a && !b) return 0;
      if (!a) return 1;
      if (!b) return -1;
      return f(a, b);
    };
    if (sortMode === 'id') {
      list.sort((a, b) => cmpEmptyLast(a.student_id, b.student_id, (x, y) => x.localeCompare(y, 'ko', { numeric: true })) || a.index - b.index);
    } else if (sortMode === 'name') {
      list.sort((a, b) => cmpEmptyLast(a.name, b.name, (x, y) => x.localeCompare(y, 'ko')) || a.index - b.index);
    } else {
      list.sort((a, b) => a.index - b.index);
    }
    return list;
  }

  function bytesLabel(text) {
    const n = countBytes(text);
    const target = cfg.target_bytes || 0;
    if (!target) return { text: `${n.toLocaleString()} 바이트`, off: false };
    const off = Math.abs(n - target) > target * 0.1;
    return { text: `${n.toLocaleString()} / ${target.toLocaleString()} 바이트`, off };
  }
  function updateBytes(card) {
    card.querySelectorAll('[data-bytes-for]').forEach((b) => {
      const ta = card.querySelector(`textarea[data-role="${b.dataset.bytesFor}"]`);
      if (!ta) return;
      const isOriginal = b.dataset.bytesFor === 'original';
      const lb = isOriginal ? { text: `${countBytes(ta.value).toLocaleString()} 바이트`, off: false } : bytesLabel(ta.value);
      b.textContent = lb.text;
      b.classList.toggle('off', lb.off);
    });
  }

  function renderCard(s) {
    const sid = el('input', { type: 'text', class: 'sid' + (s.student_id ? '' : ' missing'), placeholder: '학번', value: s.student_id });
    sid.addEventListener('input', () => { s.student_id = sid.value.trim(); sid.classList.toggle('missing', !s.student_id); });
    const name = el('input', { type: 'text', class: 'sname' + (s.name ? '' : ' missing'), placeholder: '이름', value: s.name });
    name.addEventListener('input', () => { s.name = name.value.trim(); name.classList.toggle('missing', !s.name); });
    const check = el('input', { type: 'checkbox' });
    check.checked = s.checked;
    check.addEventListener('change', () => { s.checked = check.checked; });

    const head = el('div', { class: 'student-head' }, [
      check,
      el('span', { class: 'seq', text: `${s.index + 1}번째 (p.${s.pages})` }),
      sid,
      name,
      s.error ? el('span', { class: 'err', text: '정제 실패: ' + s.error }) : null,
      s.rerr ? el('span', { class: 'err', text: '변환 실패: ' + s.rerr }) : null,
      el('button', { type: 'button', class: 'rerun', text: s.busy ? '변환 중…' : '이 학생 다시 실행', disabled: busy, onclick: () => runTransform([s.index]) }),
    ]);

    const original = el('textarea', { 'data-role': 'original' });
    original.value = s.original;
    const origCol = el('div', { class: 'col' }, [
      el('div', { class: 'col-title' }, ['원본', el('span', { class: 'bytes', 'data-bytes-for': 'original' })]),
      original,
    ]);

    const resultCol = (i) => {
      const r = s.results[i];
      const cls = 'col' + (s.picked === i ? ' picked' : '');
      if (!r) {
        return el('div', { class: cls }, [
          el('div', { class: 'col-title', text: `${i + 1}열` }),
          el('div', { class: 'empty', text: s.busy && (i === 0 || s.results[0]) ? '변환 중…' : '아직 결과가 없습니다' }),
        ]);
      }
      const ta = el('textarea', { 'data-role': 'r' + i });
      ta.value = r.text;
      ta.addEventListener('input', () => { r.text = ta.value; updateBytes(card); });
      return el('div', { class: cls }, [
        el('div', { class: 'col-title' }, [
          `${i + 1}열`,
          el('span', { class: 'bytes', 'data-bytes-for': 'r' + i }),
        ]),
        ta,
        el('button', {
          type: 'button',
          class: 'pick-btn',
          text: s.picked === i ? `${i + 1}열 선택됨` : `${i + 1}열 선택`,
          onclick: () => { s.picked = s.picked === i ? null : i; rerenderCard(s); },
        }),
      ]);
    };

    const card = el('div', { class: 'student' + (s.busy ? ' busy' : ''), 'data-index': s.index }, [
      head,
      el('div', { class: 'student-body' }, [origCol, resultCol(0), resultCol(1)]),
    ]);
    original.addEventListener('input', () => { s.original = original.value; updateBytes(card); });
    updateBytes(card);
    return card;
  }

  function rerenderCard(s) {
    const old = document.querySelector(`.student[data-index="${s.index}"]`);
    if (old) old.replaceWith(renderCard(s));
  }
  function renderStudents() {
    const box = $('studentList');
    box.innerHTML = '';
    sortedStudents().forEach((s) => box.appendChild(renderCard(s)));
    $('stage2').hidden = students.length === 0;
  }

  // ---------- 2단계: 변환 ----------
  async function runTransform(indices) {
    if (busy) return;
    const targets = indices.map(byIndex).filter(Boolean);
    if (!targets.length) { alertBox('변환할 학생을 체크해 주세요.'); return; }
    const empty = targets.filter((s) => !s.original.trim());
    if (empty.length === targets.length) { alertBox('원본이 비어 있어 변환할 수 없습니다.'); return; }
    const willDrop = targets.filter((s) => s.original.trim() && s.results[0] && s.results[1]).length;
    if (willDrop && !(await confirmBox(`${willDrop}명의 1열 결과가 삭제됩니다.\n(2열 결과가 1열로 옮겨지고, 새 결과가 2열에 들어갑니다.)\n계속할까요?`))) return;

    const tab = (cfg.transform_tabs || [])[cfg.active_tab] || {};
    const items = targets.filter((s) => s.original.trim()).map((s) => ({ index: s.index, original: s.original }));
    const r = await api.start_transform(items, tab.prompt || '', cfg.target_bytes || 0);
    if (!r.ok) { alertBox(r.error); return; }
    setBusy(true);
    items.forEach((it) => { const s = byIndex(it.index); s.busy = true; s.rerr = ''; rerenderCard(s); });
    setStatus('progress', `변환 중… 0/${items.length}명`);
  }

  function applyResult(ev) {
    const s = byIndex(ev.index);
    if (!s) return;
    s.busy = false;
    if (ev.error) {
      s.rerr = ev.error;
    } else if (!s.results[0]) {
      s.results[0] = { text: ev.text };
    } else if (!s.results[1]) {
      s.results[1] = { text: ev.text };
    } else {
      // 2열 → 1열, 기존 1열 삭제, 새 결과 → 2열. 선택은 내용을 따라간다.
      s.results[0] = s.results[1];
      s.results[1] = { text: ev.text };
      s.picked = s.picked === 1 ? 0 : null;
    }
    rerenderCard(s);
  }

  function bindStage2() {
    document.querySelectorAll('.sort-btn').forEach((b) => {
      b.addEventListener('click', () => {
        sortMode = b.dataset.sort;
        document.querySelectorAll('.sort-btn').forEach((x) => x.classList.toggle('active', x === b));
        renderStudents();
      });
    });
    $('checkAllBtn').addEventListener('click', () => { students.forEach((s) => { s.checked = true; }); renderStudents(); });
    $('uncheckAllBtn').addEventListener('click', () => { students.forEach((s) => { s.checked = false; }); renderStudents(); });
    $('transformBtn').addEventListener('click', () => runTransform(sortedStudents().filter((s) => s.checked).map((s) => s.index)));
    $('pickAll1Btn').addEventListener('click', () => { students.forEach((s) => { if (s.results[0]) s.picked = 0; }); renderStudents(); });
    $('pickAll2Btn').addEventListener('click', () => { students.forEach((s) => { if (s.results[1]) s.picked = 1; }); renderStudents(); });
    $('pickNoneBtn').addEventListener('click', () => { students.forEach((s) => { s.picked = null; }); renderStudents(); });
    $('saveBtn').addEventListener('click', () => save(true));
    $('saveRawBtn').addEventListener('click', () => save(false));
  }

  async function save(includeTransform) {
    if (!students.length) return;
    const list = sortedStudents();
    if (includeTransform) {
      const unpicked = list.filter((s) => s.picked === null || !s.results[s.picked]).length;
      if (unpicked && !(await confirmBox(`변환 결과를 선택하지 않은 학생이 ${unpicked}명 있습니다.\n이 학생들은 변환(D열)이 빈칸으로 저장됩니다. 계속할까요?`))) return;
    }
    const rows = list.map((s) => ({
      student_id: s.student_id,
      name: s.name,
      original: s.original,
      transformed: s.picked !== null && s.results[s.picked] ? s.results[s.picked].text : '',
    }));
    const base = pdf ? pdf.name.replace(/\.[^.]+$/, '') : '악필교정기';
    const r = await api.save_xlsx(rows, includeTransform, `${base}_${includeTransform ? '결과' : '원본'}.xlsx`);
    if (r.ok) alertBox('저장했습니다.\n' + r.path);
    else if (!r.cancelled) alertBox(r.error || '저장하지 못했습니다.');
  }

  // ---------- Python → 화면 이벤트 ----------
  window.onAppEvent = function (ev) {
    switch (ev.type) {
      case 'progress':
        setStatus('progress', ev.message);
        break;
      case 'error':
        setStatus('error', '오류: ' + ev.message);
        students.forEach((s) => { if (s.busy) { s.busy = false; rerenderCard(s); } });
        break;
      case 'stage1_done': {
        students = ev.students.map((s) => Object.assign(s, { results: [null, null], picked: null, checked: true, busy: false, rerr: '' }));
        sortMode = 'scan';
        document.querySelectorAll('.sort-btn').forEach((x) => x.classList.toggle('active', x.dataset.sort === 'scan'));
        renderStudents();
        const failed = students.filter((s) => s.error).length;
        const missing = students.filter((s) => !s.student_id || !s.name).length;
        let msg = `1단계 완료: ${students.length}명`;
        if (failed) msg += ` · 정제 실패 ${failed}명(OCR 원문을 그대로 넣었습니다)`;
        if (missing) msg += ` · 학번/이름 확인 필요 ${missing}명(노란 칸)`;
        setStatus('info', msg);
        setCredits(ev.cost, ev.balance);
        $('stage2').scrollIntoView({ behavior: 'smooth' });
        break;
      }
      case 'transform_result':
        applyResult(ev);
        break;
      case 'transform_done':
        setStatus('info', '변환 완료');
        setCredits(ev.cost, ev.balance);
        break;
      case 'idle':
        setBusy(false);
        document.querySelectorAll('.rerun').forEach((b) => { b.disabled = false; });
        break;
    }
  };

  // ---------- 메뉴·안내 ----------
  function bindMenu() {
    document.querySelectorAll('.menu-btn[data-panel]').forEach((b) => {
      b.addEventListener('click', () => {
        document.querySelectorAll('.menu-btn[data-panel]').forEach((x) => x.classList.toggle('active', x === b));
        document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.dataset.panelContent === b.dataset.panel));
        window.scrollTo(0, 0);
      });
    });
    // 바깥 링크는 기본 브라우저로 연다
    document.addEventListener('click', (e) => {
      const a = e.target.closest('[data-url], a[href^="http"]');
      if (!a) return;
      e.preventDefault();
      api.open_url(a.dataset.url || a.getAttribute('href'));
    });
  }
  async function loadGuide() {
    const html = await api.guide_html();
    const body = $('guideBody');
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    Array.from(tmp.children).reverse().forEach((c) => body.prepend(c));
    const tabs = document.querySelectorAll('.guide-tabs .tab-btn');
    const show = (name) => {
      tabs.forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
      body.querySelectorAll('.tab-content').forEach((c) => c.classList.toggle('active', c.dataset.tabContent === name));
    };
    tabs.forEach((t) => t.addEventListener('click', () => show(t.dataset.tab)));
    show('signup');
  }

  async function init() {
    api = window.pywebview.api;
    const info = await api.app_info();
    $('appVer').textContent = 'v' + info.version;
    $('footVer').textContent = 'v' + info.version;
    $('maker').textContent = info.maker;
    cfg = await api.load_config();
    fillSettings();
    bindMenu();
    bindSettings();
    bindPromptTabs();
    bindStage1();
    bindStage2();
    loadGuide();
    refreshBalance();
    if (!cfg.clova_url || !cfg.clova_secret || !cfg.openrouter_key) {
      document.querySelector('.menu-btn[data-panel="guide"]').click();
    }
  }

  window.addEventListener('pywebviewready', init);
})();
