(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  let api = null;
  let cfg = {};
  let pdf = null; // {path, name, pages}
  let students = [];
  let sortMode = 'scan';
  let busy = false;
  // 스캔 묶기: 쪽 요약, 새 학생이 시작되는 쪽, 뺄 쪽
  let scanPages = [];
  let starts = new Set();
  let skipped = new Set();
  let pendingRefine = false;
  let appInfo = {};

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

  // ---------- 확인 창 ----------
  function modal(message, withCancel, html) {
    return new Promise((resolve) => {
      if (html) $('modalMsg').innerHTML = message;
      else $('modalMsg').textContent = message;
      $('modalCancel').hidden = !withCancel;
      $('modal').hidden = false;
      const done = (v) => {
        $('modal').hidden = true;
        $('modal').classList.remove('wide');
        $('modalOk').onclick = $('modalCancel').onclick = null;
        resolve(v);
      };
      $('modalOk').onclick = () => done(true);
      $('modalCancel').onclick = () => done(false);
    });
  }
  const alertBox = (m) => modal(m, false);
  const confirmBox = (m) => modal(m, true);

  // ---------- 진행 창 ----------
  const JOBS = {
    ocr: {
      title: '1단계 OCR 중',
      steps: [
        { key: 'split', label: 'PDF 분할' },
        { key: 'ocr', label: 'OCR', unit: '구간' },
      ],
    },
    refine: { title: '1단계 정제 중', steps: [{ key: 'refine', label: '정제', unit: '명' }] },
    transform: { title: '2단계 변환 중', steps: [{ key: 'transform', label: '변환', unit: '명' }] },
  };
  let progTimer = null;
  let progJob = null;

  // 읽는 방식에 따른 OCR 단계
  function ocrJob(engine) {
    const steps = [];
    if (engine !== 'ai') steps.push({ key: 'split', label: 'PDF 분할' }, { key: 'ocr', label: 'CLOVA', unit: '구간' });
    if (engine !== 'clova') steps.push({ key: 'ai', label: 'Qwen', unit: '쪽' });
    return { title: '1단계 OCR 중', steps };
  }
  function openProgress(job) {
    progJob = typeof job === 'string' ? JOBS[job] : job;
    $('progTitle').textContent = progJob.title;
    const ul = $('progSteps');
    ul.innerHTML = '';
    progJob.steps.forEach((s) => {
      ul.appendChild(el('li', { class: 'step', 'data-step': s.key }, [
        el('span', { class: 'step-icon' }),
        el('span', { class: 'step-label', text: s.label }),
        el('span', { class: 'step-text' }),
        el('div', { class: 'bar' }, [el('div', { class: 'bar-fill' })]),
      ]));
    });
    const start = Date.now();
    const tick = () => {
      const sec = Math.floor((Date.now() - start) / 1000);
      $('progTime').textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
    };
    tick();
    clearInterval(progTimer);
    progTimer = setInterval(tick, 1000);
    $('stopBtn').disabled = false;
    $('stopBtn').textContent = '중지';
    $('progress').hidden = false;
  }
  function closeProgress() {
    clearInterval(progTimer);
    $('progress').hidden = true;
  }
  function updateStep(ev) {
    const li = document.querySelector(`.step[data-step="${ev.key}"]`);
    if (!li) return;
    // 앞 단계는 모두 완료로 표시
    let prev = li.previousElementSibling;
    while (prev) { prev.classList.remove('active'); prev.classList.add('done'); prev = prev.previousElementSibling; }
    li.classList.toggle('active', ev.state === 'active');
    li.classList.toggle('done', ev.state === 'done');
    const def = progJob.steps.find((s) => s.key === ev.key) || {};
    const text = li.querySelector('.step-text');
    const fill = li.querySelector('.bar-fill');
    if (ev.total) {
      text.textContent = `${ev.done}/${ev.total}${def.unit || ''}`;
      fill.style.width = `${Math.round((ev.done / ev.total) * 100)}%`;
    } else {
      text.textContent = ev.text || (ev.state === 'done' ? '완료' : '진행 중…');
      fill.style.width = ev.state === 'done' ? '100%' : '30%';
    }
  }

  // ---------- 상태 표시 ----------
  function setStatus(kind, text) {
    const s = $('status');
    s.hidden = !text;
    s.className = 'status' + (kind === 'error' ? ' error' : '');
    s.textContent = text || '';
  }
  function setBusy(v) {
    busy = v;
    ['stage1Btn', 'transformBtn', 'pickPdfBtn', 'refineBtn', 'rereadBtn', 'regroupBtn'].forEach((id) => { $(id).disabled = v; });
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

  // ---------- 화면 전환 ----------
  function showPanel(name) {
    document.querySelectorAll('.menu-btn[data-panel]').forEach((x) => x.classList.toggle('active', x.dataset.panel === name));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.dataset.panelContent === name));
    window.scrollTo(0, 0);
    if (name === 'main') requestAnimationFrame(sizePromptAreas);
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
    $('layoutMode').value = cfg.layout_mode || 'auto';
    $('readSide').value = cfg.read_side || 'both';
    document.querySelectorAll('input[name="ocrEngine"]').forEach((r) => { r.checked = r.value === (cfg.ocr_engine || 'clova'); });
    $('maskPct').value = Math.round((cfg.ocr_mask ?? 0.12) * 100);
    $('ocrAiConsent').checked = !!cfg.ocr_ai_consent;
    showMaskBox();
    $('skipGroupCheck').checked = !!cfg.skip_group_check;
    $('targetBytes').value = cfg.target_bytes ?? 1500;
    document.querySelectorAll('.model-row').forEach((row) => showPrice(row));
    renderPromptTabs();
  }

  async function showPrice(row) {
    const input = row.querySelector('.model-input');
    const main = row.querySelector('.price-main');
    const sub = row.querySelector('.price-sub');
    const id = input.value.trim();
    row.querySelector('.price').classList.remove('error');
    sub.textContent = '';
    if (!id) { main.textContent = '모델을 입력해 주세요'; return; }
    main.textContent = '단가 확인 중…';
    const r = await api.model_info(id);
    if (!r.ok) {
      row.querySelector('.price').classList.add('error');
      main.textContent = '모델을 찾지 못했습니다';
      sub.textContent = r.error;
      return;
    }
    if (input.value.trim() !== r.id) {
      input.value = r.id;
      updateCfg({ [row.dataset.model]: r.id });
    }
    const p = (v) => (v === null || v === undefined ? '?' : v.toFixed(v < 1 ? 3 : 2));
    main.textContent = `입력 ${p(r.prompt_per_m)} / 출력 ${p(r.completion_per_m)} 크레딧`;
    sub.textContent = `100만 토큰당 - ${r.name}`;
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
    $('ocrAiConsent').addEventListener('change', async () => {
      if ($('ocrAiConsent').checked && !(await confirmBox('위쪽을 가리지 않은(0%) 그림을 Qwen에 보내면 학번·이름이 담긴 그림이 OpenRouter와 그 제공업체로 전송됩니다. 동의하시나요?'))) {
        $('ocrAiConsent').checked = false;
        return;
      }
      updateCfg({ ocr_ai_consent: $('ocrAiConsent').checked });
    });
    document.querySelectorAll('input[name="refineMode"]').forEach((r) => {
      r.addEventListener('change', () => updateCfg({ refine_mode: r.value }));
    });
    $('resetKeysBtn').addEventListener('click', async () => {
      if (!(await confirmBox('저장된 키 3개(CLOVA Secret Key, CLOVA Invoke URL, OpenRouter 키)를 모두 지울까요?\n프롬프트와 모델 설정은 남습니다.'))) return;
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
    const cur = tabs[cfg.active_tab] || {};
    $('tabName').value = cur.name || '';
    $('tabPrompt').value = cur.prompt || '';
    $('tabExample').value = cur.example || '';
    updateExampleBytes();
  }
  function updateExampleBytes() {
    const v = $('tabExample').value;
    $('exampleBytes').textContent = v ? `${countBytes(v).toLocaleString()}바이트` : '';
  }
  function bindPromptTabs() {
    const save = () => {
      const tabs = cfg.transform_tabs.map((t) => Object.assign({}, t));
      tabs[cfg.active_tab] = { name: $('tabName').value, prompt: $('tabPrompt').value, example: $('tabExample').value };
      updateCfg({ transform_tabs: tabs });
      const btn = $('promptTabs').children[cfg.active_tab];
      if (btn) btn.textContent = $('tabName').value || `프롬프트 ${cfg.active_tab + 1}`;
      updateExampleBytes();
    };
    $('tabName').addEventListener('input', save);
    $('tabPrompt').addEventListener('input', save);
    $('tabExample').addEventListener('input', save);
    $('targetBytes').addEventListener('input', () => {
      updateCfg({ target_bytes: parseInt($('targetBytes').value, 10) || 0 });
      document.querySelectorAll('.student').forEach((card) => updateBytes(card));
    });
    window.addEventListener('resize', debounce(sizePromptAreas, 100));
  }

  // 1단계를 접었을 때 지침 칸이 화면 아래 끝까지 차도록 높이를 맞춘다
  function sizePromptAreas() {
    const ta = $('tabPrompt');
    if ($('stage2').hidden || !ta.offsetParent) return;
    const body = $('stage1Body');
    const extra = body.hidden ? 0 : body.offsetHeight;
    const top = ta.getBoundingClientRect().top + window.scrollY - extra;
    const below = 46; // 칸 아래 안내 문구와 여백
    const h = Math.max(280, window.innerHeight - top - below);
    ta.style.height = h + 'px';
    $('tabExample').style.height = h + 'px';
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
  function setStage1Collapsed(collapsed) {
    $('stage1Body').hidden = collapsed;
    $('stage1Toggle').textContent = collapsed ? '펼치기' : '접기';
    $('stage1').classList.toggle('collapsed', collapsed);
    requestAnimationFrame(sizePromptAreas);
  }
  function engine() { return (document.querySelector('input[name="ocrEngine"]:checked') || {}).value || 'clova'; }
  function maskValue() { return Math.max(0, Math.min(40, parseFloat($('maskPct').value) || 0)) / 100; }
  function showMaskBox() { $('maskBox').hidden = engine() === 'clova'; refreshMaskPreview(); }
  const refreshMaskPreview = debounce(async () => {
    if ($('maskBox').hidden) return;
    if (!pdf) { $('maskPreview').removeAttribute('src'); $('maskPreviewMsg').textContent = 'PDF를 고르면 첫 쪽 미리보기가 나옵니다.'; return; }
    const r = await api.mask_preview(maskValue());
    if (r.ok) { $('maskPreview').src = r.src; $('maskPreviewMsg').textContent = ''; } else $('maskPreviewMsg').textContent = r.error;
  }, 250);

  async function startOcr(onlyMissing) {
    await flushNow();
    const eng = engine();
    const r = await api.start_ocr(onlyMissing, eng, maskValue());
    if (!r.ok) { if (onlyMissing) alertBox(r.error); else setStatus('error', r.error); return false; }
    setBusy(true);
    openProgress(ocrJob(eng));
    return true;
  }

  function bindStage1() {
    $('pickPdfBtn').addEventListener('click', async () => {
      if (scanPages.some((p) => p.read) && !(await confirmBox('다른 PDF를 고르면 지금 OCR 결과와 묶기가 사라집니다. 계속할까요?'))) return;
      const r = await api.pick_pdf();
      if (r.error) { alertBox(r.error); return; }
      if (!r.ok) return;
      pdf = r;
      scanPages = [];
      thumbCache = {};
      $('groupBox').hidden = true;
      $('pdfName').textContent = `${r.name} (${r.pages}페이지)`;
      $('pdfName').classList.remove('muted');
      checkPages();
      refreshMaskPreview();
    });
    $('perStudent').addEventListener('input', () => {
      updateCfg({ pages_per_student: Math.max(1, parseInt($('perStudent').value, 10) || 1) });
      checkPages();
    });
    document.querySelectorAll('input[name="ocrEngine"]').forEach((r) => {
      r.addEventListener('change', () => { updateCfg({ ocr_engine: engine() }); showMaskBox(); });
    });
    $('maskPct').addEventListener('input', () => { updateCfg({ ocr_mask: maskValue() }); refreshMaskPreview(); });
    $('layoutMode').addEventListener('change', async () => {
      const r = await api.set_layout(null, $('layoutMode').value, null);
      if (!r.ok) { alertBox(r.error); $('layoutMode').value = cfg.layout_mode || 'auto'; return; }
      cfg.layout_mode = $('layoutMode').value;
      if (scanPages.length) { scanPages = await api.scan_pages(); renderGroups(); }
    });
    $('readSide').addEventListener('change', async () => {
      const r = await api.set_layout(null, null, $('readSide').value);
      if (!r.ok) { alertBox(r.error); $('readSide').value = cfg.read_side || 'both'; return; }
      cfg.read_side = $('readSide').value;
      if (scanPages.length) { scanPages = await api.scan_pages(); renderGroups(); }
      if (r.ai_reset) alertBox(`읽을 단이 바뀌어 Qwen이 읽은 ${r.ai_reset}쪽을 지웠습니다(보낸 그림 범위가 달라짐).\n[못 읽은 쪽 다시 읽기]를 누르면 바뀐 단만 잘라 다시 보냅니다.`);
    });
    $('skipGroupCheck').addEventListener('change', () => updateCfg({ skip_group_check: $('skipGroupCheck').checked }));
    $('stage1Toggle').addEventListener('click', () => setStage1Collapsed(!$('stage1Body').hidden));
    $('stage1Btn').addEventListener('click', async () => {
      if (!pdf) { alertBox('먼저 PDF를 선택해 주세요.'); return; }
      if (scanPages.some((p) => p.read) && !(await confirmBox('처음부터 다시 읽으면 지금 OCR 결과와 묶기·검토가 사라집니다(CLOVA·Qwen 비용이 다시 듭니다). 계속할까요?'))) return;
      if (students.length && !(await confirmBox('새로 시작하면 지금 2단계 목록과 변환 결과가 모두 사라집니다. 계속할까요?'))) return;
      setStatus('', '');
      if (!(await startOcr(false))) return;
      starts = new Set();
      skipped = new Set();
      scanPages = [];
      $('groupBox').hidden = true;
      pendingRefine = $('skipGroupCheck').checked;
    });
    $('rereadBtn').addEventListener('click', () => startOcr(true));
    $('regroupBtn').addEventListener('click', () => { resetGroups(); renderGroups(); });
    $('refineBtn').addEventListener('click', () => startRefine());
  }

  async function flushNow() {
    if (Object.keys(pending).length) { const p = pending; pending = {}; await api.save_config(p); }
  }

  // ---------- 1단계: 묶기 확인 ----------
  function perStudent() { return Math.max(1, parseInt($('perStudent').value, 10) || 1); }
  function resetGroups() {
    const per = perStudent();
    starts = new Set();
    for (let i = 0; i < scanPages.length; i += per) starts.add(i);
    skipped = new Set();
  }
  // app/scan.py groups_from_starts와 같은 규칙
  function currentGroups() {
    const groups = [];
    let cur = [];
    for (let p = 0; p < scanPages.length; p++) {
      if ((starts.has(p) || p === 0) && cur.length) { groups.push(cur); cur = []; }
      if (!skipped.has(p)) cur.push(p);
    }
    if (cur.length) groups.push(cur);
    return groups;
  }
  // 그룹 칸: 빠진 쪽도 자리를 보이게, 새 학생 시작 기준으로 쪽을 나눈다
  function pageBlocks() {
    const blocks = [];
    let cur = [];
    for (let p = 0; p < scanPages.length; p++) {
      if ((starts.has(p) || p === 0) && cur.length) { blocks.push(cur); cur = []; }
      cur.push(p);
    }
    if (cur.length) blocks.push(cur);
    return blocks;
  }

  let thumbCache = {};
  const thumbObserver = new IntersectionObserver((entries) => {
    entries.forEach(async (e) => {
      if (!e.isIntersecting) return;
      const img = e.target;
      thumbObserver.unobserve(img);
      const page = +img.dataset.page;
      const r = thumbCache[page] || await api.page_image(page, 240);
      if (r.ok) { thumbCache[page] = r; img.src = r.src; } else img.alt = r.error;
    });
  }, { rootMargin: '300px' });

  let groupSeq = 0;
  async function renderGroups() {
    const seq = ++groupSeq;
    const box = $('groupList');
    const blocks = pageBlocks();
    const preview = await api.preview_groups(blocks.map((b) => b.filter((p) => !skipped.has(p))).filter((g) => g.length));
    if (seq !== groupSeq) return; // 더 새로 그리는 중
    box.innerHTML = '';
    let n = 0;
    const unread = scanPages.filter(unreadPage).length;
    const left = scanPages.reduce((n, p) => n + (p.left || 0), 0);
    blocks.forEach((pages) => {
      const kept = pages.filter((p) => !skipped.has(p));
      const info = kept.length ? preview[n++] : null;
      const label = info ? `${n}번째 학생` : '빠진 페이지';
      const idText = info ? [info.student_id ? `학번 ${info.student_id}` : null, info.name ? `이름 ${info.name}` : null].filter(Boolean).join(' · ') : '';
      const head = el('div', { class: 'gblock-head' }, [
        el('b', { text: label }),
        info ? el('span', { class: 'gids' + (info.student_id && info.name ? '' : ' missing'), text: idText || '학번·이름 못 찾음 (정제 때 AI가 찾음)' }) : null,
        info && idText && !(info.student_id && info.name) ? el('span', { class: 'muted small', text: '나머지는 정제 때 AI가 찾음' }) : null,
      ]);
      const thumbs = el('div', { class: 'thumbs' }, pages.map((p) => renderThumb(p)));
      box.appendChild(el('div', { class: 'gblock' + (info ? '' : ' skipped') }, [head, thumbs]));
    });
    const groups = currentGroups();
    $('groupSummary').textContent = `${scanPages.length}페이지 → ${groups.length}명` + (skipped.size ? ` (뺀 페이지 ${skipped.size}쪽)` : '') + (unread ? ` - 못 읽은 쪽 ${unread}쪽` : '') + (left ? ` - 검토할 차이 ${left}곳` : '');
    $('refineBtn').textContent = `정제 시작 (${groups.length}명)`;
    $('rereadBtn').hidden = !unread;
    $('rereadBtn').textContent = `못 읽은 쪽 다시 읽기 (${unread}쪽)`;
  }

  function unreadPage(p) { return !p.read || !!p.fail || !!p.ai_error; }
  function renderThumb(p) {
    const sp = scanPages[p] || {};
    const img = el('img', { class: 'thumb-img', 'data-page': p, alt: `p.${p + 1}` });
    thumbObserver.observe(img);
    const isStart = starts.has(p) || p === 0;
    const badges = [];
    if (!sp.read) badges.push(el('span', { class: 'badge danger', text: '읽지 않음' }));
    else if (sp.fail) badges.push(el('span', { class: 'badge danger', title: sp.fail, text: 'CLOVA 실패' }));
    if (sp.ai_error) badges.push(el('span', { class: 'badge danger', title: sp.ai_error, text: 'Qwen 실패' }));
    if (sp.read && !sp.fail && !sp.ai_error && !sp.chars) badges.push(el('span', { class: 'badge', text: '글자 없음' }));
    if (sp.big) badges.push(el('span', { class: 'badge warn' + (sp.left ? '' : ' done'), text: sp.left ? '크게 다름 - 검토' : '검토 끝' }));
    else if (sp.total) badges.push(el('span', { class: 'badge warn' + (sp.left ? '' : ' done'), text: sp.left ? `검토 남음 ${sp.left}/${sp.total}` : '검토 끝' }));
    if (sp.own) badges.push(el('span', { class: 'badge', title: '이 쪽만 단 나누기·읽을 단을 따로 정함', text: '따로 설정' }));
    if (sp.cols === 2) badges.push(el('span', { class: 'badge info', text: '2단' }));
    if (sp.warn) badges.push(el('span', { class: 'badge danger', text: '많이 기울어짐 - 다시 스캔 권장' }));
    return el('div', { class: 'thumb' + (skipped.has(p) ? ' is-skipped' : '') }, [
      el('button', { type: 'button', class: 'thumb-view', title: '원본과 OCR 글자 보기', onclick: () => openViewer(scanPages.map((x) => x.index), p, '원본 보기') }, [img]),
      el('div', { class: 'thumb-meta' }, [el('b', { text: `p.${p + 1}` }), ...badges]),
      el('div', { class: 'thumb-btns' }, [
        el('button', {
          type: 'button', class: 'mini' + (isStart && p !== 0 ? ' on' : ''), text: '새 학생', disabled: p === 0,
          title: p === 0 ? '첫 페이지는 늘 첫 학생입니다' : '이 페이지부터 다음 학생',
          onclick: () => { if (starts.has(p)) starts.delete(p); else starts.add(p); renderGroups(); },
        }),
        el('button', {
          type: 'button', class: 'mini' + (skipped.has(p) ? ' on' : ''), text: skipped.has(p) ? '빼기 취소' : '빼기',
          onclick: () => { if (skipped.has(p)) skipped.delete(p); else skipped.add(p); renderGroups(); },
        }),
      ]),
    ]);
  }

  async function startRefine(auto) {
    const groups = currentGroups();
    if (!groups.length) { alertBox('정제할 학생이 없습니다.'); return; }
    const unread = groups.flat().filter((p) => !scanPages[p] || unreadPage(scanPages[p])).length;
    if (unread && !(await confirmBox(`읽지 못한 쪽이 ${unread}쪽 있습니다. 읽은 결과만으로 정제할까요?\n([못 읽은 쪽 다시 읽기]로 먼저 읽을 수 있습니다.)`))) return;
    const left = groups.flat().reduce((n, p) => n + ((scanPages[p] || {}).left || 0), 0);
    if (left && !auto && !(await confirmBox(`검토하지 않은 차이가 ${left}곳 있습니다. 그곳은 CLOVA 글로 정제할까요?`))) return;
    if (!auto && students.length && !(await confirmBox('정제하면 지금 2단계 목록과 변환 결과가 모두 사라집니다. 계속할까요?'))) return;
    const r = await api.start_refine(groups);
    if (!r.ok) { alertBox(r.error); return; }
    setBusy(true);
    openProgress('refine');
  }

  // ---------- 원본 보기 ----------
  const viewer = { pages: [], pos: 0, seq: 0, d: null, crops: {} };
  function openViewer(pages, start, title) {
    viewer.pages = pages;
    viewer.pos = Math.max(0, pages.indexOf(start));
    viewer.title = title;
    $('viewer').hidden = false;
    showViewerPage();
  }
  function curPage() { return viewer.pages[viewer.pos]; }
  async function showViewerPage() {
    const seq = ++viewer.seq;
    const p = curPage();
    $('viewerTitle').textContent = `${viewer.title} - p.${p + 1}`;
    $('viewerPos').textContent = `${viewer.pos + 1} / ${viewer.pages.length}`;
    $('viewerPrev').disabled = viewer.pos === 0;
    $('viewerNext').disabled = viewer.pos >= viewer.pages.length - 1;
    $('viewerImg').removeAttribute('src');
    $('viewerOverlay').innerHTML = '';
    $('viewerImgMsg').textContent = '그리는 중…';
    $('viewerText').textContent = '';
    $('viewerReview').innerHTML = '';
    closeEdit();
    const [img, d] = await Promise.all([api.page_image(p, 1000), api.page_detail(p)]);
    if (seq !== viewer.seq) return;
    if (img.ok) { $('viewerImg').src = img.src; $('viewerImgMsg').textContent = ''; } else $('viewerImgMsg').textContent = img.error;
    fillViewer(d);
  }
  // 그림 위에 Qwen에 가린 곳, 단 경계(점선), 빼는 단(회색)을 그린다. 좌표는 CLOVA 그림 기준(w×h), 없으면 비율로.
  function drawOverlay(d) {
    const svg = $('viewerOverlay');
    const W = d.w || 1000;
    const H = d.h || 1414;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const parts = [];
    if (d.side && d.side !== 'both') {
      const cut = d.cut || [W / 2, W / 2];
      const pts = d.side === 'right' ? `0,0 ${cut[0]},0 ${cut[1]},${H} 0,${H}` : `${cut[0]},0 ${W},0 ${W},${H} ${cut[1]},${H}`;
      parts.push(`<polygon points="${pts}" class="ov-drop"/>`);
    }
    if (d.line) parts.push(`<line x1="${d.line[0]}" y1="${d.line[1]}" x2="${d.line[2]}" y2="${d.line[3]}" class="ov-line"/>`);
    else if (d.side && d.side !== 'both') {
      const cut = d.cut || [W / 2, W / 2];
      parts.push(`<line x1="${cut[0]}" y1="0" x2="${cut[1]}" y2="${H}" class="ov-line"/>`);
    }
    if (d.mode !== 'clova' && d.mask > 0) {
      parts.push(`<rect x="0" y="0" width="${W}" height="${H * d.mask}" class="ov-mask"/>`);
      parts.push(`<text x="${W / 2}" y="${(H * d.mask) / 2}" class="ov-mask-text" font-size="${Math.max(14, W / 40)}">Qwen에 가린 곳 (${Math.round(d.mask * 100)}%)</text>`);
    }
    svg.innerHTML = parts.join('');
  }
  function fillViewer(d) {
    viewer.d = d;
    drawOverlay(d);
    $('viewerMode').value = d.cols_own || '';
    $('viewerSide').value = d.side_own || '';
    $('viewerMode').disabled = $('viewerSide').disabled = busy;
    $('viewerBadge').textContent = d.cols === 2 ? '2단으로 나눠 읽음' : '한 단으로 읽음';
    const hints = [];
    if (d.warn) hints.push(`스캔이 약 ${Math.abs(d.deg)}도 기울어 바로잡지 못했습니다. 다시 스캔하면 더 정확합니다.`);
    if (d.clova_fail) hints.push(`CLOVA가 이 쪽을 읽지 못했습니다: ${d.clova_fail}`);
    if (d.ai_error) hints.push(`Qwen이 이 쪽을 읽지 못했습니다: ${d.ai_error}`);
    if (d.mode !== 'clova' && d.ai_read === false) hints.push('Qwen이 아직 읽지 않은 쪽입니다. [못 읽은 쪽 다시 읽기]로 읽습니다.');
    $('viewerHint').textContent = hints.join(' ');
    renderReview(d);
    $('viewerFigsWrap').hidden = !(d.figures && d.figures.length);
    $('viewerFigs').checked = d.figs !== false;
    $('viewerText').textContent = d.text || (d.read ? '(인식된 글자 없음)' : '아직 OCR하지 않은 쪽입니다.');
    $('viewerFinalNote').textContent = d.edited ? '직접 고친 글' : d.cmp ? (d.cmp.big ? '' : '고르지 않은 곳은 CLOVA 글') : '학번·이름 줄은 정제할 때 프로그램이 지웁니다';
    $('viewerEditClear').hidden = !d.edited;
  }
  async function decide(data) {
    const d = await api.decide(curPage(), data);
    if (d.ok === false) { alertBox(d.error); return; }
    fillViewer(d);
    refreshSummaries();
  }
  const refreshSummaries = debounce(async () => {
    if ($('groupBox').hidden) return;
    scanPages = await api.scan_pages();
    renderGroups();
  }, 300);

  // 한 줄 입력 창(차이 직접 고치기)
  function promptBox(message, value) {
    return new Promise((resolve) => {
      $('modalMsg').textContent = '';
      const ta = el('textarea', { rows: 3, class: 'prompt-input' });
      ta.value = value || '';
      $('modalMsg').append(el('div', { text: message }), ta);
      $('modalCancel').hidden = false;
      $('modal').hidden = false;
      setTimeout(() => ta.focus(), 30);
      const done = (v) => {
        $('modal').hidden = true;
        $('modalOk').onclick = $('modalCancel').onclick = null;
        resolve(v);
      };
      $('modalOk').onclick = () => done(ta.value);
      $('modalCancel').onclick = () => done(null);
    });
  }

  function renderReview(d) {
    const box = $('viewerReview');
    box.innerHTML = '';
    if (!d.cmp) return;
    const rv = d.review || {};
    const dec = rv.d || {};
    const sugg = rv.s || {};
    const diffs = d.cmp.diffs.filter((x) => x.kind === 'text');
    if (d.cmp.big) {
      const pick = rv.page;
      box.appendChild(el('div', { class: 'review' }, [
        el('div', { class: 'review-head' }, [
          el('b', { text: '두 결과가 크게 다릅니다' }),
          el('span', { class: 'muted small', text: `(비슷한 정도 ${Math.round(d.cmp.ratio * 100)}%) 쪽 전체로 고르세요. 고르지 않으면 CLOVA 글을 씁니다.` }),
        ]),
        el('div', { class: 'two-col big-pick' }, [['clova', 'CLOVA', d.clova], ['ai', 'Qwen', d.ai]].map(([k, label, text]) => el('div', { class: 'big-col' + (pick === k ? ' picked' : '') }, [
          el('div', { class: 'big-col-head' }, [
            el('b', { text: label }),
            el('button', { type: 'button', class: 'mini' + (pick === k ? ' on' : ''), text: pick === k ? '이 글을 씀' : '이 글 쓰기', onclick: () => decide({ page: pick === k ? null : k }) }),
          ]),
          el('pre', { text: text || '' }),
        ]))),
      ]));
      return;
    }
    const left = d.unreviewed;
    const hasSugg = Object.keys(sugg).length > 0;
    box.appendChild(el('div', { class: 'review-head' }, [
      el('b', { text: diffs.length ? `다르게 읽은 곳 ${diffs.length}곳` : '두 결과가 같습니다' }),
      diffs.length ? el('span', { class: 'badge warn' + (left ? '' : ' done'), text: left ? `남은 ${left}곳` : '검토 끝' }) : null,
      el('span', { class: 'spacer' }),
      diffs.length ? el('button', { type: 'button', class: 'mini', text: '모두 CLOVA', onclick: () => decide({ all: 'clova' }) }) : null,
      diffs.length ? el('button', { type: 'button', class: 'mini', text: '모두 Qwen', onclick: () => decide({ all: 'ai' }) }) : null,
      diffs.length ? el('button', { type: 'button', class: 'mini', text: 'AI 제안', title: '그림 없이 다른 곳의 글자와 앞뒤 문맥만 보냅니다(학번·이름은 ○로 지움).', onclick: (e) => suggest(e.currentTarget) }) : null,
      hasSugg ? el('button', { type: 'button', class: 'mini', text: '제안 모두 받기', onclick: () => decide({ accept_suggest: true }) }) : null,
      diffs.length ? el('button', { type: 'button', class: 'mini', text: '고른 것 지우기', onclick: () => decide({ reset: true }) }) : null,
    ]));
    const list = el('div', { class: 'diff-list' });
    diffs.forEach((x) => {
      const c = dec[x.id] || {};
      const sg = sugg[x.id];
      const img = el('img', { class: 'diff-crop', alt: '원본 그 줄' });
      if (x.box) loadCrop(img, curPage(), x, d);
      const btn = (k, label, text) => el('button', {
        type: 'button', class: 'pick' + (c.pick === k ? ' on' : ''),
        onclick: () => decide({ d: { [x.id]: c.pick === k ? null : { pick: k } } }),
      }, [el('small', { text: label }), el('span', { text: (text || '').replace(/\n/g, ' ') || '(없음)' })]);
      const edit = el('button', {
        type: 'button', class: 'pick' + (c.pick === 'edit' ? ' on' : ''),
        onclick: async () => {
          const t = await promptBox('이 부분을 직접 고칩니다. 비우면 이 부분을 지웁니다.', c.pick === 'edit' ? c.text : x.clova);
          if (t !== null) decide({ d: { [x.id]: { pick: 'edit', text: t } } });
        },
      }, [el('small', { text: '고치기' }), el('span', { text: c.pick === 'edit' ? (c.text || '(지움)') : '…' })]);
      const sgText = sg ? `AI 제안: ${sg.pick === 'ai' ? 'Qwen' : sg.pick === 'clova' ? 'CLOVA' : `섞기 "${sg.text}"`}${sg.reason ? ` - ${sg.reason}` : ''}${sg.new ? ' (두 결과에 없는 글자를 만듦, 확인 필요)' : ''}` : '';
      list.appendChild(el('div', { class: 'diff' + (c.pick ? ' decided' : '') }, [
        x.box ? img : null,
        el('div', { class: 'diff-ctx' }, [
          el('span', { class: 'muted', text: '…' + x.before }),
          el('mark', { text: (x.clova || '').replace(/\n/g, ' ') || '∅' }),
          el('span', { class: 'muted', text: x.after + '…' }),
        ]),
        el('div', { class: 'diff-btns' }, [btn('clova', 'CLOVA', x.clova), btn('ai', 'Qwen', x.ai), edit]),
        sg ? el('div', { class: 'diff-sugg' + (sg.new ? ' new' : ''), text: sgText }) : null,
      ]));
    });
    box.appendChild(list);
  }
  async function loadCrop(img, page, x, d) {
    const key = `${page}:${x.box.join(',')}`;
    const r = viewer.crops[key] || await api.diff_crop(page, x.box, d.w, d.h, x.clip);
    if (r.ok) { viewer.crops[key] = r; img.src = r.src; } else img.alt = r.error;
  }
  async function suggest(btn) {
    btn.disabled = true;
    btn.textContent = '제안 받는 중…';
    const d = await api.suggest(curPage());
    if (d.ok === false) { alertBox(d.error); btn.disabled = false; btn.textContent = 'AI 제안'; return; }
    if (d.suggest_cost) setCredits(d.suggest_cost, undefined);
    fillViewer(d);
  }
  function closeEdit() { $('viewerEdit').hidden = true; $('viewerText').hidden = false; $('viewerEditBtn').hidden = false; }
  function bindViewer() {
    $('viewerClose').addEventListener('click', () => { $('viewer').hidden = true; });
    $('viewerPrev').addEventListener('click', () => { viewer.pos--; showViewerPage(); });
    $('viewerNext').addEventListener('click', () => { viewer.pos++; showViewerPage(); });
    document.addEventListener('keydown', (e) => {
      if ($('viewer').hidden || !$('modal').hidden) return;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.key === 'Escape') $('viewer').hidden = true;
      else if (e.key === 'ArrowLeft' && !$('viewerPrev').disabled) $('viewerPrev').click();
      else if (e.key === 'ArrowRight' && !$('viewerNext').disabled) $('viewerNext').click();
    });
    $('viewerMode').addEventListener('change', async () => {
      const r = await api.set_layout(curPage(), $('viewerMode').value, null);
      if (!r.ok) { alertBox(r.error); return; }
      fillViewer(r.detail);
      refreshSummaries();
    });
    $('viewerSide').addEventListener('change', async () => {
      const r = await api.set_layout(curPage(), null, $('viewerSide').value);
      if (!r.ok) { alertBox(r.error); return; }
      fillViewer(r.detail);
      refreshSummaries();
      if (r.ai_reset) alertBox('읽을 단이 바뀌어 이 쪽의 Qwen 결과를 지웠습니다. [못 읽은 쪽 다시 읽기]로 바뀐 단만 다시 보냅니다.');
    });
    $('viewerFigs').addEventListener('change', () => decide({ figs: $('viewerFigs').checked }));
    $('viewerEditBtn').addEventListener('click', () => {
      $('viewerEditText').value = viewer.d ? viewer.d.text : '';
      $('viewerEdit').hidden = false;
      $('viewerText').hidden = true;
      $('viewerEditBtn').hidden = true;
      $('viewerEditText').focus();
    });
    $('viewerEditCancel').addEventListener('click', closeEdit);
    $('viewerEditSave').addEventListener('click', async () => { await decide({ page: 'edit', text: $('viewerEditText').value }); closeEdit(); });
    $('viewerEditClear').addEventListener('click', async () => {
      if (!(await confirmBox('직접 고친 글을 지우고 OCR 결과(고른 것)로 되돌릴까요?'))) return;
      await decide({ page: null });
      closeEdit();
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
    if (!target) return { text: `${n.toLocaleString()}바이트`, off: false };
    const off = Math.abs(n - target) > target * 0.1;
    return { text: `${n.toLocaleString()} / ${target.toLocaleString()}바이트`, off };
  }
  function updateBytes(card) {
    card.querySelectorAll('[data-bytes-for]').forEach((b) => {
      const ta = card.querySelector(`textarea[data-role="${b.dataset.bytesFor}"]`);
      if (!ta) return;
      const isOriginal = b.dataset.bytesFor === 'original';
      const lb = isOriginal ? { text: `${countBytes(ta.value).toLocaleString()}바이트`, off: false } : bytesLabel(ta.value);
      b.textContent = lb.text;
      b.classList.toggle('off', lb.off);
    });
  }

  // 1안, 2안 중 긴 내용이 다 보이도록 높이를 맞춘다. 원본은 같은 높이에서 스크롤한다.
  function fitCard(card) {
    const results = card.querySelectorAll('textarea[data-role="r0"], textarea[data-role="r1"]');
    const original = card.querySelector('textarea[data-role="original"]');
    let h = 200;
    results.forEach((ta) => { ta.style.height = 'auto'; h = Math.max(h, ta.scrollHeight + 4); });
    results.forEach((ta) => { ta.style.height = h + 'px'; });
    card.querySelectorAll('.col .empty').forEach((e) => { e.style.height = h + 'px'; });
    if (original) original.style.height = h + 'px';
  }

  function renderCard(s) {
    const no = s.index + 1;
    const sid = el('input', { type: 'text', class: 'sid' + (s.student_id ? '' : ' missing'), placeholder: '학번', value: s.student_id });
    sid.addEventListener('input', () => { s.student_id = sid.value.trim(); sid.classList.toggle('missing', !s.student_id); });
    const name = el('input', { type: 'text', class: 'sname' + (s.name ? '' : ' missing'), placeholder: '이름', value: s.name });
    name.addEventListener('input', () => { s.name = name.value.trim(); name.classList.toggle('missing', !s.name); });
    const check = el('input', { type: 'checkbox', class: 'check' });
    check.checked = s.checked;
    check.addEventListener('change', () => { s.checked = check.checked; });

    const head = el('div', { class: 'student-head' }, [
      check,
      el('span', { class: 'seq', text: `${no}번째 학생` }),
      el('span', { class: 'pages', text: `p.${s.pages}` }),
      el('label', { class: 'field-inline' }, ['학번', sid]),
      el('label', { class: 'field-inline' }, ['이름', name]),
      s.picked !== null ? el('span', { class: 'picked-badge', text: `${s.picked + 1}안 선택됨` }) : null,
      s.id_by === 'ai' && (s.student_id || s.name) ? el('span', { class: 'badge', title: '머리글에서 규칙으로 찾지 못해 AI가 찾은 값입니다. 확인해 주세요.', text: 'AI가 찾음' }) : null,
      s.page_list && s.page_list.length ? el('button', { type: 'button', class: 'mini', text: '원본 보기', onclick: () => openViewer(s.page_list, s.page_list[0], `${no}번째 학생 원본`) }) : null,
      s.error ? el('span', { class: 'err', text: '정제 실패: ' + s.error }) : null,
      s.warn ? el('span', { class: 'err', text: s.warn }) : null,
      s.rerr ? el('span', { class: 'err', text: '변환 실패: ' + s.rerr }) : null,
      el('button', {
        type: 'button', class: 'rerun',
        text: s.busy ? '변환 중…' : `${no}번째 학생 다시 변환`,
        disabled: busy, onclick: () => runTransform([s.index]),
      }),
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
          el('div', { class: 'col-title', text: `${i + 1}안` }),
          el('div', { class: 'empty', text: s.busy && (i === 0 || s.results[0]) ? '변환 중…' : '아직 결과가 없습니다' }),
        ]);
      }
      const ta = el('textarea', { 'data-role': 'r' + i });
      ta.value = r.text;
      ta.addEventListener('input', () => { r.text = ta.value; updateBytes(card); fitCard(card); });
      return el('div', { class: cls }, [
        el('div', { class: 'col-title' }, [`${i + 1}안`, el('span', { class: 'bytes', 'data-bytes-for': 'r' + i })]),
        ta,
        el('button', {
          type: 'button',
          class: 'pick-btn',
          text: s.picked === i ? `${i + 1}안 선택됨` : `${i + 1}안 선택`,
          onclick: () => { s.picked = s.picked === i ? null : i; rerenderCard(s); },
        }),
      ]);
    };

    const card = el('div', { class: 'student' + (s.busy ? ' busy' : '') + (s.picked !== null ? ' has-pick' : ''), 'data-index': s.index }, [
      head,
      el('div', { class: 'student-body' }, [origCol, resultCol(0), resultCol(1)]),
    ]);
    original.addEventListener('input', () => { s.original = original.value; updateBytes(card); });
    updateBytes(card);
    return card;
  }

  function rerenderCard(s) {
    const old = document.querySelector(`.student[data-index="${s.index}"]`);
    if (!old) return;
    const card = renderCard(s);
    old.replaceWith(card);
    fitCard(card);
  }
  function renderStudents() {
    const box = $('studentList');
    box.innerHTML = '';
    sortedStudents().forEach((s) => box.appendChild(renderCard(s)));
    $('stage2').hidden = students.length === 0;
    box.querySelectorAll('.student').forEach(fitCard);
  }

  // ---------- 2단계: 변환 ----------
  async function runTransform(indices) {
    if (busy) return;
    const targets = indices.map(byIndex).filter(Boolean);
    if (!targets.length) { alertBox('변환할 학생을 체크해 주세요.'); return; }
    const empty = targets.filter((s) => !s.original.trim());
    if (empty.length === targets.length) { alertBox('원본이 비어 있어 변환할 수 없습니다.'); return; }
    const willDrop = targets.filter((s) => s.original.trim() && s.results[0] && s.results[1]).length;
    if (willDrop && !(await confirmBox(`${willDrop}명의 1안 결과가 삭제됩니다.\n(2안 결과가 1안으로 옮겨지고, 새 결과가 2안에 들어갑니다.)\n계속할까요?`))) return;

    const tab = (cfg.transform_tabs || [])[cfg.active_tab] || {};
    const items = targets.filter((s) => s.original.trim()).map((s) => ({ index: s.index, original: s.original }));
    const r = await api.start_transform(items, tab.prompt || '', cfg.target_bytes || 0, tab.example || '');
    if (!r.ok) { alertBox(r.error); return; }
    setBusy(true);
    items.forEach((it) => { const s = byIndex(it.index); s.busy = true; s.rerr = ''; rerenderCard(s); });
    openProgress('transform');
  }

  function applyResult(ev) {
    const s = byIndex(ev.index);
    if (!s) return;
    s.busy = false;
    if (ev.skipped) {
      // 중지되어 보내지 않은 학생은 그대로 둔다
    } else if (ev.error) {
      s.rerr = ev.error;
    } else if (!s.results[0]) {
      s.results[0] = { text: ev.text };
    } else if (!s.results[1]) {
      s.results[1] = { text: ev.text };
    } else {
      // 2안 → 1안, 기존 1안 삭제, 새 결과 → 2안. 선택은 내용을 따라간다.
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
      if (unpicked && !(await confirmBox(`변환 결과(1안 또는 2안)를 선택하지 않은 학생이 ${unpicked}명 있습니다.\n이 학생들은 변환(D열)이 빈칸으로 저장됩니다. 계속할까요?`))) return;
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
      case 'step':
        updateStep(ev);
        break;
      case 'error':
        closeProgress();
        pendingRefine = false;
        setStatus('error', '오류: ' + ev.message);
        if (pdf) api.scan_pages().then((pages) => { if (pages.some((p) => p.read)) showGroups(pages); });
        students.forEach((s) => { if (s.busy) { s.busy = false; rerenderCard(s); } });
        alertBox('오류: ' + ev.message);
        break;
      case 'stopped':
        closeProgress();
        setStatus('error', '중지했습니다.');
        break;
      case 'ocr_done': {
        closeProgress();
        showGroups(ev.pages);
        if (ev.cost) setCredits(ev.cost, ev.balance ?? undefined);
        if (ev.ai_message) alertBox('Qwen으로 읽기를 멈췄습니다: ' + ev.ai_message);
        const unread = ev.pages.filter(unreadPage).length;
        if (ev.stopped || unread) pendingRefine = false;
        if (ev.stopped) setStatus('error', `OCR을 중지했습니다. 읽은 쪽은 남아 있습니다(못 읽은 쪽 ${unread}쪽).`);
        else if (unread) setStatus('error', `${unread}쪽을 읽지 못했습니다. [못 읽은 쪽 다시 읽기]를 눌러 주세요.`);
        else setStatus('info', pendingRefine ? 'OCR 완료 - 바로 정제합니다.' : 'OCR 완료 - 아래에서 학생별 묶기를 확인한 뒤 [정제 시작]을 누르세요.');
        break;
      }
      case 'stage1_done': {
        closeProgress();
        students = ev.students.map((s) => Object.assign(s, { results: [null, null], picked: null, checked: true, busy: false, rerr: '' }));
        sortMode = 'scan';
        document.querySelectorAll('.sort-btn').forEach((x) => x.classList.toggle('active', x.dataset.sort === 'scan'));
        renderStudents();
        const failed = students.filter((s) => s.error).length;
        const missing = students.filter((s) => !s.student_id || !s.name).length;
        let msg = `완료 - ${pdf ? pdf.name : ''}, ${students.length}명`;
        if (failed) msg += ` - 정제 실패 ${failed}명(OCR 원문을 그대로 넣었습니다)`;
        if (missing) msg += ` - 학번/이름 확인 필요 ${missing}명(노란 칸)`;
        if (ev.stopped) msg = '중지됨 - ' + msg;
        const aiIds = students.filter((s) => s.id_by === 'ai' && (s.student_id || s.name)).length;
        if (aiIds) msg += ` - AI가 찾은 학번/이름 ${aiIds}명`;
        $('stage1Summary').textContent = msg;
        $('stage1Toggle').hidden = false;
        setStatus(failed || ev.stopped ? 'error' : 'info', msg);
        setCredits(ev.cost, ev.balance);
        setStage1Collapsed(!failed && !ev.stopped);
        window.scrollTo(0, 0);
        break;
      }
      case 'transform_result':
        applyResult(ev);
        break;
      case 'transform_done':
        closeProgress();
        setCredits(ev.cost, ev.balance);
        if (ev.stopped) alertBox('변환을 중지했습니다. 이미 받은 결과는 반영했습니다.');
        break;
      case 'idle':
        setBusy(false);
        if (pendingRefine) { pendingRefine = false; startRefine(true); }
        break;
      case 'update_available':
        showUpdate(ev);
        break;
      case 'update_none':
        appInfo.update = ev.error ? `업데이트 확인 실패: ${ev.error}` : '최신 버전입니다.';
        break;
      case 'update_progress':
        $('updateText').textContent = `새 버전 내려받는 중… ${Math.round((ev.done / ev.total) * 100)}%`;
        break;
      case 'update_ready':
        updateReady(ev.version);
        break;
      case 'update_error':
        $('updateText').textContent = `업데이트 실패: ${ev.message}`;
        $('updateNowBtn').disabled = false;
        $('updateNowBtn').textContent = '내려받기 페이지';
        updateInfo.can_apply = false;
        break;
    }
  };

  // ---------- 메뉴, 안내 ----------
  function bindMenu() {
    document.querySelectorAll('.menu-btn[data-panel]').forEach((b) => {
      b.addEventListener('click', () => showPanel(b.dataset.panel));
    });
    $('brandBox').addEventListener('click', () => {
      const url = appInfo.pages_url || '';
      $('modal').classList.add('wide');
      modal(`<div class="about"><img src="face.png" alt="" class="about-face">` +
        `<div class="about-name">${appInfo.name || '악필교정기'} <span>v${appInfo.version || ''}</span></div>` +
        `<div class="about-tag">${appInfo.tagline || ''}</div>` +
        `<div class="about-maker">Made by 김한량 디지털 공방</div>` +
        `<div class="about-update muted">${appInfo.update || '업데이트 확인 중…'}</div></div>` +
        `<div class="about-share"><b>다른 사람에게 줄 때</b><br>` +
        `내려받기 페이지 주소를 알려 주세요: <a href="${url}">${url.replace(/^https:\/\//, '').replace(/\/$/, '')}</a><br>` +
        `파일을 직접 줄 때는 <b class="danger-text">exe 파일만</b> 전달하세요. 키는 exe 옆 <code>악필교정기_설정.json</code>에 평문으로 저장되어, 폴더째 주면 내 키도 함께 넘어갑니다.<br>` +
        `<span class="muted">공용 PC라면 설정 &gt; 연결 정보 맨 아래 [키 초기화]로 키를 지우세요.</span></div>`, false, true);
    });
    $('stopBtn').addEventListener('click', async () => {
      $('stopBtn').disabled = true;
      $('stopBtn').textContent = '중지 중…';
      await api.stop();
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

  // ---------- 업데이트 ----------
  let updateInfo = null;
  function showUpdate(ev) {
    updateInfo = ev;
    appInfo.update = `새 버전 v${ev.version}이(가) 있습니다.`;
    $('updateText').textContent = `새 버전 v${ev.version}이(가) 있습니다.`;
    $('updateNowBtn').textContent = ev.can_apply ? '지금 업데이트' : '내려받기 페이지';
    $('updateBar').hidden = false;
  }
  function updateReady(version) {
    $('updateText').textContent = `v${version}로 바꿔 두었습니다. [다시 시작]을 누르면 새 버전으로 켜집니다.`;
    $('updateNotesBtn').hidden = true;
    $('updateNowBtn').disabled = false;
    $('updateNowBtn').textContent = '다시 시작';
    $('updateLaterBtn').textContent = '나중에(다음에 켤 때 적용)';
    updateInfo.ready = true;
  }
  function bindUpdate() {
    $('updateLaterBtn').addEventListener('click', () => { $('updateBar').hidden = true; });
    $('updateNotesBtn').addEventListener('click', () => alertBox(`v${updateInfo.version} 바뀐 내용\n\n${updateInfo.notes || '(내용 없음)'}`));
    $('updateNowBtn').addEventListener('click', async () => {
      if (!updateInfo) return;
      if (updateInfo.ready) {
        if (busy && !(await confirmBox('작업이 진행 중입니다. 지금 다시 시작하면 진행 중인 작업과 화면의 결과가 사라집니다. 계속할까요?'))) return;
        if (!busy && students.length && !(await confirmBox('다시 시작하면 화면의 학생 목록과 변환 결과가 사라집니다. 먼저 [xlsx 저장]을 하셨나요?\n계속할까요?'))) return;
        const r = await api.update_restart();
        if (!r.ok) alertBox(r.error);
        return;
      }
      if (!updateInfo.can_apply) { api.open_url(appInfo.pages_url); return; }
      const r = await api.update_apply();
      if (!r.ok) { alertBox(r.error); return; }
      $('updateNowBtn').disabled = true;
      $('updateText').textContent = '새 버전 내려받는 중…';
    });
  }

  function showGroups(pages) {
    const first = !scanPages.length;
    scanPages = pages;
    if (first || starts.size === 0) resetGroups();
    $('groupBox').hidden = false;
    renderGroups();
  }

  // 시작 화면을 닫고 본 창을 보인다(여러 번 불러도 한 번만)
  const appReady = () => { try { if (api && api.app_ready) api.app_ready(); } catch (e) { /* 시작 화면이 없으면 그만 */ } };
  async function init() {
    try { await initInner(); } finally { appReady(); }
  }
  async function initInner() {
    api = window.pywebview.api;
    const info = await api.app_info();
    appInfo = info;
    $('appVer').textContent = 'v' + info.version;
    $('maker').textContent = info.maker;
    cfg = await api.load_config();
    fillSettings();
    bindMenu();
    bindSettings();
    bindPromptTabs();
    bindStage1();
    bindStage2();
    bindViewer();
    bindUpdate();
    api.check_update();
    loadGuide();
    refreshBalance();
    const noKeys = !cfg.clova_url || !cfg.clova_secret || !cfg.openrouter_key;
    showPanel(noKeys ? 'settings' : 'main');
    if (noKeys) $('guideBox').open = true;
  }

  window.addEventListener('pywebviewready', init);
})();
