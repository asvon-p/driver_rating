const $ = (s, base = document) => base.querySelector(s);
const html = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const root = $("#workspace"), api = "/api/image-comparisons", roles = ["A","B"];
const names = ["일반 운전","오른손 문자 입력","오른손 통화","왼손 문자 입력","왼손 통화","라디오 조작","음료 마시기","뒤로 팔 뻗기","머리 손질·화장","동승자와 대화"];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let signed = false, generation = 0, draft = [], draftTitle = "", current = null, selected = 0, heatUrls = [], historyOffset = 0;
function notice(msg = "") { $("#message").textContent = msg; $("#message").hidden = !msg; }
function badge(g) { return g ? `<span class="badge ${g==="안전 운전"?"safe":g==="일반 운전"?"mid":"risk"}">${html(g)}</span>` : ""; }
function fmt(n) { return n == null ? "—" : n.toFixed(1); }
function points(n,r) {
  if(n == null) return "—";
  for (const v of [r.safe,r.ordinary]) if(n < v && Number(n.toFixed(1)) >= v) return `${v}점 미만`;
  return `${fmt(n)}점`;
}
function options(values, active) { return values.map(([v,t])=>`<option value="${html(v)}" ${String(v)===String(active)?"selected":""}>${html(t)}</option>`).join(""); }
function clearHeat() { heatUrls.forEach(URL.revokeObjectURL); heatUrls=[]; }
function clearDraft() { draft.forEach(p=>URL.revokeObjectURL(p.url)); draft=[]; draftTitle=""; }
function gate(on) {
  signed=on; $("#gate").hidden=on; $("#nav").hidden=!on;
  if(!on){generation++; clearHeat(); clearDraft(); current=null; root.replaceChildren();}
}
async function request(path, config={}) {
  const res=await fetch(path,{cache:"no-store",...config});
  if(!res.ok){
    const data=await res.json().catch(()=>({}));
    if(res.status===403 && path!=="/api/admin/session") gate(false);
    throw Error(typeof data.detail==="string"?data.detail:`요청 실패 (${res.status})`);
  }
  return res;
}
const json = async (path,config) => (await request(path,config)).json();
$("#login").onsubmit=async event=>{
  event.preventDefault(); const code=event.target.elements.code;
  try{await json("/api/admin/session",{method:"POST",headers:{"X-Demo-Code":code.value.trim()}}); code.value=""; $("#authError").textContent="";gate(true);route();}
  catch(e){$("#authError").textContent=e.message;}
};
$("#logout").onclick=async()=>{try{await request("/api/admin/session",{method:"DELETE"});gate(false);notice();}catch(e){notice(e.message);}};
window.addEventListener("hashchange",route);
async function route(){
  if(!signed)return;
  const token=++generation; clearHeat(); current=null; notice();
  const key=location.hash.slice(1)||"new";
  document.querySelectorAll("nav a").forEach(a=>{if(a.hash==="#"+(key==="new"?"new":"history"))a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");});
  try{
    if(key==="new") return renderUpload(token);
    root.textContent="결과를 불러오는 중…";
    if(key==="history") return await history(token);
    if(!/^case\/[a-f0-9-]{36}$/i.test(key)) { location.hash="new";return; }
    const id=key.slice(5); selected=0;
    while(token===generation){
      const data=await json(`${api}/${id}`);
      if(token!==generation)return;
      if(data.state==="completed"){current=data; renderCase();return;}
      if(data.state==="failed")throw Error(data.error||"분석 실패");
      root.innerHTML=`<div class="panel"><h2>${html(data.title)}</h2><p>사진 묶음을 분석하고 있습니다. ${data.processed} / ${data.photo_count}장</p><progress max="${data.photo_count}" value="${data.processed}"></progress><p><a href="#history">저장된 결과 열기</a></p></div>`;
      await wait(750);
    }
  }catch(e){if(token===generation){root.textContent="";notice(e.message);}}
}
function renderUpload(token){
  root.innerHTML=`<div class="top"><div><h1>새 사진 묶음 분석</h1><p class="muted">같은 운전자의 관찰로 가정하고, 오래된 사진부터 순서를 정하세요.</p></div><a class="button" href="#history">저장된 결과 열기</a></div>
  <div class="panel upload"><label>묶음 제목<input id="title" maxlength="120" placeholder="예: 정면 시점 운전 자세 비교" value="${html(draftTitle)}"></label>
  <label>JPG · PNG 사진 선택<input id="files" type="file" multiple accept="image/jpeg,image/png"></label><small>최대 24장 · 사진당 10 MiB, 전체 전송 48 MiB · 한 장만으로도 자세를 확인할 수 있습니다.</small>
  <div id="ordered"></div><div class="top"><span id="health" class="muted">모델 상태 확인 중…</span><button class="primary" id="submit" disabled>분석 실행</button></div></div>`;
  $("#title").oninput=e=>draftTitle=e.target.value;
  let ready=false, busy=false;
  function sync(){ $("#submit").disabled=!ready || busy || !draft.length; }
  function entries(){
    $("#ordered").innerHTML=draft.map((p,i)=>`<div class="entry" data-key="${p.id}"><img src="${p.url}" alt=""><div><strong>${i+1}. ${html(p.file.name)}</strong>
    <div class="fields"><label>카메라 시점<select data-field="view">${options(["미지정","기존 옆 시점","새 측면","정면"].map(x=>[x,x]),p.view)}</select></label>
    <label>검수 정답<select data-field="truth">${options([["","미확정"],...names.map((n,i)=>[i,`c${i} · ${n}`])],p.truth??"")}</select></label>
    <label>분석 대상<select data-field="exclude">${options([["","포함"],["운전자 없음","운전자 없음"],["10종 외 행동","10종 외 행동"]],p.exclude)}</select></label></div>
    <details><summary>사진 출처</summary><input data-field="source" maxlength="500" aria-label="사진 출처" value="${html(p.source)}"></details></div>
    <div class="order"><button data-action="up" ${i===0?"disabled":""} aria-label="위로 이동">↑</button> <button data-action="down" ${i===draft.length-1?"disabled":""} aria-label="아래로 이동">↓</button> <button data-action="remove">제거</button></div></div>`).join("");
    sync();
  }
  $("#ordered").oninput=e=>{const field=e.target.dataset.field;if(!field)return;const p=draft.find(x=>x.id===e.target.closest(".entry").dataset.key);p[field]=field==="truth"?(e.target.value===""?null:Number(e.target.value)):e.target.value;};
  $("#ordered").onclick=e=>{const action=e.target.dataset.action;if(!action || busy)return;
    const i=draft.findIndex(p=>p.id===e.target.closest(".entry").dataset.key);const [p]=draft.splice(i,1);
    if(action==="remove")URL.revokeObjectURL(p.url);else draft.splice(action==="up"?i-1:i+1,0,p);entries();};
  $("#files").onchange=e=>{
    const list=[...e.target.files]; e.target.value="";
    if(draft.length+list.length>24 || list.some(f=>f.size>10*1024*1024) || [...draft.map(p=>p.file),...list].reduce((s,f)=>s+f.size,0)>=48*1024*1024){notice("사진 수 또는 업로드 용량이 한도를 초과합니다.");return;}
    for(const file of list)draft.push({id:crypto.randomUUID(),file,url:URL.createObjectURL(file),view:"미지정",truth:null,source:"",exclude:""});
    notice();entries();
  };
  $("#submit").onclick=async()=>{
    if(busy)return;busy=true;sync();notice("사진을 전송하고 있습니다.");
    const form=new FormData();form.set("title",draftTitle.trim()||"사진 묶음");
    form.set("order",JSON.stringify(draft.map(p=>({id:p.id,name:p.file.name,view:p.view,truth:p.truth,source:p.source,exclude:p.exclude}))));
    draft.forEach(p=>form.append("files",p.file,p.id));
    try{const data=await json(api,{method:"POST",body:form});if(token===generation){clearDraft();location.hash="case/"+data.id;}}
    catch(e){if(token===generation){notice(e.message);busy=false;sync();}}
  };
  entries();
  (async()=>{while(token===generation){try{const s=await json("/api/admin/status");if(token!==generation)return;
    ready=s.state==="ready" && Object.values(s.model||{}).some(m=>m.version);
    $("#health").textContent=s.model?roles.map(r=>`${r} · ${s.model[r].name}${s.model[r].error?" (준비 중)":""}`).join("  /  "):(s.message||"모델 준비 중");sync();
    if(ready)return;}catch(e){if(token===generation)notice(e.message);}await wait(2200);}})();
}
function imgPath(d,p){return `${api}/${d.id}/photos/${p.id}`;}
function stateLabel(s){return ({completed:"완료",queued:"대기",running:"분석 중",failed:"실패"})[s]||s;}
async function history(token){
  const rows=await json(`${api}?offset=${historyOffset}`);if(token!==generation)return;
  root.innerHTML=`<div class="top"><div><h1>저장된 결과</h1><p class="muted">발표용으로 고정한 묶음이 먼저 표시됩니다.</p></div><a class="button primary" href="#new">새 묶음 분석</a></div><section class="panel">
  ${rows.map(d=>`<a class="history" href="#case/${d.id}"><img alt="" loading="lazy" src="${imgPath(d,{id:d.cover})}?thumb=true"><div><strong>${d.pinned?"★ ":""}${html(d.title)}</strong><br><small>${html(new Date(d.created).toLocaleString("ko-KR"))} · ${d.photo_count}장 · ${stateLabel(d.state)}</small></div><span class="end">${d.summary?roles.map(r=>`${r} ${badge(d.summary.models[r].grade)||"보류"}`).join("　"):""}</span></a>`).join("")||"<p>저장된 사진 묶음이 없습니다.</p>"}
  <div class="actions"><button id="prev" ${historyOffset===0?"disabled":""}>이전</button><button id="next" ${rows.length<25?"disabled":""}>다음</button></div></section>`;
  $("#prev").onclick=()=>{historyOffset=Math.max(0,historyOffset-25);route();};$("#next").onclick=()=>{historyOffset+=25;route();};
}
function summaryCard(d,r){
  const s=d.summary.models[r], m=d.models[r], label=s.valid>=2?"최종 EMA":"사진 점수";
  const repeat=Object.entries(s.actions).filter(([,ids])=>ids.length>=2).map(([n,ids])=>`${html(d.rules.classes[n])} ${ids.length}회 (${ids.join(", ")}번)`);
  const last=s.last?d.photos[s.last-1].results[r]:null;
  return `<article class="model ${r==="B"?"b":""}"><h2>${r} · ${html(m.name)}</h2><small>${html(m.description||m.error)} · ${html(m.version||"미등록")}</small>
  <div>${label} <span class="number">${points(s.ema,d.rules)}</span> ${badge(s.grade)}</div>
  <p>판정 ${s.valid}/${s.total}장 (${fmt(100*s.valid/s.total)}%) · 마지막 유효 사진 ${s.last??"—"}번</p>
  <p>${s.valid===0?"판정 가능한 사진 없음":s.valid===1?"습관 요약을 위한 관찰 부족":`${s.valid===2?"두 장 기준 잠정 경향 · ":""}첫 관찰 대비 ${s.change>0?"+":""}${fmt(s.change)}점`}</p>
  ${s.valid>=2?`<p class="muted">반복 자세 · ${repeat.join(" / ")||"반복 관찰 없음"}<br>가장 최근 관찰 · ${html(d.rules.classes[last.label])} (${s.last}번)</p>`:""}</article>`;
}
function renderCase(){
  const d=current,s=d.summary,policy=d.rules;clearHeat();
  root.innerHTML=`<div class="top"><div><h1>${html(d.title)}</h1><p class="muted">동일 운전자 가정 · 관리자 지정 순서 · ${d.photo_count}장 · ${html(new Date(d.created).toLocaleString("ko-KR"))}</p></div><a href="#history" class="button">목록으로</a></div>
  <div class="legend"><b>판정 기준</b><span>안전 운전 ${policy.safe}점 이상</span><span>일반 운전 ${policy.ordinary}점 이상 ${policy.safe}점 미만</span><span>위험 운전 ${policy.ordinary}점 미만</span></div>
  <div class="stack"><div class="grid2">${roles.map(r=>summaryCard(d,r)).join("")}</div>
  <section class="panel"><div class="top"><h2>EMA 흐름</h2><span><b style="color:#167c6b">● 모델 A</b>　<b style="color:#a15c19">◆ 모델 B</b></span></div>
  <canvas class="chart" aria-label="사진 순서별 모델 A와 B의 EMA. 아래 계산 표와 사진 선택으로 같은 데이터를 확인할 수 있습니다."></canvas>
  <p>공통 판정 ${s.common.count}장 · 공통 사진 기준 B − A <b>${s.common.delta==null?"비교할 사진 부족":fmt(s.common.delta)+"점"}</b></p>
  <details><summary>EMA 계산 보기</summary><p>α = ${policy.alpha}. 유효 사진마다 이전 관찰 가중치를 ${(1-policy.alpha).toFixed(2)}배로 줄이고 가중치 합으로 나눕니다. 빈칸은 보류된 관찰입니다.</p>
  <div class="scroll"><table><thead><tr><th>순번</th><th>A 사진 / EMA</th><th>A 비중</th><th>B 사진 / EMA</th><th>B 비중</th></tr></thead><tbody>${d.photos.map((p,i)=>`<tr><td>${i+1}</td>${roles.map(r=>`<td>${p.results[r].score??"—"} / ${points(s.models[r].trace[i],policy)}</td><td>${s.models[r].weights[i]==null?"—":fmt(100*s.models[r].weights[i])+"%"}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details></section>
  <section class="panel"><h2>사진별 판단</h2><div class="thumbs">${d.photos.map((p,i)=>`<button data-select="${i}" aria-label="${i+1}번 사진 선택"><img alt="" loading="lazy" src="${imgPath(d,p)}?thumb=true">${i+1}번</button>`).join("")}</div><div id="photoDetail"></div></section>
  <div class="actions"><button id="pin">${d.pinned?"발표용 고정 해제":"발표용 고정"}</button><button id="prep">선택 사진의 양쪽 Grad-CAM 저장</button><button id="delete">묶음 삭제</button></div></div>`;
  $(".thumbs").onclick=e=>{const t=e.target.closest("[data-select]");if(t)selectPhoto(Number(t.dataset.select));};
  $("#pin").onclick=async e=>{try{const v=await json(`${api}/${d.id}/pin?pinned=${!d.pinned}`,{method:"PUT"});d.pinned=v.pinned;e.target.textContent=d.pinned?"발표용 고정 해제":"발표용 고정";notice(d.pinned?"발표용으로 고정했습니다.":"고정을 해제했습니다.");}catch(ex){notice(ex.message);}};
  $("#delete").onclick=async()=>{if(!confirm("이 묶음의 사진과 분석 결과를 삭제할까요?"))return;try{await json(`${api}/${d.id}`,{method:"DELETE"});location.hash="history";}catch(e){notice(e.message);}};
  $("#prep").onclick=()=>roles.forEach(r=>{const b=$("#cam"+r);if(!b.disabled && !b.dataset.loaded)b.click();});
  selectPhoto(0);
}
function selectPhoto(i){
  const d=current;if(!d)return;selected=i; clearHeat();const p=d.photos[i], truth=p.metadata.truth;
  document.querySelectorAll("[data-select]").forEach((b,j)=>b.classList.toggle("active",i===j));
  const a=p.results.A,b=p.results.B, both=a.score!=null && b.score!=null;
  $("#photoDetail").innerHTML=`<h3>${i+1}번 · ${html(p.name)}</h3><small>시점 ${html(p.metadata.view)} · ${html(p.metadata.source||"출처 미입력")} · 검수 정답 ${truth==null?"미확정":html(d.rules.classes[truth])}</small>
  <img class="photo" src="${imgPath(d,p)}" alt="${i+1}번 분석 원본"><p>${a.label!=null && b.label!=null?(a.label===b.label?"자세 예측 일치":"자세 예측 불일치"):"자세 비교 보류"} · 사진 점수 차이(B − A) ${both?(b.score-a.score)+"점":"—"}</p>
  <div class="grid2">${roles.map(r=>{const x=p.results[r];const ranked=(x.probabilities||[]).map((prob,idx)=>[prob,idx]).sort((a,b)=>b[0]-a[0]);
  return `<article><h3>모델 ${r} · ${x.label==null?"예측 없음":"c"+x.label+" "+html(d.rules.classes[x.label])}</h3><p>${x.score==null?"판정 보류 · "+html(x.reason):x.score+"점 "+badge(x.grade)} · 신뢰도 ${x.confidence==null?"—":fmt(100*x.confidence)+"%"}</p>
  <details><summary>점수 계산 보기</summary><p>${x.score==null?html(x.reason):`위험도 ${x.risk} → 100 − ${x.risk} = ${x.score}점 → ${html(x.grade)}`}</p>
  ${truth!=null?`<p>정답 기준 ${100-d.rules.risks[truth]}점 · 절대오차 ${x.score==null?"—":Math.abs(100-d.rules.risks[truth]-x.score)+"점"}</p>`:""}
  <small>신뢰도 기준 ${100*d.rules.confidence}% · ${html(d.rules.version)}<br>${ranked.slice(0,3).map(([v,n])=>`c${n} ${fmt(v*100)}%`).join(" / ")}</small></details>
  <div class="actions"><button id="cam${r}" ${x.label==null?"disabled":""}>Grad-CAM 보기</button></div><div id="overlay${r}" role="status"></div></article>`;}).join("")}</div>`;
  roles.forEach(r=>$("#cam"+r).onclick=()=>showCam(d,p,r));drawChart();
}
async function showCam(d,p,r){
  const btn=$("#cam"+r),box=$("#overlay"+r);if(btn.dataset.loaded){box.hidden=!box.hidden;btn.textContent=box.hidden?"Grad-CAM 보기":"Grad-CAM 닫기";return;}
  btn.disabled=true;box.textContent="히트맵을 준비하고 있습니다…";
  const path=`${imgPath(d,p)}/cam/${r}`;
  try{let res=await request(path,{method:"POST"});
    while(res.status===202){if(!box.isConnected)return;await wait(650);res=await request(path);}
    if(!box.isConnected)return;
    const url=URL.createObjectURL(await res.blob());heatUrls.push(url);
    box.innerHTML=`<img class="cam-img" alt="모델 ${r}의 Grad-CAM" src="${url}"><small>모델 ${r} · c${p.results[r].label} · ${html(d.rules.cam_method)} · 따뜻한 영역이 예측에 기여했습니다.</small>`;
    btn.dataset.loaded="1";btn.textContent="Grad-CAM 닫기";
  }catch(e){box.textContent=e.message;}finally{btn.disabled=false;}
}
function drawChart(){
  const el=$(".chart");if(!el||!current)return;const data=current, n=data.photos.length, w=el.clientWidth,h=el.clientHeight;
  const ratio=window.devicePixelRatio||1;el.width=w*ratio;el.height=h*ratio;const ctx=el.getContext("2d");ctx.scale(ratio,ratio);
  const left=36,right=w-18,top=12,base=h-26;const x=i=>n===1?(left+right)/2:left+(right-left)*i/(n-1),y=v=>base-(base-top)*v/100;
  ctx.font="12px sans-serif";ctx.lineWidth=1;
  for(const v of [0,data.rules.ordinary,data.rules.safe,100]){ctx.strokeStyle="#cbd8d1";ctx.fillStyle="#43544e";ctx.fillText(v,2,y(v)+4);ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(right,y(v));ctx.stroke();}
  ctx.strokeStyle="#9aaea4";ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(x(selected),top);ctx.lineTo(x(selected),base);ctx.stroke();ctx.setLineDash([]);
  data.photos.forEach((p,i)=>{if(n<=15||i%2===0||i===n-1)ctx.fillText(i+1,x(i)-4,base+18);});
  roles.forEach(r=>{const t=data.summary.models[r].trace,color=r==="A"?"#167c6b":"#a15c19"; ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;let last=false;
    ctx.beginPath();t.forEach((v,i)=>{if(v==null){last=false;return;}if(last)ctx.lineTo(x(i),y(v));else ctx.moveTo(x(i),y(v));last=true;});ctx.stroke();
    t.forEach((v,i)=>{if(v==null){ctx.fillText("×",x(i)-3,base-(r==="A"?2:13));return;}ctx.beginPath();if(r==="A"){ctx.arc(x(i),y(v),4,0,Math.PI*2);ctx.fill();}else{const a=x(i),b=y(v);ctx.moveTo(a,b-6);ctx.lineTo(a+6,b);ctx.lineTo(a,b+6);ctx.lineTo(a-6,b);ctx.closePath();ctx.stroke();}});});
  el.onclick=e=>selectPhoto(n===1?0:Math.max(0,Math.min(n-1,Math.round((e.offsetX-left)*(n-1)/(right-left)))));
}
window.addEventListener("resize",drawChart);
(async()=>{try{const s=await json("/api/admin/session");gate(s.authenticated);if(s.authenticated)route();}catch(e){gate(false);notice(e.message);}})();
