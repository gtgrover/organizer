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
// Month grid for the display. With onPrint, a Print button fills the empty boxes:
// bottom-right after the last day if there are any, else the empty boxes before day 1,
// else (a 28-day February starting on Sunday) one extra row at the bottom.
function calendar(el,selected,onSelect,onPrint){
	let d=new Date(selected+'T12:00:00'),y=d.getFullYear(),m=d.getMonth(),first=new Date(y,m,1).getDay(),count=new Date(y,m+1,0).getDate();
	const trailing=(7-(first+count)%7)%7;
	const monthName=d.toLocaleDateString(undefined,{month:'long'});
	const printCell=span=>`<button class="day print-cell${span<2?' narrow':''}" type="button" style="grid-column:span ${span}" aria-label="Print ${esc(monthName)} ${y} calendar" title="Print this month">${PRINTER_ICON}<span class="print-label">${span>=3?'Print '+esc(monthName):'Print'}</span></button>`;
	let html=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(x=>`<div class="dow">${x}</div>`).join('');
	if(onPrint&&!trailing&&first)html+=printCell(first);
	else for(let i=0;i<first;i++)html+='<div class="day blank"></div>';
	for(let n=1;n<=count;n++){let key=`${y}-${String(m+1).padStart(2,'0')}-${String(n).padStart(2,'0')}`,colors=[...new Set(state.events.filter(e=>e.day===key).map(e=>e.color||'blue'))];html+=`<button class="day ${key===selected?'selected':''}" data-day="${key}">${n}<span class="dots">${colors.map(c=>`<span class="dot dot-${esc(c)}"></span>`).join('')}</span></button>`}
	if(onPrint&&trailing)html+=printCell(trailing);
	if(onPrint&&!trailing&&!first)html+=printCell(7);
	el.innerHTML=html;
	el.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>onSelect(b.dataset.day));
	const pb=el.querySelector('.print-cell');if(pb)pb.onclick=()=>onPrint(y,m+1);
}
const PRINTER_ICON='<svg class="print-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/></svg>';

// Printable month calendar (US Letter landscape layout, see print.css). Needs an empty
// <div id="print-calendar"> directly inside <body>. month is 1-12.
function printMonth(year,month){
	const monthLabel=new Date(year,month-1,1,12).toLocaleDateString(undefined,{month:'long',year:'numeric'});
	const firstWeekday=new Date(year,month-1,1).getDay();
	const daysInMonth=new Date(year,month,0).getDate();
	const dayCells=Array.from({length:42},(_,index)=>{
		const dayNumber=index-firstWeekday+1;
		if(dayNumber<1||dayNumber>daysInMonth)return '<div class="pc-day outside"></div>';
		const day=`${year}-${String(month).padStart(2,'0')}-${String(dayNumber).padStart(2,'0')}`;
		const eventMarkup=state.events.filter(event=>event.day===day).map(event=>{
			const time=event.time?`<span class="pc-time">${esc(formatTime(event.time))}</span> `:'';
			return `<div class="pc-event">${time}${esc(event.title)}</div>`;
		}).join('');
		return `<div class="pc-day"><div class="pc-day-number">${dayNumber}</div>${eventMarkup}</div>`;
	}).join('');
	const weekdays=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map(day=>`<div class="pc-weekday">${day}</div>`).join('');
	// Once #print-calendar has content, print.css prints only the calendar. Nothing is
	// switched back after printing: Safari (iPhone and Mac) reports "printing finished"
	// before it has actually printed, which used to put the normal screen on paper.
	$('#print-calendar').innerHTML=`<h1>${esc(monthLabel)}</h1><div class="pc-weekdays">${weekdays}</div><div class="pc-month-grid">${dayCells}</div>`;
	if(!printMonth.pageTitle)printMonth.pageTitle=document.title;
	document.title=`${monthLabel} Calendar`; // used as the PDF name / print header
	setTimeout(()=>window.addEventListener('pointerdown',()=>{document.title=printMonth.pageTitle},{once:true}),1000);
	// let the browser lay out the calendar before the print dialog takes its snapshot
	requestAnimationFrame(()=>setTimeout(()=>window.print(),50));
}
function taskRows(){return state.tasks.length?state.tasks.map(t=>`<div class="task"><label><input type="checkbox" data-task="${esc(t.id)}" ${t.done?'checked':''}>${esc(t.title)}</label></div>`).join(''):'<p class="muted">No tasks yet.</p>'}
function bindTasks(root=document){root.querySelectorAll('[data-task]').forEach(x=>x.onchange=()=>DB.setTaskDone(x.dataset.task,x.checked).catch(showError))}
