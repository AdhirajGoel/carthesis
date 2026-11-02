(function (root) {
  'use strict';
  const defaults = {daily:30,years:5,gas:4,down:3000,term:60,tax:7,ins:1800,fees:300,reg:250,infl:2};
  const components=['purchase','interest','fuel','insurance','maintenance','registration','resale'];
  function number(x,name,min=0,max=1e9) {
    if(typeof x!=='number'||!Number.isFinite(x)||x<min||x>max) throw Error(`${name} must be between ${min} and ${max}.`);
    return x;
  }
  function validate(s) {
    if(!s||s.version!==1||!Array.isArray(s.vehicles)||!s.vehicles.length||s.vehicles.length>200) throw Error('Expected a version 1 comparison with 1–200 vehicles.');
    const enabled=s.enabled===undefined?[...components]:s.enabled;
    if(!Array.isArray(enabled)||enabled.some(k=>!components.includes(k))||new Set(enabled).size!==enabled.length)throw Error('Cost components must be a list of unique supported names.');
    const preferencePremium=number(s.preferencePremium===undefined?0:s.preferencePremium,'Extra savings required to choose used',0);
    const p={...defaults,...s.assumptions};
    for(const k of Object.keys(defaults)) number(p[k],k,0);
    number(p.years,'Ownership years',1/12,30);number(p.term,'Loan term',1,120);
    if(!Number.isInteger(p.term)||Math.abs(p.years*12-Math.round(p.years*12))>1e-7)throw Error('Use a whole loan term and an ownership horizon in whole months.');
    number(p.tax,'Sales tax',0,100);number(p.infl,'Inflation',0,100);
    const seen=new Set();
    const vehicles=s.vehicles.map(v=>{
      if(!v||typeof v.id!=='string'||!v.id||seen.has(v.id))throw Error('Vehicle IDs must be unique, nonempty strings.');seen.add(v.id);
      for(const k of ['name','brand','model','body','power','condition'])if(typeof v[k]!=='string'||!v[k].trim()||v[k].length>200)throw Error(`Vehicle ${k} is required (up to 200 characters).`);
      if(!['Gas','Hybrid'].includes(v.power))throw Error('Use Gas or Hybrid for the fuel-cost model.');
      number(v.price,'Price',0.01);number(v.mpg,'MPG',0.01,300);number(v.apr,'APR',0,100);
      number(v.r5,'Five-year resale fraction',0,1);number(v.r10,'Ten-year resale fraction',0,v.r5);
      number(v.maint,'Annual maintenance and repairs',0);
      if(v.insurance!=null)number(v.insurance,'Annual insurance',0);
      if(v.term!=null){number(v.term,'Vehicle loan term',1,120);if(!Number.isInteger(v.term))throw Error('Loan term must be whole months.');}
      if(v.payment!=null)number(v.payment,'Payment',0.01);
      if(v.dealEdge!=null)number(v.dealEdge,'Deal edge',-10000,100);
      for(const k of ['pair','source','checkedAt','url'])if(v[k]!=null&&(typeof v[k]!=='string'||v[k].length>2000))throw Error(`Invalid ${k}.`);
      return {...v};
    });
    return {version:1,title:typeof s.title==='string'?s.title:'Comparison',assumptions:p,vehicles,enabled:[...enabled],preferencePremium};
  }
  function minimum(principal,apr,term){const r=apr/1200;return principal===0?0:r===0?principal/term:principal*r/(1-Math.pow(1+r,-term));}
  function loan(principal,apr,term,payment,horizon){
    const required=minimum(principal,apr,term),pay=payment??required;
    if(principal===0)return {required:0,payment:0,interest:0,totalInterest:0,months:0,balance:0,balances:[0]};
    if(pay+1e-7<required)throw Error('The payment is below the contractual minimum.');
    let balance=principal,total=0,held=0,atSale=principal,months=0;const balances=[principal];
    while(balance>0.000001&&months<term+1){
      const interest=balance*apr/1200;
      balance=Math.max(0,balance+interest-pay);total+=interest;months++;
      if(months<=horizon){held+=interest;atSale=balance;}
      balances.push(balance);
    }
    if(balance>0.01)throw Error('Loan did not amortize.');
    return {required,payment:pay,interest:held,totalInterest:total,months,balance:horizon>=months?0:atSale,balances};
  }
  function growth(years,rate){const full=Math.floor(years),part=years-full;const r=rate/100;return (r===0?full:(Math.pow(1+r,full)-1)/r)+part*Math.pow(1+r,full);}
  function retained(years,r5,r10){if(years<=5)return 1+(r5-1)*years/5;if(years<=10)return r5+(r10-r5)*(years-5)/5;return Math.max(0,r10+(r10-r5)*(years-10)/5);}
  function compute(v,p,enabled=components){
    const purchase=v.price*(1+p.tax/100)+p.fees,principal=Math.max(0,purchase-p.down);
    const financing=loan(principal,v.apr,v.term??p.term,v.payment,Math.round(p.years*12));
    const factor=growth(p.years,p.infl),annualMiles=p.daily*365;
    const costs={purchase,interest:financing.interest,fuel:annualMiles/v.mpg*p.gas*factor,insurance:(v.insurance??p.ins)*factor,maintenance:v.maint*factor,registration:p.reg*factor,resale:v.price*retained(p.years,v.r5,v.r10)};
    const net=components.filter(k=>enabled.includes(k)).reduce((sum,k)=>sum+(k==='resale'?-costs[k]:costs[k]),0);
    return {...costs,net,perYear:net/p.years,perMonth:net/(p.years*12),perMile:annualMiles?net/(p.years*annualMiles):null,annualFuel:annualMiles/v.mpg*p.gas,financing};
  }
  function hybrids(vehicles,p,enabled=components){
    const result=[];
    for(const h of vehicles.filter(v=>v.power==='Hybrid'&&v.pair)){
      const g=vehicles.find(v=>v.power==='Gas'&&v.pair===h.pair);
      if(!g)continue;
      const hc=compute(h,p,enabled),gc=compute(g,p,enabled),premium=hc.purchase-gc.purchase,savings=gc.annualFuel-hc.annualFuel;
      result.push({gas:g,hybrid:h,netSavings:gc.net-hc.net,premium,annualFuelSavings:savings,fuelPayback:premium<=0?0:savings>0?premium/savings:null});
    }
    return result;
  }
  function financingMargin(used,newCar,p,term,apr,premium=0){
    number(premium,'New-car preference premium',0);
    function total(v,months,rate){const price=v.price*(1+p.tax/100)+p.fees;return price+loan(Math.max(0,price-p.down),rate,months,null,months).totalInterest;}
    return total(newCar,newCar.term??p.term,newCar.apr)-total(used,term,apr)-premium;
  }
  function crossover(used,newCar,p,term,premium=0){
    if(financingMargin(used,newCar,p,term,0,premium)<0)return {kind:'below-zero'};
    if(financingMargin(used,newCar,p,term,100,premium)>0)return {kind:'above-range'};
    let low=0,high=100;
    for(let i=0;i<60;i++){const mid=(low+high)/2;if(financingMargin(used,newCar,p,term,mid,premium)>0)low=mid;else high=mid;}
    return {kind:'rate',rate:(low+high)/2};
  }
  const api={defaults,components,validate,minimum,loan,growth,retained,compute,hybrids,financingMargin,crossover};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CarThesis=api;
})(globalThis);
