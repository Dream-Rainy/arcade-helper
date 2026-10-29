"use strict";
const $ = id => document.getElementById(id);
let authVersion = 0;
const state = {me:null, song:null, selectedSongId:null, cursors:{}, lists:{}, relink:new Map(), mapping:null};
const ops = {ADD_ALIAS:"新增别名",RETRACT_EVIDENCE:"撤回来源证据",REPLACE_ALIAS:"替换别名",BLOCK_ALIAS:"屏蔽别名",UNBLOCK_ALIAS:"恢复别名",RELINK_GAME_SONGS:"调整作品关系",MAP_SOURCE_RECORD:"来源记录映射"};
const statuses = {pending:"待审核",approved:"已通过",rejected:"已拒绝",withdrawn:"已撤回",superseded:"已替代"};
const reasons = {possible_rename:"疑似改名",duplicate_identity:"身份重复",duplicate_source_record_key:"来源编号重复",variant_changed:"音乐或谱面版本变化",incomplete_identity:"身份信息不足",retired_identity:"历史身份重新出现",approved_mapping_changed:"已批准的映射变化",source_record_changed:"来源原文变化",title_artist:"标题或作者相近",chart_variants:"同游戏、同曲名和作者的谱面变体",variant_or_uncertain:"音乐版本或关系需要确认"};
const el = (tag,text,cls) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const notice = (text,error=false) => {$("notice").textContent=text;$("notice").className=error?"error":"";$("notice").hidden=false;};
const clear = node => node.replaceChildren();
const badge = (text,cls="") => el("span",text,"badge "+cls);
function bind(node,event,fn){node.addEventListener(event,async ev=>{if(event==="submit")ev.preventDefault();const button=event==="submit"?ev.submitter:(node.tagName==="BUTTON"?node:null);if(button?.disabled)return;if(button)button.disabled=true;try{await fn(ev);}catch(error){if(!error.obsolete)notice(error.message,true);}finally{if(button?.isConnected)button.disabled=false;}});}
function button(text,fn,cls="secondary"){const b=el("button",text,cls);b.type="button";bind(b,"click",fn);return b;}
async function api(path,body){
  const version=authVersion;
  let response,data;
  try{
    response=await window.aliasRelay.request(path,body);
    data=await response.json();
  }catch(error){
    if(version!==authVersion)error.obsolete=true;
    throw error;
  }
  // A response from a previous login must never update the new user's page.
  if(version!==authVersion){
    const error=new Error("已忽略旧登录状态的响应");
    error.obsolete=true;
    throw error;
  }
  if(!response.ok){
    if(response.status===401&&path!=="me")signedOut();
    const error=new Error(data.detail||"请求失败");
    error.status=response.status;
    throw error;
  }
  return data;
}
function refreshAfterWrite(...tasks){
  const version=authVersion;
  Promise.all(tasks).catch(error=>{
    if(version===authVersion&&!error.obsolete)notice("操作已成功，但页面刷新失败，请手动刷新："+error.message,true);
  });
}
function signedOut(){
  authVersion++;
  state.me=null;
  state.song=null;
  state.selectedSongId=null;
  state.mapping=null;
  state.relink.clear();
  state.cursors={};
  state.lists={};
  for(const id of ["song-results","selected-info","linked-entries","evidence-list","my-list","review-list","review-detail","candidate-list","import-list","pending-list","mapping-detail","relink-results","relink-selection","detail-content"])clear($(id));
  for(const form of document.querySelectorAll("form"))form.reset();
  for(const dialog of document.querySelectorAll("dialog"))dialog.close();
  $("identity").textContent="";
  $("song-selected").hidden=true;
  $("selection-empty").hidden=false;
  $("selection-empty").textContent="选择搜索结果后，确认音乐版本和别名来源。";
  $("workspace").hidden=true;
  $("account").hidden=true;
  $("login").hidden=false;
  document.querySelectorAll(".admin-only").forEach(node=>node.hidden=true);
}

