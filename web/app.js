'use strict';
const M=LotLogic;
let state=M.validate(DEMO), tab='Overview', selected=null, sort='net', ascending=true;
const excluded=new Set(), filters={brand:'All',power:'All',condition:'All'};
const $=id=>document.getElementById(id);
const money=x=>Number.isFinite(x)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(x):'—';
function node(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,action,cls='btn'){const b=node('button',text,cls);b.type='button';b.onclick=action;return b;}
function error(e){$('error').hidden=!e;$('error').textContent=e?e.message:'';}
function card(title,caption){const c=node('section',null,'card');c.append(node('h3',title));if(caption)c.append(node('p',caption,'cap'));return c;}
function field(label,value,change,options={}){
  const wrap=node('div',null,'ctrl'),l=node('label',label),input=node('input',null,'num');
  input.type='number';input.value=value;input.step=options.step??'any';input.min=options.min??0;input.setAttribute('aria-label',label);
  l.append(input);wrap.append(l);input.onchange=()=>{
    const previous=structuredClone(state);
    try{
      if(input.value==='')throw Error(`${label} is required.`);
      change(Number(input.value));
      const checked=M.validate(state);
      checked.vehicles.forEach(v=>M.compute(v,checked.assumptions,checked.enabled));
      state=checked;controls();render();
    }catch(e){state=previous;controls();render();error(e);}
  };return wrap;
}
const inputNames={daily:'Miles per day',years:'Ownership years',gas:'Gas price ($/gallon)',down:'Down payment ($)',term:'Loan term (months)',tax:'Sales tax (%)',ins:'Default insurance ($/year)',fees:'One-time fees ($)',reg:'Registration ($/year)',infl:'Annual operating-cost inflation (%)'};
function controls(){
  $('inputs').replaceChildren();
  for(const [k,label] of Object.entries(inputNames))$('inputs').append(field(label,state.assumptions[k],n=>{state.assumptions[k]=n;},{step:k==='years'?1/12:k==='term'?1:'any'}));
  $('components').replaceChildren();
  for(const k of M.components){const b=button(k,()=>{state.enabled=state.enabled.includes(k)?state.enabled.filter(x=>x!==k):[...state.enabled,k];controls();render();},'chip'+(state.enabled.includes(k)?' on':''));b.setAttribute('aria-pressed',state.enabled.includes(k));$('components').append(b);}
  $('filters').replaceChildren();
  for(const k of Object.keys(filters)){
    const label=node('label',k),select=node('select',null,'num');select.setAttribute('aria-label',`Filter ${k}`);
    for(const value of ['All',...new Set(state.vehicles.map(v=>v[k]))]){const o=node('option',value);o.value=value;select.append(o);}
    if(![...select.options].some(o=>o.value===filters[k]))filters[k]='All';select.value=filters[k];
    select.onchange=()=>{filters[k]=select.value;render();};label.append(select);$('filters').append(label);
  }
}
function visible(){return state.vehicles.filter(v=>!excluded.has(v.id)&&Object.entries(filters).every(([k,value])=>value==='All'||v[k]===value));}
function table(headers,rows){const wrap=node('div',null,'tablewrap'),t=node('table'),head=node('thead'),tr=node('tr');
  headers.forEach(h=>{const th=node('th',null,'l');th.scope='col';th.append(typeof h==='string'?document.createTextNode(h):h);tr.append(th);});head.append(tr);t.append(head);
  const body=node('tbody');rows.forEach(cells=>{const tr=node('tr');cells.forEach(x=>{const td=node('td',null,'l');td.append(x instanceof Node?x:document.createTextNode(x??'—'));tr.append(td);});body.append(tr);});t.append(body);wrap.append(t);return wrap;}
function bars(rows){const box=node('div');const max=Math.max(...rows.map(r=>Math.abs(r.value)),1);
  rows.forEach(r=>{const line=node('div',null,'bar-row'),track=node('div',null,'bar-wrap'),bar=node('div',null,'bar'),value=node('span',money(r.value),'bar-val');bar.style.width=`${Math.abs(r.value)/max*100}%`;bar.style.background=r.hybrid?'var(--hyb)':'var(--accent)';track.append(bar,value);line.append(node('div',r.name,'lab'),track);box.append(line);});return box;}
