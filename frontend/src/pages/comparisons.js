import {heading} from '../components/common/header.js';
import {esc,fmt,time} from '../utils/format.js';
import {status} from '../components/common/status.js';
import {table} from '../components/common/table.js';
import {loadPairs} from '../api/business.js';
import {store} from '../stores/business.js';

// 分页依据按比较任务缓存，避免刷新整页数据时丢失翻页位置。
export const pairCache={};

function approvedRuns(d){return d.runs.filter(r=>r.status==='completed'&&r.approved)}

function rateCard(title,info){
 if(!info)return `<div class="metrics"><div><small>${esc(title)}</small><strong>未发布</strong></div></div>`;
 return `<div class="metrics"><div><small>${esc(title)} · ${esc(info.name)}</small><strong>${fmt(info.overdue_rate.overdue_rate*100)}%</strong></div>
 <div><small>配对样本超时数</small><strong>${info.overdue_rate.overdue}/${info.overdue_rate.paired_samples}</strong></div>
 <div><small>冻结口径</small><strong>${esc(info.policy_version)}</strong></div></div>`;
}

function pairTable(comp,d){
 const cached=pairCache[comp.id];
 if(!cached)return `<p class="empty">配对完成后可查看逐对依据。</p>`;
 const rows=cached.items.map(it=>({
  'A底稿行号':it.line_a,'A开单时间':it.a.opened,'A优先级':it.a.priority,'A小组':it.a.department,
  'B底稿行号':it.line_b,'B开单时间':it.b.opened,'B优先级':it.b.priority,'B小组':it.b.department,
  '时间差(分钟)':Math.round(it.gap_seconds/60),'同优先级':it.same_priority?'是':'否',
 }));
 const start=cached.offset+1,end=Math.min(cached.offset+cached.limit,cached.total);
 return `${table(rows)}
 <div class="pager">
  <button class="toggle" data-page="prev" ${cached.offset===0?'disabled':''}>上一页</button>
  <span>第 ${start}-${end} 对 / 共 ${cached.total} 对</span>
  <button class="toggle" data-page="next" ${end>=cached.total?'disabled':''}>下一页</button>
  <button class="toggle" data-export-pairs="${comp.id}">导出全部配对依据 CSV</button>
 </div>`;
}

function comparisonDetail(comp,d){
 if(!comp)return '<p class="empty">选择或发起一次配对比较。仅可选择两份已复核报告的冻结底稿。</p>';
 const runs=approvedRuns(d);
 const option=id=>runs.map(r=>`<option value="${r.id}" ${r.id===id?'selected':''}>${esc(r.name)}（#${r.id}）</option>`).join('');
 return `<section class="report">
 <div class="report-top"><h2>${esc(comp.name)}</h2>${status(comp.status)} ${comp.published?'<span class="status">已发布</span>':'<span class="status" style="opacity:.6">未发布</span>'}</div>
 <p>代次 gen${comp.generation} · 创建于 ${time(comp.created)} · 报告 A #${comp.run_a_id} / 报告 B #${comp.run_b_id}</p>
 ${comp.error?`<p class="error">${esc(comp.error)}</p>`:''}
 ${comp.status==='completed'&&comp.result?`
 <p class="method">${esc(comp.result.basis)}</p>
 ${comp.result.warnings?.length?`<p class="warning">${comp.result.warnings.map(esc).join('；')}</p>`:''}
 <div class="metrics">
  <div><small>配对数</small><strong>${comp.result.stats.pair_count}</strong></div>
  <div><small>时间差合计(分钟)</small><strong>${Math.round(comp.result.stats.gap_seconds_total/60)}</strong></div>
  <div><small>A未配对</small><strong>${comp.result.stats.group_a_unpaired}</strong></div>
  <div><small>B未配对</small><strong>${comp.result.stats.group_b_unpaired}</strong></div>
 </div>
 <h3>A 组配对样本超时率</h3>${rateCard('报告A',comp.result.run_a)}
 <h3>B 组配对样本超时率</h3>${rateCard('报告B',comp.result.run_b)}
 ${!comp.published?'<p class="warning">发布闸门未通过：配对样本或超时率不齐备，暂不向业务阅读者发布。</p>':''}
 <details ${pairCache[comp.id]?'open':''}><summary>查看配对依据（按底稿行号定序）</summary>${pairTable(comp,d)}</details>
 `:comp.status==='failed'?'':'<p class="empty">计算进行中或排队中，页面将自动刷新。</p>'}
 ${store.session?.role==='viewer'?'':`<details><summary>改选报告 / 重新配对（旧代次结果作废）</summary>
 <form id="comparison-form" data-id="${comp.id}" class="inline-form">
  <label>比较名称<input name="name" value="${esc(comp.name)}" required minlength="2"></label>
  <label>报告 A（已复核）<select name="run_a_id">${option(comp.run_a_id)}</select></label>
  <label>报告 B（已复核）<select name="run_b_id">${option(comp.run_b_id)}</select></label>
  <button>改选并重算</button>
 </form></details>`}
 </section>`;
}

