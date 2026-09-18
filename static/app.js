// اعداد ناموجود/نامعتبر را به‌جای NaN با — نشان می‌دهد
const fmtInt = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
};

const startInput = document.getElementById('startDate');
const endInput = document.getElementById('endDate');
const runBtn = document.getElementById('runBtn');
const reqNote = document.getElementById('reqNote');
const dbStatus = document.getElementById('dbStatus');
const emptyState = document.getElementById('emptyState');
const reportRoot = document.getElementById('reportRoot');
const deptSwitcher = document.getElementById('deptSwitcher');

let charts = {};
let departments = [];
let currentDept = null;

// ---------------- Departments ----------------
async function initDepartments() {
  try {
    const res = await fetch('/api/departments');
    const data = await res.json();
    departments = data.departments || [];
    currentDept = data.default;
    renderDeptSwitcher();
  } catch (e) {
    console.error('initDepartments failed:', e);
  }
}

function renderDeptSwitcher() {
  deptSwitcher.innerHTML = '';
  departments.forEach(d => {
    const btn = document.createElement('button');
    btn.className = 'dept-pill' + (d.key === currentDept ? ' active' : '');
    btn.type = 'button';
    btn.textContent = d.label;
    btn.dataset.dept = d.key;
    btn.addEventListener('click', () => switchDept(d.key));
    deptSwitcher.appendChild(btn);
  });
}

function switchDept(key) {
  if (key === currentDept) return;
  currentDept = key;
  renderDeptSwitcher();

  // نمای فعلی را پاک می‌کنیم چون داده‌ی بخش قبلی دیگر معتبر نیست
  emptyState.style.display = 'block';
  reportRoot.style.display = 'none';
  document.getElementById('testDetailCard').style.display = 'none';
  setStatus('', 'در انتظار اجرای تحلیل');
  reqNote.textContent = '';

  // اگر بازه‌ی معتبری از قبل انتخاب شده، همان را برای بخش جدید دوباره اجرا کن
  if (validDateFormat(startInput.value.trim()) && validDateFormat(endInput.value.trim())) {
    runAnalysis();
  }
}

// ---------------- Tabs ----------------
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
  });
});

// ---------------- Presets ----------------
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', async () => {
    const res = await fetch(`/api/preset?range=${chip.dataset.preset}`);
    const data = await res.json();
    startInput.value = data.start;
    endInput.value = data.end;
  });
});

(async function init() {
  await initDepartments();
  try {
    const res = await fetch('/api/preset?range=this_month');
    const data = await res.json();
    startInput.value = data.start;
    endInput.value = data.end;
  } catch (e) { /* silent */ }
})();

function setStatus(state, text) {
  dbStatus.className = 'brand-status ' + state;
  dbStatus.querySelector('.status-text').textContent = text;
}

function validDateFormat(v) {
  return /^\d{4}-\d{2}-\d{2}$/.test(v);
}

// ---------------- Run analysis ----------------
runBtn.addEventListener('click', runAnalysis);