function overview(root){
  const rows=visible().map(v=>({v,c:M.compute(v,state.assumptions,state.enabled)})).sort((a,b)=>a.c.net-b.c.net);
  if(!rows.length){root.append(node('p','No vehicles match. Adjust filters or restore excluded vehicles.'));return;}
  const kpis=node('div',null,'kpis');
  for(const [label,value,detail] of [['Lowest ownership cost',money(rows[0].c.net),rows[0].v.name],['Equivalent monthly cost',money(rows[0].c.perMonth),'Ownership cost spread over the holding period'],['Ownership horizon',`${state.assumptions.years} years`,`${Math.round(state.assumptions.daily*365).toLocaleString()} miles/year`]]){
    const k=node('div',null,'kpi');k.append(node('div',label,'k'),node('div',value,'v'),node('div',detail,'d'));kpis.append(k);
  }root.append(kpis);
  const summary=card('What changes the decision','Purchase price + interest during ownership + operating costs − resale value. Change the assumptions to see whether the ranking holds.');
  summary.append(bars(rows.map(r=>({name:r.v.name,value:r.c.net,hybrid:r.v.power==='Hybrid'}))));root.append(summary);
  const explanation=card('Compare the deal and the ownership cost');
  explanation.append(table(['Vehicle','Asking price','Below local peer median','Ownership cost'],rows.map(r=>[r.v.name,money(r.v.price),r.v.dealEdge==null?'Not assessed':`${r.v.dealEdge.toFixed(1)}%`,money(r.c.net)])));root.append(explanation);
}
function comparison(root){
  const rows=visible().map(v=>({v,c:M.compute(v,state.assumptions,state.enabled)}));rows.sort((a,b)=>((a.c[sort]??a.v[sort])-(b.c[sort]??b.v[sort]))*(ascending?1:-1));
  const columns=[['price','Price'],['purchase','Out-the-door'],['interest','Interest'],['fuel','Fuel'],['insurance','Insurance'],['maintenance','Maintenance'],['registration','Registration'],['resale','Resale'],['net','Net cost']];
  const c=card('Ownership cost, component by component','Click a column to sort. Edit a vehicle to change its quote, operating costs, resale assumptions, or monthly payment.');
  c.append(table(['Vehicle',...columns.map(([k,label])=>button(label,()=>{ascending=sort===k?!ascending:true;sort=k;render();},'chip')),'Actions'],rows.map(({v,c})=>{
    const actions=node('div');actions.append(button('Edit',()=>edit(v)),button('Exclude',()=>{excluded.add(v.id);render();}));
    return [v.name,...columns.map(([k])=>money(c[k]??v[k])),actions];
  })));root.append(c);
  if(excluded.size)root.append(button('Restore excluded vehicles',()=>{excluded.clear();render();}));
  const cost=card('Cost breakdown','All components are shown here. Components excluded in the sidebar do not enter the net-cost total.');
  for(const {v,c} of rows){const d=node('details');d.append(node('summary',v.name));d.append(bars(M.components.map(k=>({name:k,value:k==='resale'?-c[k]:c[k]}))));cost.append(d);}root.append(cost);
}
function market(root){
  const c=card('Local price index','Positive deal edge means the asking price is below the supported five-peer median. Negative means above. The index uses comparable vehicles in the searched area, balanced across sellers.');
  c.append(table(['Vehicle','Asking price','Deal edge','Implied peer benchmark','Evidence'],visible().map(v=>[v.name,money(v.price),v.dealEdge==null?'Not assessed':`${v.dealEdge.toFixed(2)}%`,v.dealEdge==null||v.dealEdge>=100?'—':money(v.price/(1-v.dealEdge/100)),v.marketSource||'Run local comparisons before treating a listing as a deal.'])));root.append(c);
  root.append(node('p','The benchmark describes asking prices, not completed transactions. An unsupported comparison stays unscored.','foot'));
}
function hybrid(root){
  const c=card('Does the hybrid premium pay back?','Pairs are matched explicitly in the comparison file. Fuel-only payback is separate from the full ownership-cost comparison, which includes financing and every selected component.');
  const pairs=M.hybrids(visible(),state.assumptions,state.enabled);
  c.append(table(['Gas → hybrid','Upfront premium','Annual fuel saved','Fuel-only payback','Net ownership savings'],pairs.map(p=>[`${p.gas.name} → ${p.hybrid.name}`,money(p.premium),money(p.annualFuelSavings),p.fuelPayback==null?'No fuel payback':`${p.fuelPayback.toFixed(1)} years`,money(p.netSavings)])));
  if(!pairs.length)c.append(node('p','No matched gas/hybrid pair is currently shown.'));root.append(c);
}
function financing(root){
  const rows=visible();if(!rows.length)return;
  const v=rows.find(v=>v.id===selected)||rows[0];selected=v.id;
  const select=node('select',null,'sel');select.setAttribute('aria-label','Loan vehicle');rows.forEach(v=>{const o=node('option',v.name);o.value=v.id;select.append(o);});select.value=v.id;select.onchange=()=>{selected=select.value;render();};root.append(select);
  const p=state.assumptions,c=M.compute(v,p,state.enabled),term=v.term??p.term,base=M.loan(Math.max(0,c.purchase-p.down),v.apr,term,null,Math.round(p.years*12));
  const top=card('Loan and prepayment','Monthly simple-interest amortization. Extra payments shorten the loan; the ownership comparison counts only interest through the sale month.');
  top.append(field('Monthly payment ($)',c.financing.payment,n=>{v.payment=n;M.compute(v,p,state.enabled);}));
  top.append(button('Use contractual minimum',()=>{delete v.payment;render();}));
  top.append(table(['Measure','Value'],[['APR',`${v.apr}%`],['Contractual minimum',money(c.financing.required)],['Payoff time',`${c.financing.months} months`],['Interest through sale',money(c.interest)],['Full-loan interest',money(c.financing.totalInterest)],['Full-loan interest saved by prepayment',money(base.totalInterest-c.financing.totalInterest)],['Loan balance at sale',money(c.financing.balance)]]));root.append(top);
  const schedule=card('Remaining principal');schedule.append(bars(c.financing.balances.filter((_,i)=>i%12===0).map((value,i)=>({name:`Year ${i}`,value}))));root.append(schedule);
}
let crossoverUsed=null,crossoverNew=null;
function financingCrossover(root){
  const used=visible().filter(v=>v.condition.toLowerCase()==='used'), fresh=visible().filter(v=>v.condition.toLowerCase()==='new');
  if(!used.length||!fresh.length){root.append(node('p','Show at least one New and one Used vehicle to compare financing.'));return;}
  const u=used.find(v=>v.id===crossoverUsed)||used[0],n=fresh.find(v=>v.id===crossoverNew)||fresh[0];
  crossoverUsed=u.id;crossoverNew=n.id;
  const c=card('When does the used-car APR erase the price advantage?','This view compares purchase cash through full loan payoff. Use Ownership costs for fuel, insurance, maintenance, and resale.');
  for(const [label,cars,chosen,change] of [['Used vehicle',used,u.id,id=>crossoverUsed=id],['New vehicle',fresh,n.id,id=>crossoverNew=id]]){
    const l=node('label',label),select=node('select',null,'num');select.setAttribute('aria-label',label);
    cars.forEach(v=>{const o=node('option',v.name);o.value=v.id;select.append(o);});select.value=chosen;select.onchange=()=>{change(select.value);render();};l.append(select);c.append(l);
  }
  c.append(field('Extra savings required to choose used ($)',state.preferencePremium,value=>{if(value<0)throw Error('Required savings must be nonnegative.');state.preferencePremium=value;}));
  const terms=[36,48,60];
  c.append(table(['Used term','APR at cash parity','APR after required savings'],terms.map(term=>{
    const format=x=>x.kind==='rate'?`${x.rate.toFixed(2)}%`:x.kind==='below-zero'?'New wins even at 0%':'Used wins through 100%';
    return [`${term} months`,format(M.crossover(u,n,state.assumptions,term)),format(M.crossover(u,n,state.assumptions,term,state.preferencePremium))];
  })));
  c.append(table(['Used APR',...terms.map(t=>`${t}-month savings after preference`)], [0,3,6,9,12,15].map(apr=>[`${apr}%`,...terms.map(t=>money(M.financingMargin(u,n,state.assumptions,t,apr,state.preferencePremium)))])));
  root.append(c);
}
function assumptions(root){const c=card('Vehicle assumptions and evidence','Retention fractions describe resale relative to today’s purchase price, measured from purchase. Replace demo estimates with vehicle-specific sources and buyer quotes.');
  c.append(table(['Vehicle','APR / term','MPG','Insurance/year','Maintenance/year','Resale in 5 / 10 years','Source / checked',''],visible().map(v=>[v.name,`${v.apr}% / ${v.term??state.assumptions.term} mo`,v.mpg.toString(),money(v.insurance??state.assumptions.ins),money(v.maint),`${(v.r5*100).toFixed(0)}% / ${(v.r10*100).toFixed(0)}%`,`${v.source||'No source recorded'} ${v.checkedAt||''}`,button('Edit',()=>edit(v))])));root.append(c);}
