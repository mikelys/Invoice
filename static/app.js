const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/>',
  receipt:'<path d="M5 3l2 1 2-1 3 1 3-1 2 1 2-1v18l-2-1-2 1-3-1-3 1-2-1-2 1z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
  chart:'<path d="M4 3v17h17M8 15v-4M13 15V7M18 15v-6"/>',
  shield:'<path d="M12 3l8 3v6c0 4-5 8-8 9-3-1-8-5-8-9V6z"/><path d="M8 12l3 3 5-6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  wallet:'<path d="M19 8V5H5a2 2 0 000 4h15v11H5a2 2 0 01-2-2V7"/><path d="M20 12h-5v5h5M16.5 14.5h.1"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  trend:'<path d="M4 17l6-6 4 3 6-9M14 5h6v6"/>',
  upload:'<path d="M12 16V3M7 8l5-5 5 5M4 15v5h16v-5"/>',
  download:'<path d="M12 3v13M7 11l5 5 5-5M4 16v4h16v-4"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  donut:'<path d="M10 3a9 9 0 1011 11H10z"/><path d="M14 3v7h7a9 9 0 00-7-7z"/>',
  edit:'<path d="M14 5l5 5M4 20l5-1L21 7a2 2 0 00-5-4L4 15z"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.receipt}</svg>`;