async function runAnalysis() {
  const start = startInput.value.trim();
  const end = endInput.value.trim();
  reqNote.textContent = '';
  startInput.classList.remove('invalid');
  endInput.classList.remove('invalid');

  if (!validDateFormat(start) || !validDateFormat(end)) {
    reqNote.textContent = 'فرمت تاریخ باید به شکل 1405-01-01 باشد.';
    if (!validDateFormat(start)) startInput.classList.add('invalid');
    if (!validDateFormat(end)) endInput.classList.add('invalid');
    return;
  }

  if (!currentDept) {
    reqNote.textContent = 'بخش هنوز مشخص نشده — چند لحظه صبر کنید و دوباره تلاش کنید.';
    return;
  }

  runBtn.disabled = true;
  runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> در حال دریافت...';
  setStatus('', 'در حال محاسبه...');

  let res;
  try {
    res = await fetch(`/api/report?start=${start}&end=${end}&dept=${currentDept}`);
  } catch (e) {
    reqNote.textContent = 'ارتباط با سرور برقرار نشد — مطمئن شوید python app.py هنوز در حال اجراست.';
    setStatus('error', 'خطا در ارتباط');
    runBtn.disabled = false;
    runBtn.innerHTML = '<i class="fa-solid fa-flask-vial"></i> اجرای تحلیل';
    return;
  }

  try {
    const raw = await res.text();
    let data;
    try {
      data = JSON.parse(raw);
    } catch (parseErr) {
      reqNote.textContent = 'خطای غیرمنتظره از سرور (پاسخ JSON نبود). متن کامل در کنسول مرورگر (F12) موجود است.';
      console.error('Non-JSON response from server:', raw);
      setStatus('error', 'خطای سرور');
      return;
    }

    if (!res.ok) {
      reqNote.textContent = data.error || 'خطایی رخ داد.';
      if (data.traceback) console.error(data.traceback);
      setStatus('error', 'خطا در دریافت داده');
      return;
    }

    emptyState.style.display = 'none';
    reportRoot.style.display = 'block';
    renderAll(data, start, end);
    const deptLabel = (departments.find(d => d.key === currentDept) || {}).label || currentDept;
    setStatus('ready', `${deptLabel} — به‌روز — ${start} تا ${end}`);

  } catch (e) {
    reqNote.textContent = 'خطای غیرمنتظره در پردازش پاسخ سرور. کنسول مرورگر (F12) را بررسی کنید.';
    console.error(e);
    setStatus('error', 'خطا');
  } finally {
    runBtn.disabled = false;
    runBtn.innerHTML = '<i class="fa-solid fa-flask-vial"></i> اجرای تحلیل';
  }
}

// ---------------- Rendering ----------------
function renderAll(data, start, end) {
  try { renderKitsTable(data.tests); } catch (e) { console.error('renderKitsTable failed:', e); }
  try { renderInsights(data.insights); } catch (e) { console.error('renderInsights failed:', e); }
  try { renderConsumablesTable(data.consumables); } catch (e) { console.error('renderConsumablesTable failed:', e); }
  try { renderLotsTable(data.tests); } catch (e) { console.error('renderLotsTable failed:', e); }
  try { loadRepeats(); } catch (e) { console.error('loadRepeats failed:', e); }

  const warnParts = [];
  if (data.missing_days && data.missing_days.length) {
    warnParts.push(
      'برای این تست‌ها هیچ آمار روزانه‌ای در بازه‌ی انتخابی ثبت نشده (ممکن است data_collector برای این بازه اجرا نشده باشد): ' +
      data.missing_days.join('، ')
    );
  }
  if (data.missing_consumption && data.missing_consumption.length) {
    warnParts.push(
      'برای این تست‌ها مصرف هر تست تعریف نشده، پس ستون «ظرفیت کل» برایشان نامشخص است: ' +
      data.missing_consumption.join('، ')
    );
  }

  if (warnParts.length) {
    document.getElementById('warnBox').style.display = 'flex';
    document.getElementById('warnText').textContent = warnParts.join(' — ');
  } else {
    document.getElementById('warnBox').style.display = 'none';
  }
}

// ---------------- Tab 1: کیت و ظرفیت ----------------

// خلاصه‌ی کوتاه تفکیک دستگاه برای زیرِ نام تست (مستقل از این‌که چه
// دستگاه‌هایی در این بخش تعریف شده‌اند — کاملاً پویا)
function deviceBreakdownText(test) {
  const byType = test.tests_by_device_type || {};
  const parts = Object.entries(byType)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => `${type}: ${fmtInt(count)}`);
  return parts.join(' · ');
}

function fmtWaste(value) {
  if (value === null || value === undefined) return '—';
  const cls = value > 0 ? 'val-neg' : (value < 0 ? 'val-pos' : '');
  return `<span class="${cls}">${fmtInt(value)}</span>`;
}

function fmtWastePercent(value) {
  if (value === null || value === undefined) return '—';
  const cls = value > 0 ? 'val-neg' : (value < 0 ? 'val-pos' : '');
  return `<span class="${cls}">${value.toFixed(1)}%</span>`;
}