function edit(vehicle){
  const existing=vehicle!=null,v=structuredClone(vehicle||{id:`vehicle-${Date.now()}`,name:'New vehicle',brand:'Example',model:'Model',body:'Sedan',power:'Gas',condition:'New',price:30000,mpg:30,apr:5,r5:.55,r10:.3,maint:600,source:'User estimate'});
  const dialog=node('dialog');dialog.style.cssText='max-width:850px;width:90%;border:1px solid #ccc;border-radius:12px';
  const form=node('form'),grid=node('div',null,'vehicle-form');form.append(node('h2',existing?'Edit vehicle':'Add vehicle'));
  const fields={name:'Vehicle name',brand:'Brand',model:'Model',body:'Body',power:'Gas or Hybrid',condition:'New or Used',price:'Purchase price',mpg:'MPG',apr:'APR (%)',term:'Loan term (optional)',insurance:'Insurance/year (optional)',maint:'Maintenance and repairs/year',r5:'Resale fraction at year 5',r10:'Resale fraction at year 10',pair:'Gas/hybrid pair ID (optional)',source:'Source or quote reference',checkedAt:'Checked date (optional)'};
  const numbers=new Set(['price','mpg','apr','term','insurance','maint','r5','r10']);
  for(const [k,label] of Object.entries(fields)){const l=node('label',label),input=node('input',null,'num');input.name=k;input.value=v[k]??'';input.type=numbers.has(k)?'number':'text';if(numbers.has(k)){input.step='any';input.min=0;}l.append(input);grid.append(l);}
  const issue=node('p',null,'error');issue.hidden=true;issue.setAttribute('role','alert');form.append(grid,issue);
  const save=node('button','Save vehicle','btn');save.type='submit';form.append(save,button('Cancel',()=>dialog.close()));
  form.onsubmit=e=>{e.preventDefault();try{
    for(const [k,value] of new FormData(form)){if(value===''){delete v[k];continue;}v[k]=numbers.has(k)?Number(value):value;}
    if(existing&&['price','name','brand','model','body','power','condition'].some(k=>v[k]!==vehicle[k])){delete v.dealEdge;delete v.marketSource;}
    const next={...state,vehicles:existing?state.vehicles.map(x=>x.id===v.id?v:x):[...state.vehicles,v]};
    const checked=M.validate(next);checked.vehicles.forEach(x=>M.compute(x,checked.assumptions,checked.enabled));state=checked;controls();render();dialog.close();
  }catch(e){issue.textContent=e.message;issue.hidden=false;}};
  dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
}
const tabs={Overview:overview,'Local price index':market,'Ownership costs':comparison,'Gas vs hybrid':hybrid,'Loan & prepay':financing,'APR crossover':financingCrossover,Assumptions:assumptions};
for(const name of Object.keys(tabs)){const b=button(name,()=>{tab=name;render();},'tab');$('tabs').append(b);}
function render(){try{error(null);state=M.validate(state);$('title').textContent=tab;$('view').replaceChildren();for(const b of $('tabs').children){b.classList.toggle('on',b.textContent===tab);b.setAttribute('aria-current',b.textContent===tab?'page':'false');}tabs[tab]($('view'));}catch(e){error(e);}}
$('reset').onclick=()=>{state=M.validate(DEMO);excluded.clear();for(const k in filters)filters[k]='All';$('dataset').textContent='Synthetic demo · editable assumptions';controls();render();};
$('add').onclick=()=>edit(null);
$('choose-file').onclick=()=>$('import').click();
$('import').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>2e6)throw Error('Comparison files must be smaller than 2 MB.');const next=M.validate(JSON.parse(await file.text()));next.vehicles.forEach(v=>M.compute(v,next.assumptions,next.enabled));state=next;excluded.clear();$('dataset').textContent=state.title;controls();render();}catch(e){error(e);}finally{e.target.value='';}};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify(state,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
controls();render();
