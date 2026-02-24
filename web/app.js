// Timeline + router + per-user favorites via GitHub Issues + source editor
const pages = ["hot","low","video","fav","source","system","policy","user"];
const state = { data:null, site:null, user:null, sources:null, login:"" };

function $q(sel){ return document.querySelector(sel); }
function $el(tag, cls){ const e=document.createElement(tag); if(cls) e.className=cls; return e; }

function switchPage(p){
  document.querySelectorAll(".menu .item").forEach(a=>{
    a.classList.toggle("active", a.dataset.page===p);
  });
  pages.forEach(pg=>{
    const el = document.getElementById(`page-${pg}`);
    if(el) el.classList.toggle("visible", pg===p);
  });
  if(p==="fav") renderFav();
  if(p==="source") renderSourceEditor();
  if(p==="user") primeUserPage();
}
document.querySelectorAll(".menu .item").forEach(a=>{
  a.addEventListener("click", ()=> switchPage(a.dataset.page));
});

function setAccountLabel(login){
  const el = $q("#acct-email");
  el.textContent = login ? `github:${login}` : "未设置";
}

function getLogin(){
  return localStorage.getItem("gh_login") || "";
}
function setLogin(login){
  state.login = (login||"").trim();
  localStorage.setItem("gh_login", state.login);
  setAccountLabel(state.login);
  loadUserData(state.login);
}

async function loadUserData(login){
  if(!login){ state.user = {favorites:{},ratings:{}}; renderFav(); return; }
  try{
    const res = await fetch(`./public/userdata/${encodeURIComponent(login)}.json?ts=`+Date.now());
    if(res.ok){
      state.user = await res.json();
    }else{
      state.user = {favorites:{},ratings:{}}; // 未创建过
    }
  }catch(e){
    state.user = {favorites:{},ratings:{}}; 
  }
  renderFav();
}

async function boot(){
  try{
    const [feed, site, sources] = await Promise.all([
      fetch("./public/feed.json?ts="+Date.now()).then(r=>r.json()),
      fetch("./public/site.json?ts="+Date.now()).then(r=>r.json()).catch(()=>({})),
      fetch("./public/sources.json?ts="+Date.now()).then(r=>r.json()).catch(()=>({}))
    ]);
    state.data = feed;
    state.site = site;
    state.sources = sources;
    const login = getLogin();
    state.login = login;
    setAccountLabel(login);
    renderTimeline(feed.items || []);
    loadUserData(login);
  }catch(e){
    console.error(e);
    $q("#timeline").innerHTML = `<div class="empty">数据加载失败</div>`;
  }
}

function groupByDate(items){
  const map = {};
  items.forEach(it=>{
    const d = new Date(it.published*1000);
    const dateKey = `${d.getUTCFullYear()}-${d.getUTCMonth()+1}-${d.getUTCDate()}`;
    if(!map[dateKey]) map[dateKey] = { dateStr: `${d.getUTCMonth()+1}月${d.getUTCDate()}日`, rows: [] };
    map[dateKey].rows.push(it);
  });
  Object.values(map).forEach(g=>g.rows.sort((a,b)=>b.published-a.published));
  const arr = Object.keys(map).sort((a,b)=> new Date(b)-new Date(a)).map(k=>map[k]);
  return arr;
}

function issueURL(action, payload){
  const repo = (state.site && state.site.repo) || {};
  const owner = repo.owner || ""; const name = repo.name || "";
  if(!owner || !name){ return "#"; }
  const title = encodeURIComponent(`[${action}] ${payload.title || payload.id || ""}`.slice(0,100));
  const body = encodeURIComponent(JSON.stringify(payload, null, 2));
  return `https://github.com/${owner}/${name}/issues/new?title=${title}&body=${body}`;
}

function favBtn(it){
  const btn = $el("button","btn");
  const isFav = state && state.user && state.user.favorites && state.user.favorites[it.id];
  btn.textContent = isFav ? "已收藏" : "收藏";
  btn.title = isFav ? "取消收藏" : "收藏该条目";
  btn.addEventListener("click", ()=>{
    if(!state.site || !state.site.repo){ alert("仓库元信息缺失"); return; }
    if(!state.login){ alert("请先到“用户”页设置你的 GitHub 用户名"); return; }
    const action = isFav ? "unfav" : "fav";
    const url = issueURL(action, { action, id: it.id, url: it.url, title: it.title, source: it.source, user_hint: state.login });
    window.open(url, "_blank");
  });
  return btn;
}