function renderKitsTable(tests) {
  const tbody = document.querySelector('#tableKits tbody');
  tbody.innerHTML = '';
  const sorted = [...tests].sort((a, b) => b.qty_out - a.qty_out);
  for (const t of sorted) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    const breakdown = deviceBreakdownText(t);
    const sharedNote = t.shared_kit_group
      ? `<div class="device-breakdown" style="color:#B5482E;">⚠ ${t.shared_kit_group} — ظرفیت به نسبت سهم واقعی تقسیم شده</div>`
      : '';
    tr.innerHTML = `
      <td>
        ${t.name}
        ${breakdown ? `<div class="device-breakdown">${breakdown}</div>` : ''}
        ${sharedNote}
      </td>
      <td>${t.code}</td>
      <td>${fmtInt(t.total_tests)}</td>
      <td>${fmtInt(t.qty_in)}</td>
      <td>${fmtInt(t.qty_out)}</td>
      <td>${t.capacity_unknown ? 'نامشخص' : fmtInt(t.capacity)}</td>
      <td>${t.capacity_unknown ? 'نامشخص' : fmtWaste(t.waste)}</td>
      <td>${t.capacity_unknown ? 'نامشخص' : fmtWastePercent(t.waste_percent)}</td>
      <td>${fmtInt(t.current_stock)}</td>
    `;
    tr.addEventListener('click', () => showTestDetail(t));
    tbody.appendChild(tr);
  }

  const withOutput = sorted.filter(t => t.qty_out > 0);
  renderBarChart('chartKitOut', withOutput.map(t => t.name), withOutput.map(t => t.qty_out), 'کیت خروجی', '#1F7A6C');
}

// ---------------- تحلیل و بینش (زیر تب کیت و ظرفیت) ----------------
function renderInsights(insights) {
  if (!insights) return;

  const topKit = insights.top_kits_by_output[0];
  document.getElementById('kpiTopKitOutput').textContent = topKit
    ? `${topKit.kit_name} (${fmtInt(topKit.qty_out)})`
    : '—';

  const topTestCount = insights.top_tests_by_count[0];
  document.getElementById('kpiTopTestCount').textContent = topTestCount
    ? `${topTestCount.name} (${fmtInt(topTestCount.total_tests)})`
    : '—';

  const topWaste = insights.top_tests_by_waste[0];
  document.getElementById('kpiTopWaste').textContent = topWaste
    ? `${topWaste.name} (${fmtInt(topWaste.waste)})`
    : '—';

  const topWastePercent = insights.top_tests_by_waste_percent[0];
  document.getElementById('kpiTopWastePercent').textContent = topWastePercent
    ? `${topWastePercent.name} (${topWastePercent.waste_percent.toFixed(1)}%)`
    : '—';

  document.getElementById('kpiOverallEfficiency').textContent =
    insights.overall_efficiency_percent !== null && insights.overall_efficiency_percent !== undefined
      ? `${insights.overall_efficiency_percent.toFixed(1)}%`
      : 'نامشخص';

  const kits = insights.top_kits_by_output;
  renderBarChart(
    'chartTopKitsOutput',
    kits.map(k => `${k.kit_name} (${k.test_name})`),
    kits.map(k => k.qty_out),
    'کیت خروجی', '#1F7A6C'
  );

  const byCount = insights.top_tests_by_count;
  renderBarChart(
    'chartTopTestsByCount',
    byCount.map(t => t.name),
    byCount.map(t => t.total_tests),
    'تعداد تست', '#2F6F8F'
  );

  const byWaste = insights.top_tests_by_waste;
  renderBarChart(
    'chartTopTestsByWaste',
    byWaste.map(t => t.name),
    byWaste.map(t => t.waste),
    'ضایعات', '#B5482E'
  );
}