$$('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const colors = ['#9b82d1','#c4b1e6','#e6bc9a','#9ec7b7','#d6cc9b','#a5b9da','#d5cce2'];
let currentPage = 'overview', rows = [], allRows = [], report = {}, page = 1, editing = null, config = {}, uploading = false, toastTimer;
const pageSize = 8;
const money = (cents, currency = $('#currency').value) => new Intl.NumberFormat('en-CA', {style:'currency',currency}).format(cents / 100);
function toast(message, error = false) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').className = 'toast' + (error ? ' error' : ''); $('#toast').hidden = false;
  toastTimer = setTimeout(() => $('#toast').hidden = true, error ? 7500 : 4000);
}
async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers:{'X-Invoice-Client':'1', ...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...options.headers}});
  if (!response.ok) { let message = '操作失败，请重试'; try {const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : '请检查填写的数据';} catch {} throw new Error(message); }
  return response.json();
}
function params(includeList = true) {
  const p = new URLSearchParams({currency:$('#currency').value});
  if ($('#start').value) p.set('start', $('#start').value);
  if ($('#end').value) p.set('end', $('#end').value);
  if (includeList) { for (const key of ['category','status','sort']) if ($('#'+key).value) p.set(key,$('#'+key).value); if ($('#search').value.trim()) p.set('q',$('#search').value.trim()); }
  return p;
}
let requestVersion = 0;
async function refresh() {
  if ($('#start').value && $('#end').value && $('#start').value > $('#end').value) {toast('开始日期不能晚于结束日期',true); return;}
  const version = ++requestVersion;
  try {
    const [list, statsList, summary, all] = await Promise.all([api('/api/invoices?'+params()), api('/api/invoices?'+params(false)), api('/api/reports?'+params(false)), api('/api/invoices')]);
    if (version !== requestVersion) return;
    rows = list; allRows = all; report = summary;
    $('#nav-count').textContent = all.length;
    const currency = $('#currency').value, totals = report.totals[currency] || {cents:0,count:0};
    $('#total').textContent = money(totals.cents); $('#average').textContent = money(totals.count ? Math.round(totals.cents/totals.count) : 0);
    $('#total-note').textContent = '已确认的发票金额 · '+currency;
    $('#count').textContent = statsList.length; $('#pending').textContent = statsList.filter(r=>r.status==='pending').length;
    renderTable(); renderCharts(); renderReport();
  } catch (e) {toast(e.message,true);}
}
function renderTable() {
  page = Math.min(page,Math.max(1,Math.ceil(rows.length/pageSize)));
  $('#table-count').textContent = rows.length;
  const slice = rows.slice((page-1)*pageSize,page*pageSize);
  $('#invoice-rows').innerHTML = slice.map(row=>`<tr><td><div class="merchant-cell"><span class="merchant-icon">${icon('receipt')}</span><div><button type="button" class="merchant-name merchant-review" data-edit="${row.id}" title="查看 / 核对 ${esc(row.vendor || '发票')}">${esc(row.vendor || row.file_name || '未填写商户')}</button><div class="merchant-number">${esc(row.number || '待补充发票号码')}</div></div></div></td><td>${esc(row.date || '待补充')}</td><td><span class="category-tag">${esc(row.category)}</span></td><td class="align-right amount">${esc(money(row.amount_cents,row.currency))}<small>${esc(row.currency)}</small></td><td><span class="status ${row.status==='confirmed'?'confirmed':''}">${row.status==='confirmed'?'已确认':'待核对'}</span></td><td><button class="button secondary" data-edit="${row.id}" aria-label="编辑 ${esc(row.vendor||'发票')}">${icon('edit')}查看 / 核对</button>${row.file_name ? `<a class="text-link" href="/api/files/${row.id}" target="_blank" rel="noopener">打开原件 ↗</a>` : ''}</td></tr>`).join('');
  $$('[data-edit]').forEach(button => button.onclick = () => openEditor(allRows.find(r=>r.id===button.dataset.edit)));
  $('#empty-state').hidden = rows.length > 0;
  $('#empty-state h3').textContent = allRows.length ? '没有符合条件的发票' : '让第一张发票，开启有序的一天';
  $('#empty-state p').textContent = allRows.length ? '试试调整日期、搜索词或筛选条件。' : '上传扫描件或手动录入，所有费用都会在这里整理好。';
  $('#pagination-note').textContent = rows.length ? `显示 ${(page-1)*pageSize+1}–${Math.min(page*pageSize,rows.length)}，共 ${rows.length} 张发票` : '共 0 张发票';
  $('#page-number').textContent = page+' / '+Math.max(1,Math.ceil(rows.length/pageSize));
  $('#prev').disabled = page <= 1; $('#next').disabled = page*pageSize >= rows.length;
}
function renderCharts() {
  const currency = $('#currency').value;
  $('#chart-currency').textContent = currency;
  const months = report.months[currency] || {};
  const keys = Object.keys(months).sort();
  if (!keys.length) {
    $('#trend-chart').innerHTML = `<div class="chart-empty">${icon('chart')}<p>确认第一张发票后，在这里查看支出趋势</p></div>`;
  } else {
    // Include zero-spend months between first and last confirmed invoices.
    const start = new Date(keys[0]+'-01T12:00:00'), last = new Date(keys.at(-1)+'-01T12:00:00'), entries = [];
    while (start <= last && entries.length < 1200) { const key = start.getFullYear()+'-'+String(start.getMonth()+1).padStart(2,'0'); entries.push([key,months[key]||0]); start.setMonth(start.getMonth()+1); }
    const display = entries.slice(-12), max = Math.max(...display.map(e=>e[1]),100);
    const unit = max > 100000 ? 100000 : max > 10000 ? 10000 : max > 1000 ? 1000 : 100;
    const ceiling = Math.ceil(max/unit)*unit;
    $('#trend-chart').innerHTML = `<div class="chart-inner"><div class="grid-lines">${[1,.75,.5,.25,0].map(x=>`<div class="grid-line"><span>${new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(ceiling*x/100)}</span></div>`).join('')}</div><div class="bars">${display.map(([key,cents])=>`<div class="bar-group" title="${key} · ${esc(money(cents))}"><div class="bar"></div><span>${display.length>6?Number(key.slice(5))+'月':key.slice(2).replace('-',' / ')}</span></div>`).join('')}</div></div>`;
    // Styles are set through CSSOM to work with the app's content security policy.
    $$('.bar').forEach((el,i)=> el.style.height = display[i][1]/ceiling*100+'%');
  }
  const categories = Object.entries(report.categories[currency]||{}).sort((a,b)=>b[1]-a[1]);
  const total = categories.reduce((s,e)=>s+e[1],0);
  let end = 0;
  const gradient = categories.map(([,amount],i)=>{ const start=end; end += amount/total*100; return `${colors[i%colors.length]} ${start}% ${end}%`; }).join(',');
  $('#category-chart').innerHTML = `<div class="donut"><div class="donut-label">${categories.length}<small>${total?'费用类别':'等待第一笔支出'}</small></div></div><div class="category-legend">${categories.length?categories.map(([name,amount],i)=>`<div class="category-row"><i></i><span>${esc(name)}</span><strong>${Math.round(amount/total*100)}%</strong></div>`).join(''):'<div class="category-row"><i></i><span>确认发票后自动分类统计</span></div><div class="category-row"><span>了解每一笔费用的去向</span></div>'}</div>`;
  $('.donut').style.background = total ? `conic-gradient(${gradient})` : '#f0ebf7';
  $$('.category-row>i').forEach((el,i)=>el.style.background=colors[i%colors.length]);
}
function renderReport() {
  const currency = $('#currency').value;
  const totals = report.totals[currency]||{cents:0,count:0};
  const months = Object.entries(report.months[currency]||{}).sort((a,b)=>b[0].localeCompare(a[0]));
  $('#report-table').innerHTML = `<div class="report-total"><div>合计支出<strong>${esc(money(totals.cents))}</strong></div><div>已确认发票<strong>${totals.count} 张</strong></div></div><table><thead><tr><th>月份</th><th>币种</th><th class="align-right">支出金额</th></tr></thead><tbody>${months.length?months.map(([month,cents])=>`<tr><td>${month}</td><td>${currency}</td><td class="align-right amount">${esc(money(cents))}</td></tr>`).join(''):'<tr><td colspan="3">当前时段暂无已确认发票，核对确认后即可生成报表。</td></tr>'}</tbody></table>`;
}
function navigate(name) {
  currentPage = name;
  const titles={overview:['费用总览','每一笔支出，都心中有数','把繁琐的发票交给 Invoice，留更多时间给重要的事。'],invoices:['所有发票','所有凭证，井然有序','集中管理你的发票，查找、核对与导出都更轻松。'],reports:['分析报表','让数据，说清每一笔费用','按月、按年或自定义时段，了解支出变化与费用构成。']};
  $('#crumb').textContent = titles[name][0]; $('#page-title').innerHTML = esc(titles[name][1])+'<span>。</span>'; $('#page-description').textContent = titles[name][2];
  $$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===name));
  $('#overview-content').hidden = name!=='overview'; $('#charts').hidden = name==='invoices'; $('#invoice-panel').hidden = name==='reports'; $('#report-detail').hidden = name!=='reports';
  $('#table-title').firstChild.textContent = name==='invoices'?'所有发票 ':'最近的发票 ';
}
function openEditor(row = null) {
  editing = row; $('#invoice-form').reset(); $('#form-error').textContent = '';
  $('#editor-title').textContent = row ? (row.status==='confirmed'?'编辑发票':'核对发票') : '手动录入发票';
  const form = $('#invoice-form');
  for (const key of ['vendor','number','date','amount','currency','category','purpose']) form.elements[key].value = row?.[key] ?? ({currency:'CAD',category:'其他费用'}[key] || '');
  $('#record-id').textContent = row ? '系统 ID：'+row.id : '系统会自动生成唯一 ID';
  $('#delete-invoice').hidden = !row;
  $('#ocr-text').textContent = row?.ocr_text || '暂无识别文本'; $('#ocr-error').textContent = row?.ocr_error || '';
  const hasFile = !!row?.file_name; $('#original-link').hidden = !hasFile;
  $('#rerecognize').hidden = !hasFile; $('#rerecognize').disabled = row?.status === 'confirmed';
  $('#preview-error').textContent = ''; $('#preview-note').textContent = '';
  if (hasFile) {
    const url='/api/files/'+row.id; $('#original-link').href=url;
    $('#preview').innerHTML = `<a href="${url}" target="_blank" rel="noopener" aria-label="打开原始凭证"><img src="${url}/preview" alt="发票原始扫描件"></a>`;
    $('#preview img').onerror = () => { $('#preview-error').textContent = '预览加载失败，请点击「打开原件」查看；若仍失败，请检查服务器的附件存储。'; };
    $('#preview-note').textContent = row.file_name.toLowerCase().endsWith('.pdf') ? 'PDF 首页预览；点击「打开原件」可查看全部页面。' : '点击图片或「打开原件」可放大查看。';
  } else {$('#preview').textContent = '手动录入的发票暂无原始凭证'; $('#original-link').removeAttribute('href');}
  $('#editor').showModal();
}
let saving = false, recognizing = false;
async function save(status) {
  if(saving || recognizing) return;
  const data = Object.fromEntries(new FormData($('#invoice-form'))); data.status = status;
  $('#form-error').textContent = ''; saving = true;
  $('#confirm-invoice').disabled = $('#save-draft').disabled = true;
  try {
    await api(editing?'/api/invoices/'+editing.id:'/api/invoices',{method:editing?'PUT':'POST',body:JSON.stringify(data)});
    $('#editor').close(); toast(status==='confirmed'?'已确认入账，报表已更新':'已保存，稍后可继续核对'); await refresh();
  } catch(e) {$('#form-error').textContent=e.message;} finally {saving=false;$('#confirm-invoice').disabled=$('#save-draft').disabled=false;}
}
async function uploadFiles(files) {
  if(uploading) {toast('正在处理上一批文件，请稍候'); return;}
  if(!files.length) return;
  uploading=true; $('#upload-progress').hidden=false;
  let successes=[], failures=[];
  for(let i=0;i<files.length;i++) {
    $('#progress-title').textContent=`正在识别 ${i+1} / ${files.length}`; $('#progress-note').textContent=files[i].name;
    if(files[i].size>20*1024*1024) {failures.push(files[i].name+'：超过 20 MB'); continue;}
    const form=new FormData(); form.append('file',files[i]);
    try {successes.push(await api('/api/upload',{method:'POST',body:form}));} catch(e) {failures.push(files[i].name+'：'+e.message);}
  }
  uploading=false; $('#upload-progress').hidden=true; $('#file-input').value=''; await refresh();
  if(successes.length) {toast(`已保存 ${successes.length} 张发票，核对后即可入账`); if(successes.length===1) openEditor(successes[0]); else {navigate('invoices');$('#status').value='pending';page=1;await refresh();}}
  if(failures.length) toast(failures.join('；'),true);
}
function chooseFiles() {if(!uploading) $('#file-input').click(); else toast('正在识别，请稍候');}
function changePeriod(period) {
  $$('[data-period]').forEach(b=>b.classList.toggle('selected',b.dataset.period===period));
  $('#date-fields').hidden = period!=='custom';
  if(period==='custom') return;
  const now=new Date(), year=now.getFullYear(), month=String(now.getMonth()+1).padStart(2,'0');
  $('#start').value = period==='all'?'':period==='year'?`${year}-01-01`:`${year}-${month}-01`;
  $('#end').value = period==='all'?'':period==='year'?`${year}-12-31`:`${year}-${month}-${new Date(year,now.getMonth()+1,0).getDate()}`;
  page=1;refresh();
}
$$('[data-page]').forEach(b=>b.onclick=()=>navigate(b.dataset.page));
$$('[data-period]').forEach(b=>b.onclick=()=>changePeriod(b.dataset.period));
for (const id of ['upload-top','upload-banner','empty-upload']) $('#'+id).onclick=e=>{e.stopPropagation();chooseFiles();};
$('#drop-zone').onclick=chooseFiles;
$('#drop-zone').onkeydown=e=>{if(e.target===e.currentTarget && ['Enter',' '].includes(e.key)){e.preventDefault();chooseFiles();}};
for(const evt of ['dragover','dragenter']) $('#drop-zone').addEventListener(evt,e=>{e.preventDefault();$('#drop-zone').classList.add('dragging');});
$('#drop-zone').ondragleave=()=>$('#drop-zone').classList.remove('dragging');
$('#drop-zone').ondrop=e=>{e.preventDefault();$('#drop-zone').classList.remove('dragging');uploadFiles([...e.dataTransfer.files]);};
$('#file-input').onchange=e=>uploadFiles([...e.target.files]);
$('#manual').onclick=()=>openEditor(); $('#close-editor').onclick=()=>{if(!saving && !recognizing)$('#editor').close();};
$('#invoice-form').onsubmit=e=>{e.preventDefault();save('confirmed');};
$('#save-draft').onclick=()=>save('pending');
$('#delete-invoice').onclick=()=>{if(!recognizing && !saving)$('#delete-dialog').showModal();};
$('#cancel-delete').onclick=()=>$('#delete-dialog').close();
$('#confirm-delete').onclick=async()=>{
  $('#confirm-delete').disabled=true;
  try {await api('/api/invoices/'+editing.id,{method:'DELETE'});$('#delete-dialog').close();$('#editor').close();toast('发票已删除');await refresh();}catch(e){toast(e.message,true);}finally{$('#confirm-delete').disabled=false;}
};
$('#rerecognize').onclick = async () => {
  if (!editing || recognizing || saving) return;
  recognizing = true;
  $('#rerecognize').disabled = $('#confirm-invoice').disabled = $('#save-draft').disabled = true;
  $('#form-error').textContent = '正在重新识别，请稍候…';
  const id = editing.id;
  try {
    const row = await api('/api/invoices/'+id+'/recognize', {method:'POST'});
    // Keep unsaved edits in the form as well as fields already saved on the server.
    for (const key of ['vendor','number','date','amount','purpose']) {
      const field = $('#invoice-form').elements[key];
      if (!field.value || (key === 'amount' && Number(field.value) === 0)) field.value = row[key];
    }
    if ($('#form-category').value === '其他费用') $('#form-category').value = row.category;
    editing = row;
    $('#ocr-text').textContent = row.ocr_text || '暂无识别文本'; $('#ocr-error').textContent = row.ocr_error;
    $('#form-error').textContent = ''; toast('已补充缺失信息，请对照原件核对'); await refresh();
  } catch (e) { $('#form-error').textContent = e.message; }
  finally {
    recognizing = false;
    $('#rerecognize').disabled = $('#confirm-invoice').disabled = $('#save-draft').disabled = false;
  }
};
$('#editor').addEventListener('cancel', e => {if(saving || recognizing) e.preventDefault();});
$('#review-pending').onclick=()=>{navigate('invoices');$('#status').value='pending';page=1;refresh();};
$('#prev').onclick=()=>{page--;renderTable();}; $('#next').onclick=()=>{page++;renderTable();};
for(const id of ['currency','status','category','sort','start','end']) $('#'+id).onchange=()=>{page=1;refresh();};
let searchTimer; $('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;refresh();},200);};
$('#export').onclick=()=>{window.location.href='/api/export?'+params();};
$('#export-report').onclick=()=>{const p=params(false);p.set('mode','report');window.location.href='/api/export?'+p;};
$('#today').textContent=new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});
(async()=>{try {config=await api('/api/config');for(const category of config.categories){$('#category').add(new Option(category,category));$('#form-category').add(new Option(category,category));}await refresh();}catch(e){toast(e.message,true);}})();