function rateBtn(it){
  const btn = $el("button","btn ghost");
  btn.textContent = "评分";
  btn.title = "给该条目打分(0-100)";
  btn.addEventListener("click", ()=>{
    if(!state.login){ alert("请先到“用户”页设置你的 GitHub 用户名"); return; }
    const input = prompt("请输入评分(0-100)：", "88");
    if(input==null) return;
    const rating = Math.max(0, Math.min(100, parseInt(input)||0));
    const url = issueURL("rate", { action:"rate", id: it.id, rating, title: it.title, url: it.url, source: it.source, user_hint: state.login });
    window.open(url, "_blank");
  });
  return btn;
}

function card(it){
  const node = $el("div","node");
  const clock = $el("div","clock"); clock.textContent = it.time_str || new Date(it.published*1000).toISOString().slice(11,16);
  const dot = $el("div","dot");
  const card = $el("div","card");
  const head = $el("div","card-head");
  const title = $el("div","title"); title.textContent = it.title;
  const src = $el("div","src"); src.textContent = `${it.source}${it.author?(" · "+it.author):""}`;

  head.appendChild(title);

  const actions = $el("div","actions");
  actions.appendChild(favBtn(it));
  actions.appendChild(rateBtn(it));
  head.appendChild(actions);

  const meta = $el("div","meta");
  const summary = (it.summary || it.content || "").slice(0,240);
  meta.textContent = summary;

  const link = $el("a"); link.href = it.url; link.target="_blank"; link.textContent=" 打开原文";
  meta.appendChild(link);

  const badges = $el("div","badges");
  (it.tags||[]).forEach(t=>{
    const b=$el("span","badge"); b.textContent=t; badges.appendChild(b);
  });
  const score = $el("span","badge score"); score.textContent = it.importance; badges.appendChild(score);

  card.appendChild(src);
  card.appendChild(head);
  card.appendChild(meta);
  card.appendChild(badges);

  node.appendChild(clock); node.appendChild(dot); node.appendChild(card);
  return node;
}

function renderTimeline(items){
  const wrap = $q("#timeline");
  wrap.innerHTML = "";
  const groups = groupByDate(items);
  groups.forEach(g=>{
    const group = $el("div","group");
    const date = $el("div","date"); date.textContent = g.dateStr;
    group.appendChild(date);
    g.rows.forEach(it=> group.appendChild(card(it)));
    wrap.appendChild(group);
  });
}

function renderFav(){
  const wrap = $q("#fav-list");
  if(!wrap) return;
  wrap.innerHTML = "";
  if(!state.login){
    wrap.innerHTML = `<div class="empty">请先到“用户”页设置你的 GitHub 用户名</div>`;
    return;
  }
  const favs = (state.user && state.user.favorites) ? Object.values(state.user.favorites) : [];
  if(!favs.length){ wrap.innerHTML = `<div class="empty">该用户暂无收藏</div>`; return; }
  favs.sort((a,b)=> (b.time||0)-(a.time||0));
  favs.forEach(f=>{
    const it = { id:f.id, title:f.title, url:f.url, source:f.source, published:f.time||0, time_str:"", summary:"", importance: (state.user.ratings||{})[f.id] || "" };
    wrap.appendChild(card(it));
  });
}

function renderSourceEditor(){
  const y = (state.sources && state.sources.youtube && state.sources.youtube.channels) || [];
  const r = (state.sources && state.sources.reddit && state.sources.reddit.subreddits) || [];
  const t = (state.sources && state.sources.twitter && state.sources.twitter.users) || [];
  $q("#src-yt").value = (y||[]).join("\n");
  $q("#src-rd").value = (r||[]).join("\n");
  $q("#src-tw").value = (t||[]).join("\n");
}

function submitSources(){
  const channels = $q("#src-yt").value.split("\n").map(s=>s.trim()).filter(Boolean);
  const subs = $q("#src-rd").value.split("\n").map(s=>s.trim()).filter(Boolean);
  const users = $q("#src-tw").value.split("\n").map(s=>s.trim()).filter(Boolean);
  const payload = {
    action: "update_sources",
    sources: { youtube:{channels}, reddit:{subreddits:subs}, twitter:{users} }
  };
  const url = issueURL("sources", payload);
  if(url==="#"){ alert("仓库元信息缺失"); return; }
  window.open(url, "_blank");
}

function primeUserPage(){
  const inp = $q("#gh-login-input");
  if(inp && !inp.value){ inp.value = state.login || ""; }
}

switchPage("hot");
boot();

document.getElementById("btn-save-login").addEventListener("click", ()=>{
  const v = ($q("#gh-login-input").value||"").trim();
  if(!v){ alert("请输入 GitHub 用户名"); return; }
  setLogin(v);
  alert("已保存。收藏页将加载该用户的数据。");
});
document.getElementById("btn-clear-login").addEventListener("click", ()=>{
  localStorage.removeItem("gh_login");
  setLogin("");
  alert("已清除。");
});

// expose
window.submitSources = submitSources;