function showTestDetail(test) {
  const card = document.getElementById('testDetailCard');
  card.style.display = 'block';
  document.getElementById('detailTestName').textContent = `جزئیات: ${test.name}`;

  const kitBody = document.querySelector('#tableKitDetail tbody');
  kitBody.innerHTML = '';
  const kits = (test.kits || []).filter(k => k.qty_out > 0 || k.current_stock > 0);
  if (!kits.length) {
    kitBody.innerHTML = '<tr><td colspan="10" style="font-family:var(--sans); color:#8CA09A;">در این بازه حرکتی برای کیت‌های این تست ثبت نشده است.</td></tr>';
  } else {
    for (const k of kits) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${k.kit_name || '—'}</td>
        <td>${k.item_no}</td>
        <td>${k.unit || '—'}</td>
        <td>${fmtInt(k.packet_size)}</td>
        <td>${fmtInt(k.qty_in)}</td>
        <td>${fmtInt(k.qty_out)}</td>
        <td>${fmtInt(k.current_stock)}</td>
        <td>${k.capacity_unknown ? 'نامشخص' : fmtInt(k.capacity)}</td>
        <td>${k.capacity_unknown ? 'نامشخص' : fmtWaste(k.waste)}</td>
        <td>${k.capacity_unknown ? 'نامشخص' : fmtWastePercent(k.waste_percent)}</td>
      `;
      kitBody.appendChild(tr);
    }
  }

  const movBody = document.querySelector('#tableMovementsDetail tbody');
  movBody.innerHTML = '';
  const movements = test.movements || [];
  if (!movements.length) {
    movBody.innerHTML = '<tr><td colspan="3" style="font-family:var(--sans); color:#8CA09A;">در این بازه حرکت ورود/خروجی ثبت نشده است.</td></tr>';
  } else {
    for (const m of movements) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${m.date}</td>
        <td class="${m.qty_in > 0 ? 'val-pos' : ''}">${m.qty_in > 0 ? fmtInt(m.qty_in) : '—'}</td>
        <td class="${m.qty_out > 0 ? 'val-neg' : ''}">${m.qty_out > 0 ? fmtInt(m.qty_out) : '—'}</td>
      `;
      movBody.appendChild(tr);
    }
  }

  const devBody = document.querySelector('#tableDeviceDetail tbody');
  devBody.innerHTML = '';
  const devices = (test.devices || []).filter(d => d.count > 0);
  if (!devices.length) {
    devBody.innerHTML = '<tr><td colspan="4" style="font-family:var(--sans); color:#8CA09A;">در این بازه آماری برای دستگاه‌های این تست ثبت نشده است.</td></tr>';
  } else {
    for (const d of devices) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${d.device_name}</td>
        <td>${d.device_type}</td>
        <td style="font-family:var(--sans); white-space:normal;">${d.log_desc || '—'}</td>
        <td>${fmtInt(d.count)}</td>
      `;
      devBody.appendChild(tr);
    }
  }

  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

document.getElementById('closeTestDetail').addEventListener('click', () => {
  document.getElementById('testDetailCard').style.display = 'none';
});

// ---------------- مواد مصرفی عمومی (غیرمرتبط با تست خاص) ----------------
function renderConsumablesTable(consumables) {
  const tbody = document.querySelector('#tableConsumables tbody');
  tbody.innerHTML = '';
  const list = consumables || [];
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="font-family:var(--sans); color:#8CA09A;">در این بازه ماده‌ی مصرفی عمومی‌ای ثبت نشده است.</td></tr>';
    return;
  }
  for (const c of list) {
    const tr = document.createElement('tr');
    tr.classList.add('clickable');
    tr.innerHTML = `
      <td>${c.name || '—'}</td>
      <td>${c.item_no}</td>
      <td>${c.unit || '—'}</td>
      <td>${fmtInt(c.qty_in)}</td>
      <td>${fmtInt(c.qty_out)}</td>
      <td>${fmtInt(c.current_stock)}</td>
    `;
    tr.addEventListener('click', () => showConsumableDetail(c));
    tbody.appendChild(tr);
  }
}

function showConsumableDetail(item) {
  const card = document.getElementById('consumableDetailCard');
  card.style.display = 'block';
  document.getElementById('detailConsumableName').textContent = `جزئیات: ${item.name}`;

  const movBody = document.querySelector('#tableConsumableMovements tbody');
  movBody.innerHTML = '';
  const movements = item.movements || [];
  if (!movements.length) {
    movBody.innerHTML = '<tr><td colspan="3" style="font-family:var(--sans); color:#8CA09A;">در این بازه حرکت ورود/خروجی ثبت نشده است.</td></tr>';
  } else {
    for (const m of movements) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${m.date}</td>
        <td class="${m.qty_in > 0 ? 'val-pos' : ''}">${m.qty_in > 0 ? fmtInt(m.qty_in) : '—'}</td>
        <td class="${m.qty_out > 0 ? 'val-neg' : ''}">${m.qty_out > 0 ? fmtInt(m.qty_out) : '—'}</td>
      `;
      movBody.appendChild(tr);
    }
  }

  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