export function comparisons(c,d,selected){
 const list=d.comparisons||[];
 const comp=list.find(x=>x.id===selected)||list[0];
 const runs=approvedRuns(d);
 const readonly=(store.session?.role==='viewer');
 const canWrite=!readonly&&d.runs&&runs.length>=2;
 return heading('MATCHING DESK / 05','两组报告配对比较','同优先级、开单相隔30分钟内才可配对，每单用一次；配对数最多优先，并列取时间差总和最小。')+
 `<div class="run-layout"><aside class="run-list">
 ${canWrite?`<form id="comparison-new-form" class="new-compare">
 <label>比较名称<input name="name" value="组间超时率比较" required minlength="2"></label>
 <label>报告 A<select name="run_a_id">${runs.map(r=>`<option value="${r.id}">${esc(r.name)} #${r.id}</option>`).join('')}</select></label>
 <label>报告 B<select name="run_b_id">${runs.map((r,i)=>`<option value="${r.id}" ${i===1?'selected':''}>${esc(r.name)} #${r.id}</option>`).join('')}</select></label>
 <button>发起比较</button></form>`:readonly?'<p class="empty">只读视图：仅展示已发布的配对比较。</p>':'<p class="empty">至少需要两份已复核报告。</p>'}
 ${list.map(x=>`<button class="select-compare" data-id="${x.id}">${esc(x.name)} ${status(x.status)} ${x.published?'· 已发布':''}</button>`).join('')||'<p class="empty">尚无比较任务。</p>'}
 </aside><div>${comparisonDetail(comp,d)}</div></div>`;
}

export function bindComparisons(store,render,action,request,download){
 const newForm=document.querySelector('#comparison-new-form');
 if(newForm)newForm.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(newForm));
  b.run_a_id=Number(b.run_a_id);b.run_b_id=Number(b.run_b_id);
  const created=await request('/comparisons','POST',b);
  store.selected=created.id;await render();
 })};
 document.querySelectorAll('.select-compare').forEach(btn=>btn.onclick=()=>{store.selected=Number(btn.dataset.id);render()});
 const editForm=document.querySelector('#comparison-form[data-id]');
 if(editForm)editForm.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(editForm));
  b.run_a_id=Number(b.run_a_id);b.run_b_id=Number(b.run_b_id);
  const id=Number(editForm.dataset.id);
  const updated=await request('/comparisons/'+id,'PUT',b);
  delete pairCache[id];store.selected=updated.id;await render();
 })};
 const detail=store.data.comparisons.find(x=>x.id===store.selected)||store.data.comparisons[0];
 if(!detail)return;
 const wirePaging=()=>{
  document.querySelectorAll('[data-page]').forEach(btn=>btn.onclick=()=>action(async()=>{
   const cached=pairCache[detail.id];const offset=btn.dataset.page==='next'?cached.offset+cached.limit:Math.max(0,cached.offset-cached.limit);
   pairCache[detail.id]=await loadPairs(detail.id,offset,cached.limit);render();
  }));
  document.querySelectorAll('[data-export-pairs]').forEach(btn=>btn.onclick=()=>action(()=>download('/comparisons/'+btn.dataset.exportPairs+'/csv')));
 };
 if(detail.status==='completed'){
  if(pairCache[detail.id]){wirePaging();return}
  // 异步加载首页依据，完成后自行触发一次渲染，避免嵌套渲染。
  loadPairs(detail.id,0,10).then(page=>{pairCache[detail.id]=page;render();wirePaging()});
 }
}