async function signedIn(me){authVersion++;state.me=me;$("login").hidden=true;$("workspace").hidden=false;$("account").hidden=false;$("identity").textContent=me.label+(me.admin?" · 管理员":"");document.querySelectorAll(".admin-only").forEach(n=>n.hidden=!me.admin);const games=await api("games");for(const id of ["game-filter","relink-game"]){const select=$(id);select.replaceChildren(new Option("全部游戏",""),...games.map(g=>new Option(g,g)));}switchView("submit");await loadRequests("my");}
function switchView(view){for(const section of ["submit","review","match"]){$("view-"+section).hidden=section!==view;document.querySelector(`[data-view="${section}"]`)?.setAttribute("aria-current",section===view?"page":"false");}}
bind($("logout"),"click",async()=>{await api("logout",{});signedOut();window.aliasRelay.socket.close();notice("已退出登录。请私聊机器人重新获取编辑链接。");});
for(const tab of document.querySelectorAll("[data-view]"))bind(tab,"click",async()=>{const view=tab.dataset.view;switchView(view);if(view==="review")await loadRequests("review");if(view==="match")await Promise.all([loadCandidates(),loadImports()]);});
function songCard(song){const box=el("div",undefined,"song-meta");const tags=el("div",undefined,"tags");tags.append(badge(song.game_code||"未知游戏"),badge("ID "+(song.public_id??song.id??song.game_song_id)));if(song.active===false)tags.append(badge("已下架","withdrawn"));box.append(tags,el("h3",song.title||"未知乐曲"),el("div",song.artist||"作者未提供","muted"));const markers=Array.isArray(song.chart_variant)?song.chart_variant.join(" · "):song.chart_variant;if(markers)box.append(el("p","谱面标记："+markers,"hint"));if(song.marker_aliases?.length)box.append(el("p","搜索标记："+song.marker_aliases.join("、"),"hint"));return box;}
function resultButton(song,onPick){const b=button("",()=>onPick(song),"result");b.append(badge(song.game_code),el("strong",song.title),el("span",[song.artist||"作者未提供","ID "+song.public_id,song.matched_text&&song.matched_text!==song.title?"命中 "+song.matched_text:""].filter(Boolean).join(" · "),"muted"));return b;}
let searchVersion=0;
bind($("song-search"),"submit",async()=>{const version=++searchVersion;$("search-summary").textContent="正在搜索…";clear($("song-results"));try{const data=await api("search?"+new URLSearchParams({q:$("song-query").value.trim(),game:$("game-filter").value}));if(version!==searchVersion)return;$("search-summary").textContent=data.items.length?`找到 ${data.total} 个收录${data.total>50?"，显示前 50 个，请缩小查询范围":""}`:"未找到匹配的乐曲。";if(data.unavailable_games.length)$("search-summary").textContent+=" 暂不可用："+data.unavailable_games.join("、");for(const song of data.items)$("song-results").append(resultButton(song,()=>selectSong(song.id)));}catch(e){if(e.obsolete)throw e;$("search-summary").textContent="搜索未完成。";throw e;}});
let selectionVersion=0;
async function selectSong(id){const changed=state.selectedSongId!==id;const version=++selectionVersion;state.selectedSongId=id;state.song=null;$("song-selected").hidden=true;$("selection-empty").hidden=false;$("selection-empty").textContent="正在读取收录和别名来源…";const data=await api("songs/"+id);if(version!==selectionVersion)return;state.song=data;if(changed)$("alias-scope").value=data.song.chart_variant?.length?"game_song":"work";$("selection-empty").hidden=true;$("song-selected").hidden=false;$("selected-info").replaceChildren(songCard(data.song));if(data.music_variant)$("selected-info").append(el("p","音乐版本："+data.music_variant,"hint"));$("linked-entries").replaceChildren(...data.entries.map(songCard));$("alias-origin").options[0].textContent="来自 "+data.song.game_code;renderEvidence(data);resetAliasKey();}
function renderEvidence(data){const container=$("evidence-list");container.replaceChildren(el("h3","已有别名与来源"));const evidence=data.evidence.filter(e=>e.kind==="alias");if(!evidence.length)container.append(el("p","暂无别名。","hint"));for(const e of evidence){const row=el("div",undefined,"evidence");row.append(el("strong",e.text),el("div",`${e.source_key} · ${e.origin_game||"通用来源"} · ${e.scope==="work"?"作品共享":"单一收录"}${e.effective?"":" · 当前未生效"}`,"hint"));if(state.me.admin){const actions=el("div",undefined,"actions");actions.append(button("撤回此证据",()=>operation("RETRACT_EVIDENCE",{evidence_id:e.id},"撤回来源证据",e.text)),button("替换",()=>replacement(e,data.song)),button(e.effective?"屏蔽此别名":"恢复此别名",()=>operation(e.effective?"BLOCK_ALIAS":"UNBLOCK_ALIAS",{game_song_id:data.song.id,text:e.text},e.effective?"屏蔽别名":"恢复别名","作用于整个作品；通过审核后生效。")));row.append(actions);}container.append(row);}}
let aliasKey=crypto.randomUUID();function resetAliasKey(){aliasKey=crypto.randomUUID();}
$("alias-form").addEventListener("input",resetAliasKey);
bind($("alias-form"),"submit",async()=>{if(!state.song)throw new Error("请先选择乐曲。");const song=state.song.song;const payload={game_song_id:song.id,text:$("alias-text").value.trim(),scope:$("alias-scope").value,origin_game:$("alias-origin").value==="selected"?song.game_code:null};if($("alias-url").value.trim())payload.evidence_url=$("alias-url").value.trim();const result=await api("requests",{payload,reason:$("alias-reason").value.trim(),idempotency_key:aliasKey});notice(`已提交请求 #${result.id}，审核通过后生效。`);$("alias-text").value="";$("alias-reason").value="";$("alias-url").value="";resetAliasKey();refreshAfterWrite(loadRequests("my"));});
function requestLabel(r){if(r.payload.text)return r.payload.text;if(r.operation==="MAP_SOURCE_RECORD")return ({bind:"绑定现有收录",new:"确认新曲",reject:"忽略来源记录"}[r.payload.decision]||"来源映射")+" · "+r.payload.game_code;if(r.operation==="RELINK_GAME_SONGS")return r.payload.mode==="merge"?"合并作品关系":"拆分作品关系";return (r.songs||[]).map(s=>s.title).join(" / ")||ops[r.operation];}
async function loadRequests(kind,more=false){const version=(state.lists[kind]||0)+1;state.lists[kind]=version;if(!more)$(kind+"-more").hidden=true;const data=await api("requests?"+new URLSearchParams({mine:kind==="my",status:$(kind==="my"?"my-status":"review-status").value,after:more?state.cursors[kind]||0:0})).catch(error=>{if(version!==state.lists[kind])error.obsolete=true;throw error;});if(version!==state.lists[kind])return;const list=$(kind+"-list");if(!more)clear(list);if(!data.items.length&&!more)list.append(el("div","目前没有此状态的请求。","empty"));for(const r of data.items){const row=button("",async()=>{if(kind==="review"){await showRequest(r.id,$("review-detail"));}else{await showRequest(r.id,$("detail-content"));$("detail-dialog").showModal();}},"request-row");const line=el("div",undefined,"topline");line.append(el("span",`#${r.id} · ${ops[r.operation]||r.operation}`),badge(statuses[r.status]||r.status,r.status));row.append(line,el("strong",requestLabel(r)),el("span",(r.songs||[]).map(s=>s.game_code+" · "+s.title).join(" / ")||r.affected_games.join("、"),"muted"));const wrap=el("div",undefined,"request-item");wrap.append(row);if(r.can_withdraw)wrap.append(button("撤回申请",()=>openAction("撤回申请","撤回后此请求不再占用待审额度。",null,async()=>{await api(`requests/${r.id}/withdraw`,{version:r.version});refreshAfterWrite(loadRequests(kind));},false),"quiet"));list.append(wrap);}state.cursors[kind]=data.next_cursor;$(kind+"-more").hidden=!data.has_more;}
for(const kind of ["my","review"]){bind($(kind+"-refresh"),"click",()=>loadRequests(kind));bind($(kind+"-more"),"click",()=>loadRequests(kind,true));bind($(kind+"-status"),"change",()=>loadRequests(kind));}
function fieldText(container,title,text){const block=el("div",undefined,"detail-block");block.append(el("strong",title),el("p",text||"未提供","hint"));container.append(block);}
async function showRequest(id,container){container.className="";container.replaceChildren(el("p","正在读取请求…","loading"));const r=await api("requests/"+id);container.replaceChildren(badge(statuses[r.status]||r.status,r.status),el("h3",`#${id} · ${ops[r.operation]||r.operation}`));for(const s of r.songs)container.append(songCard(s));fieldText(container,"申请内容",requestLabel(r));fieldText(container,"范围 / 来源",[(r.payload.scope==="work"?"同作品所有收录":r.payload.scope==="game_song"?"仅当前收录":""),r.payload.origin_game||"通用来源"].filter(Boolean).join(" · "));fieldText(container,"影响游戏",r.affected_games.join("、"));fieldText(container,"提交说明",r.reason);if(r.decision_reason)fieldText(container,"处理理由",r.decision_reason);if(r.payload.evidence_url){try{const u=new URL(r.payload.evidence_url);if(["https:","http:"].includes(u.protocol)){const a=el("a","打开参考链接");a.href=u.href;a.target="_blank";a.rel="noopener noreferrer";container.append(a);}}catch{}}
  if(r.operation==="MAP_SOURCE_RECORD"){const imports=await api("imports");const batch=imports.find(b=>b.game_code===r.payload.game_code&&b.provider===r.payload.provider&&b.namespace===r.payload.namespace);if(batch){const records=await api(`imports/${batch.id}/records?`+new URLSearchParams({key:r.payload.external_key}));const selected=records.find(o=>o.record_selector===r.payload.record_selector)||(records.length===1?records[0]:null);if(selected){container.append(el("h3","来源记录"),songCard({...selected,game_code:batch.game_code,public_id:selected.source_record_key||"来源未提供"}));rawDetails(container,selected.raw);}else container.append(el("p","来源快照已变化，请重新选择来源记录。","warning"));}}
  if(r.operation==="RELINK_GAME_SONGS")fieldText(container,"关系调整",r.payload.mode==="merge"?"将这些收录合并到同一作品，别名会随作品共享。":"将这些收录拆为独立作品，已有共享别名的适用关系可能变化。");
  if(r.same_alias_candidates.length){container.append(el("h3","同名别名已命中的候选"));for(const s of r.same_alias_candidates)container.append(songCard(s));}else if(r.payload.text)container.append(el("p","当前没有同名别名冲突。","hint"));
  if(r.stale){container.append(el("p","目标已发生变化。请重新核对当前歌曲、来源与影响范围；审核时需明确确认使用当前版本。","warning"));const detail=el("details");detail.append(el("summary","查看提交时 / 当前版本"),el("pre",JSON.stringify({提交时:r.target_versions,当前:r.current_target_versions},null,2)));container.append(detail);}
  const actions=el("div",undefined,"button-row");if(r.can_review){actions.append(button("通过",()=>reviewAction(r,true)),button("拒绝",()=>reviewAction(r,false),"danger"));}else if(r.status==="pending")container.append(el("p","你不能审核此请求：自己或共同提交的请求必须由另一位管理员处理。","hint"));if(r.can_withdraw)actions.append(button("撤回申请",()=>openAction("撤回申请","只有原提交者可以撤回。",null,async()=>{await api(`requests/${id}/withdraw`,{version:r.version});$("detail-dialog").close();refreshAfterWrite(loadRequests("my"));},false)));container.append(actions);
}
bind($("detail-close"),"click",()=>$("detail-dialog").close());
let actionCallback=null,actionNeedsReason=true;
function openAction(title,description,fields,fn,needsReason=true){$("action-title").textContent=title;$("action-description").textContent=description;$("action-fields").replaceChildren(...(fields?[fields]:[]));$("action-reason").value="";$("action-reason").required=needsReason;$("action-reason").hidden=!needsReason;document.querySelector('label[for="action-reason"]').hidden=!needsReason;$("action-error").textContent="";actionCallback=fn;actionNeedsReason=needsReason;$("action-submit").disabled=false;$("action-dialog").showModal();}
for(const id of ["dialog-close","dialog-cancel"])bind($(id),"click",()=>$("action-dialog").close());
bind($("action-form"),"submit",async()=>{const reason=$("action-reason").value.trim();if(actionNeedsReason&&!reason){$("action-error").textContent="请填写理由。";return;}try{$("notice").hidden=true;$("notice").textContent="";await actionCallback(reason);$("action-dialog").close();if($("notice").hidden)notice("操作已完成。");}catch(e){if(e.obsolete)return;$("action-error").textContent=e.message+(e.status===409?" 请关闭窗口、刷新详情后重试。":"");}});
function reviewAction(r,approve){let checkbox=null;const fields=el("div");fields.append(el("p","影响游戏："+r.affected_games.join("、"),"hint"));if(r.stale){const label=el("label",undefined,"check-row");checkbox=el("input");checkbox.type="checkbox";checkbox.required=true;label.append(checkbox,el("span","我已核对目标变化，确认按当前版本审核。"));fields.append(label);}openAction(approve?"通过此申请":"拒绝此申请",`#${r.id} · ${requestLabel(r)}`,fields,async reason=>{const body={version:r.version,approve,reason};if(r.stale){if(!checkbox.checked)throw new Error("请先确认目标变化。");body.confirmed_target_versions=r.current_target_versions;}await api(`requests/${r.id}/review`,body);$("detail-dialog").close();clear($("review-detail"));const refreshes=[loadRequests("review"),loadRequests("my")];if(state.selectedSongId)refreshes.push(selectSong(state.selectedSongId));refreshAfterWrite(...refreshes);});}
function operation(op,payload,title,description,fields=null){const key=crypto.randomUUID();openAction(title,description,fields,async reason=>{const data=await api("requests",{operation:op,payload:typeof payload==="function"?payload():payload,reason,idempotency_key:key});notice(`已提交请求 #${data.id}，由另一位管理员审核后生效。`);refreshAfterWrite(loadRequests("my"));});}
function replacement(e,song){const fields=el("div");const label=el("label","新别名");const input=el("input");input.required=true;input.maxLength=256;input.value=e.source_text??e.text;label.append(input);fields.append(label,el("p","范围及来源沿用此证据。","hint"));operation("REPLACE_ALIAS",()=>({evidence_id:e.id,game_song_id:e.anchor_id,text:input.value.trim(),scope:e.scope,origin_game:e.origin_game}),"替换别名","替换原证据，并生成新别名证据；通过审核后生效。",fields);}
async function loadCandidates(more=false){
  const version=(state.lists.candidates||0)+1;state.lists.candidates=version;
  if(!more)$("candidate-more").hidden=true;
  const data=await api("candidates?after="+(more?state.cursors.candidates||0:0)).catch(error=>{if(version!==state.lists.candidates)error.obsolete=true;throw error;});
  if(version!==state.lists.candidates)return;
  if(!more)clear($("candidate-list"));
  if(!data.items.length&&!more)$("candidate-list").append(el("div","暂无待确认的音乐关联候选。","empty"));
  for(const row of data.items){
    const card=el("div",undefined,"candidate"),fields=new Map();
    card.append(badge("候选 #"+row.id+" · "+row.songs.length+" 个收录"),el("p","匹配依据："+(reasons[row.reason]||row.reason),"hint"));
    const compare=el("div",undefined,"compare");
    for(const song of row.songs)if(song){
      const box=songCard(song);
      if(song.chart_variant?.length){
        const label=el("label","新增搜索标记（可选）"),input=el("input");
        input.maxLength=512;input.placeholder="例如：宴、派对";input.dataset.markerSong=String(song.id);
        input.disabled=row.stale;label.append(input);box.append(label);fields.set(song.id,{input,title:song.title});
      }
      compare.append(box);
    }
    card.append(compare);
    if(fields.size)card.append(el("p","多个标记用逗号分隔；仅用于搜索，保留原谱面标记。确认本组时一起保存。","hint"));
    if(row.stale)card.append(el("p","歌曲版本已变化，请重新计算匹配。","warning"));
    const actions=el("div",undefined,"button-row");
    for(const [accept,text] of [[true,row.songs.length>2?"确认本组同一音乐":"确认同一音乐"],[false,row.songs.length>2?"拒绝本组关联":"不是同一音乐"]]){
      const b=button(text,()=>{
        const markers={},preview=[];
        if(accept)for(const [id,field] of fields){const labels=field.input.value.split(/[，,、\n]+/).map(s=>s.trim()).filter(Boolean);if(labels.length){markers[id]=labels;preview.push(field.title+"："+labels.join("、"));}}
        return openAction(text,"核对本组全部收录的作者和音乐版本后填写理由，确认会关联组内全部作品。"+(preview.length?" 新增搜索标记："+preview.join("；"):""),null,async reason=>{
          await api(`candidates/${row.id}/decide`,{version:row.version,approve:accept,reason,...(accept?{marker_aliases:markers}:{})});
          refreshAfterWrite(loadCandidates());
        });
      },accept?"":"secondary");
      b.disabled=row.stale;actions.append(b);
    }
    card.append(actions);$("candidate-list").append(card);
  }
  state.cursors.candidates=data.next_cursor;$("candidate-more").hidden=!data.has_more;
}
bind($("candidate-refresh"),"click",()=>loadCandidates());bind($("candidate-more"),"click",()=>loadCandidates(true));
bind($("associate-open"),"click",()=>openAction("重新计算曲库匹配","会自动关联规则明确确认的同一音乐，并重新生成需要人工确认的候选。",null,async()=>{await api("associate",{});refreshAfterWrite(loadCandidates());},false));
async function loadImports(){const rows=await api("imports");clear($("import-list"));if(!rows.length)$("import-list").append(el("p","当前没有 arcade-songs 导入记录。","hint"));for(const r of rows){const b=button("",()=>loadPending(r),"import-card");b.append(el("strong",r.game_code),el("p",`可用 ${r.available} · 待定 ${r.pending}`,"hint"),badge("数据集 v"+r.sync_version));$("import-list").append(b);}}
bind($("import-refresh"),"click",async()=>{state.mapping=null;clear($("mapping-detail"));clear($("pending-list"));$("pending-more").hidden=true;await loadImports();});
let currentImport=null;
async function loadPending(batch,more=false){currentImport=batch;const data=await api(`imports/${batch.id}?after=`+(more?state.cursors.pending||0:0));if(!more){clear($("pending-list"));clear($("mapping-detail"));state.mapping=null;$("pending-list").append(el("h3",batch.game_code+" · 待定记录"));}if(!data.items.length&&!more)$("pending-list").append(el("p","此数据集没有待定记录。","hint"));for(const r of data.items){const b=button("",()=>showMapping(data,r),"request-row");b.append(el("strong",r.title||"无标题记录"),el("span",(reasons[r.reason]||r.reason)+` · ${r.record_count||1} 条来源观察`,"muted"));$("pending-list").append(b);}state.cursors.pending=data.next_cursor;$("pending-more").hidden=!data.has_more;}
bind($("pending-more"),"click",()=>loadPending(currentImport,true));
function rawDetails(container,raw){const details=el("details");details.append(el("summary","查看来源原文"),el("pre",JSON.stringify(raw,null,2)));container.append(details);}
async function showMapping(batch,pending){const container=$("mapping-detail");container.replaceChildren(el("p","正在读取来源记录…","loading"));state.mapping=null;const records=await api(`imports/${batch.id}/records?`+new URLSearchParams({key:pending.external_key}));container.replaceChildren(el("h3","选择要处理的来源观察"));if(!records.length){container.append(el("p","来源快照中已没有此记录，请刷新数据集。","warning"));return;}for(const r of records){const choice=el("div",undefined,"candidate");choice.append(songCard({...r,game_code:batch.game_code,public_id:r.source_record_key||"来源未提供"}));rawDetails(choice,r.raw);choice.append(button("处理这条记录",()=>mappingEditor(batch,r,container)));container.append(choice);}}
function mappingEditor(batch,record,container){const mapping={batch,record,target:null};state.mapping=mapping;container.replaceChildren(el("h3","映射来源记录"));container.append(songCard({...record,game_code:batch.game_code,public_id:record.source_record_key||"来源未提供"}));rawDetails(container,record.raw);const label=el("label","搜索同游戏的目标曲目"),input=el("input");input.required=true;input.maxLength=256;input.value=record.title;const form=el("form",undefined,"search-form");input.id="mapping-query";label.htmlFor=input.id;const row=el("div",undefined,"input-row");const submit=el("button","搜索","secondary");row.append(input,submit);form.append(label,row);const results=el("div",undefined,"results compact"),selection=el("div"),actions=el("div",undefined,"button-row");container.append(form,results,selection,actions);bind(form,"submit",async()=>{const data=await api("search?"+new URLSearchParams({q:input.value.trim(),game:batch.game_code}));if(state.mapping!==mapping)return;results.replaceChildren(...data.items.map(s=>resultButton(s,()=>{mapping.target=s;selection.replaceChildren(songCard(s));})));if(!data.items.length)results.append(el("p","没有同游戏匹配，可选择确认新曲。","hint"));});for(const [decision,text] of [["bind","绑定所选收录"],["new","确认这是新曲"],["reject","忽略错误记录"]])actions.append(button(text,()=>{if(decision==="bind"&&!mapping.target)throw new Error("先搜索并选择目标收录。");operation("MAP_SOURCE_RECORD",{game_code:batch.game_code,provider:batch.provider,namespace:batch.namespace,external_key:record.external_key,record_selector:record.record_selector,decision,...(decision==="bind"?{target_id:mapping.target.id}:{})},text,`来源：${record.title} · ${batch.game_code}。${decision==="bind"?"绑定到 "+mapping.target.title:"此操作需要另一位管理员审核。"}`);}));}
bind($("relink-search"),"submit",async()=>{const data=await api("search?"+new URLSearchParams({q:$("relink-query").value.trim(),game:$("relink-game").value}));$("relink-results").replaceChildren(...data.items.map(s=>resultButton(s,()=>{state.relink.set(s.id,s);relinkKey=crypto.randomUUID();renderRelink();})));});
function renderRelink(){$("relink-selection").replaceChildren(...[...state.relink.values()].map(s=>{const row=el("div",undefined,"selection-chip");row.append(el("span",s.game_code+" · "+s.title),button("移除",()=>{state.relink.delete(s.id);relinkKey=crypto.randomUUID();renderRelink();},"quiet"));return row;}));}
let relinkKey=crypto.randomUUID();$("relink-form").addEventListener("input",()=>relinkKey=crypto.randomUUID());
bind($("relink-form"),"submit",async()=>{const ids=[...state.relink.keys()];const mode=$("relink-mode").value;if(ids.length<(mode==="merge"?2:1))throw new Error(mode==="merge"?"合并至少选择两个收录。":"请至少选择一个收录。");const reason=$("relink-reason").value.trim();if(!reason)throw new Error("请填写调整理由。");const result=await api("requests",{operation:"RELINK_GAME_SONGS",payload:{song_ids:ids,mode},reason,idempotency_key:relinkKey});notice(`已提交请求 #${result.id}，由另一位管理员审核后生效。`);state.relink.clear();renderRelink();$("relink-reason").value="";relinkKey=crypto.randomUUID();refreshAfterWrite(loadRequests("my"));});
(async()=>{try{await signedIn(await api("me"));}catch(e){if(!e.obsolete&&e.status!==401)notice(e.message,true);}})();