document.getElementById('closeConsumableDetail').addEventListener('click', () => {
  document.getElementById('consumableDetailCard').style.display = 'none';
});

// ---------------- Tab 2: پارتی‌ها و انقضا ----------------
function renderLotsTable(tests) {
  const tbody = document.querySelector('#tableLots tbody');
  tbody.innerHTML = '';

  const rows = [];
  for (const t of tests) {
    for (const p of (t.partynos || [])) {
      if (p.qty > 0 || (p.days_remaining !== null && p.days_remaining <= 60)) {
        rows.push({ testName: t.name, ...p });
      }
    }
  }
  rows.sort((a, b) => {
    const da = a.days_remaining === null ? Infinity : a.days_remaining;
    const db = b.days_remaining === null ? Infinity : b.days_remaining;
    return da - db;
  });

  let nearExpiryCount = 0;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="font-family:var(--sans); color:#8CA09A;">پارتی‌ای برای نمایش یافت نشد.</td></tr>';
  } else {
    for (const r of rows) {
      const isNear = r.days_remaining !== null && r.days_remaining <= 30 && r.qty > 0;
      if (isNear) nearExpiryCount++;
      const tr = document.createElement('tr');
      if (isNear) tr.style.background = '#FBF4E7';
      tr.innerHTML = `
        <td>${r.testName}</td>
        <td>${r.kit_name}</td>
        <td>${r.party_no}</td>
        <td>${fmtInt(r.qty)}</td>
        <td>${r.expire_date || '—'}</td>
        <td>${r.status_text || '—'}</td>
        <td class="${isNear ? 'val-neg' : ''}">${r.days_remaining !== null ? fmtInt(r.days_remaining) : '—'}</td>
      `;
      tbody.appendChild(tr);
    }
  }
  document.getElementById('kpiNearExpiry').textContent = fmtInt(nearExpiryCount);
}

// ---------------- Tab 3: علت تکرار تست‌ها (فایل اکسل بخش فنی) ----------------
const repeatsFileInput = document.getElementById('repeatsFile');
const uploadRepeatsBtn = document.getElementById('uploadRepeatsBtn');
const uploadRepeatsNote = document.getElementById('uploadRepeatsNote');
const repeatsEmptyState = document.getElementById('repeatsEmptyState');
const repeatsContent = document.getElementById('repeatsContent');

uploadRepeatsBtn.addEventListener('click', async () => {
  const file = repeatsFileInput.files[0];
  if (!file) {
    uploadRepeatsNote.textContent = 'اول یک فایل انتخاب کنید.';
    uploadRepeatsNote.style.color = '#B5453A';
    return;
  }

  uploadRepeatsBtn.disabled = true;
  uploadRepeatsNote.textContent = 'در حال بارگذاری...';
  uploadRepeatsNote.style.color = '#6E7F7A';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`/api/upload-repeats?dept=${currentDept}`, { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) {
      uploadRepeatsNote.textContent = data.error || 'خطا در بارگذاری فایل.';
      uploadRepeatsNote.style.color = '#B5453A';
      if (data.traceback) console.error(data.traceback);
      return;
    }
    uploadRepeatsNote.textContent = `${data.rows} ردیف با موفقیت بارگذاری شد.`;
    uploadRepeatsNote.style.color = '#3F9142';
    await loadRepeats();
  } catch (e) {
    uploadRepeatsNote.textContent = 'ارتباط با سرور برقرار نشد.';
    uploadRepeatsNote.style.color = '#B5453A';
    console.error(e);
  } finally {
    uploadRepeatsBtn.disabled = false;
  }
});

