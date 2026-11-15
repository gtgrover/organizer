let state={events:[],tasks:[],notes:'',view:'overview',selected_day:''};
const $=s=>document.querySelector(s);
// Today's date from the device's local clock and timezone (never toISOString, which is UTC).
const localDay=()=>{let d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const formatTime=t=>{if(!t)return 'All day';let [hour,minute]=t.split(':').map(Number);if(!Number.isInteger(hour)||!Number.isInteger(minute))return t;return `${hour%12||12}:${String(minute).padStart(2,'0')} ${hour<12?'AM':'PM'}`};
const showError=e=>alert(e&&e.message?e.message:String(e));
// Loads the data and re-renders whenever it changes. `DB` is the app's data layer:
// IndexedDB on this device (pwa/db.js) or the household server (app/static/db-server.js).
function connect(render){const refresh=()=>DB.snapshot().then(s=>{state=s;render()}).catch(e=>console.error(e));DB.init().then(()=>{DB.subscribe(refresh);refresh()}).catch(e=>alert('Could not load your data: '+e.message))}
function calendar(el,selected,onSelect){let d=new Date(selected+'T12:00:00'),y=d.getFullYear(),m=d.getMonth(),first=new Date(y,m,1).getDay(),count=new Date(y,m+1,0).getDate();let html=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(x=>`<div class="dow">${x}</div>`).join('');for(let i=0;i<first;i++)html+='<div class="day blank"></div>';for(let n=1;n<=count;n++){let key=`${y}-${String(m+1).padStart(2,'0')}-${String(n).padStart(2,'0')}`,colors=[...new Set(state.events.filter(e=>e.day===key).map(e=>e.color||'blue'))];html+=`<button class="day ${key===selected?'selected':''}" data-day="${key}">${n}<span class="dots">${colors.map(c=>`<span class="dot dot-${esc(c)}"></span>`).join('')}</span></button>`}el.innerHTML=html;el.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>onSelect(b.dataset.day))}
function taskRows(){return state.tasks.length?state.tasks.map(t=>`<div class="task"><label><input type="checkbox" data-task="${esc(t.id)}" ${t.done?'checked':''}>${esc(t.title)}</label></div>`).join(''):'<p class="muted">No tasks yet.</p>'}
function bindTasks(root=document){root.querySelectorAll('[data-task]').forEach(x=>x.onchange=()=>DB.setTaskDone(x.dataset.task,x.checked).catch(showError))}
