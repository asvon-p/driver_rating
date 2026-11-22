"use strict";
const el=id=>document.getElementById(id);
const colors=["#398361","#b3523f","#cc7846","#9b493f","#b16e45","#a48c3c","#a18e4e","#b76744","#a77464","#84955c"];
let selected=null, objectUrl=null, ready=false, busy=false, generation=0;
const secs=ms=>(ms/1000).toLocaleString("ko-KR",{maximumFractionDigits:3})+"초";
function error(message=""){el("error").textContent=message;el("error").hidden=!message}
function controls(){el("submit").disabled=!ready||!selected||busy;el("file").disabled=busy}
async function api(url,opts){
  const res=await fetch(url,{cache:"no-store",...opts});
  const info=await res.json();
  if(!res.ok){const err=new Error(typeof info.detail==="string"?info.detail:"요청을 처리할 수 없습니다.");err.status=res.status;throw err}
  return info;
}
async function health(){
  try{const data=await api("/health");ready=data.state==="ready";
    el("health").textContent=ready?"분석 준비 완료":data.state==="error"?"모델 연결 실패":"모델 준비 중";
    el("model").textContent=ready?data.model.name+" · "+data.model.device.toUpperCase():data.message;
  }catch{ready=false;el("health").textContent="서버 연결 대기"}
  el("health").classList.toggle("ready",ready);controls();
  setTimeout(health,ready?12000:2000);
}
function choose(file){
  if(!file||busy)return;
  selected=null;error();if(objectUrl)URL.revokeObjectURL(objectUrl);
  el("preview").hidden=true;el("preview").removeAttribute("src");el("filename").textContent=file.name;
  if(file.size>100000000){error("100 MB 이하 영상을 선택해 주세요.")}
  else{selected=file;objectUrl=URL.createObjectURL(file);el("preview").src=objectUrl;el("preview").hidden=false}
  controls();
}
el("file").addEventListener("change",e=>choose(e.target.files[0]));
el("drop").addEventListener("dragover",e=>{e.preventDefault();el("drop").classList.add("over")});
el("drop").addEventListener("dragleave",()=>el("drop").classList.remove("over"));
el("drop").addEventListener("drop",e=>{e.preventDefault();el("drop").classList.remove("over");choose(e.dataTransfer.files[0])});
function showResult(r){
  const held=r.score===null;
  el("score").textContent=held?"판정 보류":r.score.toFixed(1)+"점";
  el("score-desc").textContent=held?"최소 판정 기준 미달":"인정된 시간 기준 · 100점 만점";
  el("coverage").textContent=r.coverage.toFixed(1)+"%";
  el("time").textContent=secs(r.duration_ms)+" 중 "+secs(r.valid_ms)+" 판정";
  const top=[...r.classes].sort((a,b)=>(b.risk*b.seconds)-(a.risk*a.seconds))[0];
  el("explanation").textContent=held?r.hold_reasons.join(" "):top.deduction>0?
    "가장 큰 감점 요인은 "+top.name+"입니다. "+secs(top.seconds*1000)+" 동안 "+top.deduction.toFixed(1)+"점이 감점됐습니다.":"판정된 구간에서 감점이 발생하지 않았습니다.";
  el("classes").replaceChildren();
  for(const c of r.classes.filter(c=>c.seconds>0)){
    const row=el("classes").insertRow();for(const value of [c.name,secs(c.seconds*1000),c.risk,c.deduction.toFixed(1)+"점"])row.insertCell().textContent=value;
  }
  if(!r.valid_ms){const cell=el("classes").insertRow().insertCell();cell.colSpan=4;cell.textContent="판정된 자세가 없습니다."}
  el("segments").replaceChildren();el("timeline").replaceChildren();
  for(const s of r.segments){
    const idx=s.label===null?null:Number(s.label.slice(1));
    const label=idx===null?"미판정 · "+r.reason_names[s.reason]:r.classes[idx].name;
    const text=secs(s.start_ms)+" – "+secs(s.end_ms)+"  "+label;
    const item=document.createElement("li");item.textContent=text;el("segments").append(item);
    const bar=document.createElement("span");bar.style.flexGrow=String(s.end_ms-s.start_ms);bar.style.background=idx===null?"#c4cec5":colors[idx];bar.title=text;el("timeline").append(bar);
  }
  const reasons=Object.entries(r.excluded_by_reason).filter(([,ms])=>ms).map(([key,ms])=>r.reason_names[key]+" "+secs(ms));
  el("excluded").textContent="제외 시간 "+secs(r.excluded_ms)+(reasons.length?" · "+reasons.join(", "):"");
  el("versions").textContent=r.model.name+" · 모델 "+r.model.version+" · 점수 정책 "+r.policy;
  el("progress-section").hidden=true;el("results").hidden=false;
}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function watch(id,token){
  el("progress-section").hidden=false;
  while(token===generation){
    try{
      const j=await api("/analyses/"+id);if(token!==generation)return;
      if(j.state==="failed"){error(j.error);el("progress-section").hidden=true;break}
      if(["completed","insufficient"].includes(j.state)){showResult(await api("/analyses/"+id+"/result"));break}
      el("phase").textContent=j.state==="queued"?"분석 대기 중":"자세 분석 중";
      el("progress-text").textContent=secs(j.processed_ms)+" / "+secs(j.duration_ms);
      el("progress").value=100*j.processed_ms/j.duration_ms;
    }catch(err){
      if(err.status===404){localStorage.removeItem("driver.analysis");error(err.message);el("progress-section").hidden=true;break}
      el("phase").textContent="서버 재연결 중";
    }
    await sleep(1100);
  }
  busy=false;controls();
}
el("form").addEventListener("submit",async e=>{
  e.preventDefault();if(!selected||busy||!ready)return;
  error();busy=true;controls();el("results").hidden=true;el("progress-section").hidden=false;
  el("phase").textContent="영상 업로드 중";el("progress-text").textContent="";el("progress").removeAttribute("value");
  const form=new FormData();form.append("video",selected);
  try{const job=await api("/analyses",{method:"POST",body:form});localStorage.setItem("driver.analysis",job.id);await watch(job.id,++generation)}
  catch(err){error(err.message);el("progress-section").hidden=true;busy=false;controls()}
});
health();
const previous=localStorage.getItem("driver.analysis");
if(previous){busy=true;controls();watch(previous,++generation)}