async function loadRepeats() {
  const start = startInput.value.trim();
  const end = endInput.value.trim();
  if (!validDateFormat(start) || !validDateFormat(end) || !currentDept) return;

  try {
    const res = await fetch(`/api/repeats?start=${start}&end=${end}&dept=${currentDept}`);
    const data = await res.json();
    if (!data.available) {
      repeatsEmptyState.style.display = 'block';
      repeatsContent.style.display = 'none';
      return;
    }
    repeatsEmptyState.style.display = 'none';
    repeatsContent.style.display = 'block';
    renderRepeats(data);
  } catch (e) {
    console.error('loadRepeats failed:', e);
  }
}

function renderRepeats(data) {
  document.getElementById('kpiRepeatCount').textContent = fmtInt(data.summary.total_rows);

  renderRepeatsNarrative(data.summary);

  // جدول عمومی — دقیقاً همان ستون‌هایی که در فایل اکسل بودند
  const headRow = document.getElementById('repeatsHeadRow');
  headRow.innerHTML = data.columns.map(c => `<th>${c}</th>`).join('');

  const tbody = document.querySelector('#tableRepeats tbody');
  tbody.innerHTML = '';
  if (!data.rows.length) {
    tbody.innerHTML = `<tr><td colspan="${data.columns.length}" style="font-family:var(--sans); color:#8CA09A;">در این بازه ردیفی یافت نشد.</td></tr>`;
  } else {
    for (const row of data.rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = data.columns.map(c => `<td style="font-family:var(--sans); white-space:normal;">${row[c] ?? ''}</td>`).join('');
      tbody.appendChild(tr);
    }
  }

  const byTest = data.summary.by_test.slice(0, 15);
  renderBarChart('chartRepeatsByTest', byTest.map(x => x.label), byTest.map(x => x.count), 'تعداد تکرار', '#1F7A6C');

  const byDevice = data.summary.by_device.slice(0, 15);
  renderBarChart('chartRepeatsByDevice', byDevice.map(x => x.label), byDevice.map(x => x.count), 'تعداد تکرار', '#C08A2E');

  const byReason = data.summary.by_reason.slice(0, 15);
  renderBarChart('chartRepeatsByReason', byReason.map(x => x.label), byReason.map(x => x.count), 'تعداد تکرار', '#B5453A');
}

function renderRepeatsNarrative(summary) {
  const card = document.getElementById('repeatsNarrativeCard');
  const list = document.getElementById('repeatsNarrativeList');
  const lines = summary.narrative || [];

  if (!lines.length) {
    card.style.display = 'none';
    return;
  }

  list.innerHTML = lines.map(line => `<li>${line}</li>`).join('');
  card.style.display = 'block';
}

// تلاش برای بارگذاری فایل کش‌شده در همان لحظه‌ای که تب باز می‌شود
document.querySelector('.tab[data-tab="repeats"]').addEventListener('click', loadRepeats);

// ---------------- Chart helpers ----------------
const CHART_FONT = { family: 'Vazirmatn', size: 11 };

function chartLibReady() {
  const ready = typeof Chart !== 'undefined';
  if (!ready) {
    const warn = document.getElementById('chartLoadWarn');
    if (warn) warn.style.display = 'flex';
  }
  return ready;
}

function showChartFallback(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const msg = document.createElement('p');
  msg.style.cssText = 'color:#8CA09A; font-size:0.82rem; margin:0.4rem 0 0;';
  msg.textContent = 'نمودار به دلیل عدم بارگذاری کتابخانه نمایش داده نشد؛ داده‌ها در جدول بالا موجود است.';
  canvas.replaceWith(msg);
}

function destroy(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }

function renderBarChart(canvasId, labels, data, label, color) {
  if (!chartLibReady()) { showChartFallback(canvasId); return; }
  const canvasEl = document.getElementById(canvasId);
  if (!canvasEl) return; // ممکن است قبلاً با پیام جایگزین (fallback) عوض شده باشد

  // اگر داده‌ای برای نمایش نیست، بوم را خالی نگه دار (نه اینکه با آرایه‌ی
  // خالی خطا بدهد یا نموداری گمراه‌کننده و بدون محور نشان بدهد)
  destroy(canvasId);
  if (!labels.length) return;

  const ctx = canvasEl.getContext('2d');
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label, data, backgroundColor: color, borderRadius: 4, maxBarThickness: 34 }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: CHART_FONT }, grid: { color: '#E4EAE7' } },
        y: { ticks: { font: CHART_FONT }, grid: { display: false } }
      }
    }
  });
}


// ---------------- Tab 4: دستیار هوشمند (RAG) ----------------

const chatInput = document.getElementById('chatInput');
const chatAskBtn = document.getElementById('chatAskBtn');
const chatNote = document.getElementById('chatNote');
const chatThread = document.getElementById('chatThread');
const chatDocsList = document.getElementById('chatDocsList');

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderChatDocs(docs) {
  if (!docs || !docs.length) {
    chatDocsList.innerHTML = '<p class="chat-doc-empty">سندی برای این سؤال پیدا نشد.</p>';
    return;
  }
  chatDocsList.innerHTML = docs.map(d => `
    <div class="chat-doc-item">
      <span class="chat-doc-tag">${escapeHtml(d.tag)}</span>
      <div>${escapeHtml(d.text)}</div>
    </div>
  `).join('');
}

async function askChat(question) {
  question = (question || chatInput.value).trim();
  if (!question) return;

  const start = startInput.value.trim();
  const end = endInput.value.trim();
  chatNote.textContent = '';

  if (!validDateFormat(start) || !validDateFormat(end)) {
    chatNote.textContent = 'اول یک بازه‌ی تاریخ معتبر (بالای صفحه) انتخاب کنید.';
    return;
  }
  if (!currentDept) {
    chatNote.textContent = 'بخش هنوز مشخص نشده — چند لحظه صبر کنید و دوباره تلاش کنید.';
    return;
  }

   chatInput.value = '';
  chatAskBtn.disabled = true;
  chatAskBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> در حال فکر کردن...';

  // پنل اسناد سؤال قبلی رو پاک کن تا کاربر گیج نشه
  chatDocsList.innerHTML = '<p class="chat-doc-empty">در حال بازیابی اسناد مرتبط...</p>';
  const qBubble = document.createElement('div');
  qBubble.className = 'chat-bubble question';
  qBubble.textContent = question;
  chatThread.prepend(qBubble);

  const aBubble = document.createElement('div');
  aBubble.className = 'chat-bubble answer pending';
  aBubble.textContent = 'در حال بررسی داده‌ها و تولید پاسخ...';
  chatThread.insertBefore(aBubble, qBubble.nextSibling);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, start, end, dept: currentDept })
    });
    const data = await res.json();

    if (!res.ok) {
          aBubble.classList.remove('pending');
    aBubble.textContent = data.answer;
    if (data.used_llm === false) {
      aBubble.classList.add('fallback');
      const badge = document.createElement('div');
      badge.className = 'chat-fallback-badge';
      badge.innerHTML = '<i class="fa-solid fa-circle-info"></i> پاسخ بدون مدل زبانی (حالت آفلاین)';
           aBubble.appendChild(badge);
    }
    renderChatDocs(data.retrieved);
      return;
    }

    aBubble.classList.remove('pending');
    aBubble.textContent = data.answer;
    renderChatDocs(data.retrieved);

    } catch (e) {
    aBubble.classList.remove('pending');
    aBubble.classList.add('error');
    aBubble.textContent = 'ارتباط با سرور برقرار نشد.';
    chatDocsList.innerHTML = '<p class="chat-doc-empty">ارتباط با سرور قطع شد.</p>';
    console.error(e);
  } finally {
    chatAskBtn.disabled = false;
    chatAskBtn.innerHTML = '<i class="fa-solid fa-comment-dots"></i> بپرس';
  }
}

if (chatAskBtn) {
  chatAskBtn.addEventListener('click', () => askChat());
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      askChat();
    }
  });
  document.querySelectorAll('#chatSamples .chip').forEach(chip => {
    chip.addEventListener('click', () => askChat(chip.dataset.q));
  });
}