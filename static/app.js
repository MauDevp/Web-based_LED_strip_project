'use strict';
const $ = id => document.getElementById(id);
let token = '', busy = false, reading = false, demo = false, anchor = 1, dragging = false;
let selected = new Set([1]);
let pixels = Array.from({length:150},(_,i)=>({index:i+1,color:'#262e2b',brightness:50}));
const notice = (text,error=false) => { $('notice').textContent=text; $('notice').classList.toggle('error',error); };
async function api(path,data) {
 const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)});
 const result=await response.json(); if(!response.ok) throw new Error(result.error||'No se pudo completar la solicitud.'); return result;
}
function render() {
 for(const [i,cell] of Array.from($('pixels').children).entries()) { const pixel=pixels[i]; cell.style.setProperty('--led',pixel.color); cell.style.setProperty('--level',.25+pixel.brightness/135); cell.classList.toggle('selected',selected.has(i+1));cell.setAttribute('aria-pressed',String(selected.has(i+1)));cell.setAttribute('aria-label',`LED ${i+1}, color ${pixel.color}, brillo ${pixel.brightness}%, ${selected.has(i+1)?'seleccionado':'sin seleccionar'}`); }
 $('selection-count').textContent=`${selected.size} LED${selected.size===1?'':'s'} seleccionado${selected.size===1?'':'s'}`;
 if(selected.size) { const list=[...selected];$('first').value=Math.min(...list);$('last').value=Math.max(...list); }
 $('draft').disabled=selected.size===0;
 updateActions();
}
function updateActions() { const enabled=$('native-enable').checked&&!busy; $('apply-selection').disabled=!enabled||!selected.size;$('apply-layout').disabled=!enabled;$('native-scene').disabled=!enabled; }
function selectRange(first,last) { if(!Number.isInteger(first)||!Number.isInteger(last)||first<1||last>150||first>last) return notice('Selecciona un rango válido del 1 al 150.',true);selected=new Set(Array.from({length:last-first+1},(_,i)=>first+i));render(); }
for(let i=1;i<=150;i++) { const cell=document.createElement('button');cell.className='led';cell.textContent=i;cell.type='button';cell.tabIndex=i===1?0:-1;
 cell.addEventListener('pointerdown',event=>{if(event.button!==0)return;dragging=true;if(event.shiftKey)selectRange(Math.min(anchor,i),Math.max(anchor,i));else {anchor=i;selected=new Set([i]);render();}});
 cell.addEventListener('pointerenter',()=>{if(dragging)selectRange(Math.min(anchor,i),Math.max(anchor,i));});
 cell.addEventListener('click',event=>{if(event.detail===0){anchor=i;selected=new Set([i]);render();}});
 cell.addEventListener('keydown',event=>{const cols=getComputedStyle($('pixels')).gridTemplateColumns.split(' ').length;const delta={ArrowRight:1,ArrowLeft:-1,ArrowDown:cols,ArrowUp:-cols}[event.key];if(delta){event.preventDefault();const next=Math.min(150,Math.max(1,i+delta));cell.tabIndex=-1;const target=$('pixels').children[next-1];target.tabIndex=0;target.focus();if(event.shiftKey)selectRange(Math.min(anchor,next),Math.max(anchor,next));}else if(event.key===' '){event.preventDefault();anchor=i;if(selected.has(i))selected.delete(i);else selected.add(i);render();}});
 $('pixels').append(cell);
}
window.addEventListener('pointerup',()=>dragging=false);window.addEventListener('pointercancel',()=>dragging=false);
function preview(){ $('hex').textContent=$('color').value.toUpperCase();$('brightness-value').textContent=$('brightness').value+'%';$('strip').style.background=`repeating-linear-gradient(90deg,${$('color').value} 0 8px,transparent 8px 13px)`; }
async function refresh(reconnect=false) {
 if(reading)return;reading=true;$('reconnect').disabled=true;if(reconnect)$('connection').textContent='REINTENTANDO';
 try{const state=await api(reconnect?'/api/reconnect':'/api/state');$('connection').textContent=demo?'DEMO / SIN HARDWARE':'CONECTADO / LOCAL';$('last-read').textContent='Última lectura: '+new Date().toLocaleTimeString('es-MX');$('power-state').textContent=state.power===true?'Tira encendida':state.power===false?'Tira apagada':'Encendido no disponible';$('effect-status').textContent=state.effect.running?'EN EJECUCIÓN':'INACTIVO';if(state.effect.error)notice(state.effect.error,true);if(reconnect)notice('Lectura recuperada. No se enviaron cambios ni se reiniciaron efectos.');}
 catch(error){$('connection').textContent='DESCONECTADO';notice(error.message,true);}
 finally{reading=false;$('reconnect').disabled=false;}
}
async function command(path,data,message){if(busy)return;busy=true;for(const id of ['on','off','apply','start'])$(id).disabled=true;updateActions();try{await api(path,data);notice(message);await refresh();}catch(error){notice(error.message,true);}finally{busy=false;for(const id of ['on','off','apply','start'])$(id).disabled=false;updateActions();}}
$('color').oninput=preview;$('brightness').oninput=preview;
$('select-all').onclick=()=>selectRange(1,150);$('clear-selection').onclick=()=>{selected.clear();render();};$('select-range').onclick=()=>{anchor=Number($('first').value);selectRange(anchor,Number($('last').value));};
$('draft').onclick=()=>{for(const index of selected)pixels[index-1]={index,color:$('color').value,brightness:Number($('brightness').value)};render();notice('Borrador actualizado. No se envió ningún comando.');};
$('clear-draft').onclick=()=>{pixels=pixels.map(p=>({index:p.index,color:'#000000',brightness:0}));render();notice('Borrador limpio. La tira física no cambió.');};
$('native-enable').onchange=updateActions;
function applyPixels(indexes){if(!indexes.size)return notice('Selecciona al menos un LED.',true);command('/api/pixels',{acknowledge_experimental:$('native-enable').checked,pixels:pixels.filter(p=>indexes.has(p.index))},'Comandos confirmados por el controlador. Revisa la tira física: el dibujo no es una lectura del resultado.');}
$('apply-selection').onclick=()=>applyPixels(selected);$('apply-layout').onclick=()=>applyPixels(new Set(pixels.map(p=>p.index)));
$('on').onclick=()=>command('/api/control',{power:true},'Comando de encendido confirmado.');$('off').onclick=()=>command('/api/control',{power:false},'Comando de apagado confirmado.');$('apply').onclick=()=>command('/api/control',{color:$('color').value,brightness:Number($('brightness').value)},'Color general confirmado. El borrador conserva su diseño; no representa ahora la tira física.');
function addStep(color='#ff8a42',seconds=3){if($('steps').children.length>=16)return notice('Máximo 16 pasos.',true);const row=document.createElement('div');row.className='step';const picker=document.createElement('input');picker.type='color';picker.value=color;picker.setAttribute('aria-label','Color del paso');const duration=document.createElement('input');duration.type='number';duration.min=2;duration.max=60;duration.value=seconds;duration.setAttribute('aria-label','Duración del paso en segundos');const remove=document.createElement('button');remove.textContent='Eliminar';remove.className='secondary small';remove.onclick=()=>row.remove();row.append(picker,duration,remove);$('steps').append(row);}
$('effect').onchange=()=>{$('custom').hidden=$('effect').value!=='custom';};$('add').onclick=()=>addStep();addStep();addStep('#70c5cc');
$('start').onclick=()=>{const effect={kind:$('effect').value,duration:Number($('duration').value),speed:Number($('speed').value),brightness:Number($('brightness').value),color:$('color').value};if(effect.kind==='custom')effect.steps=Array.from($('steps').children,row=>({color:row.children[0].value,seconds:Number(row.children[1].value)}));if($('effect-scope').value==='selection'){if(!$('native-enable').checked||!selected.size)return notice('Selecciona LEDs y acepta el protocolo experimental.',true);effect.indexes=[...selected];effect.acknowledge_experimental=true;}command('/api/effects',effect,'Efecto programado. El dibujo conserva el borrador; verifica el resultado físico.');};
$('stop').onclick=async()=>{try{await api('/api/effects/stop',{});notice('Efecto de software detenido. Para salir de una escena nativa usa el color general o Apagar.');await refresh();}catch(error){notice(error.message,true);}};
$('native-scene').onclick=()=>command('/api/native',{kind:'scene',acknowledge_experimental:$('native-enable').checked,colors:Array.from($('steps').children,row=>row.children[0].value),brightness:Number($('brightness').value),mode:Number($('native-mode').value),speed:Number($('native-speed').value),segmented:$('segmented').checked},'Escena nativa confirmada, resultado físico sin verificar. Usa color general o Apagar para salir.');
function validateLayout(value){if(!Array.isArray(value)||value.length!==150)return false;return value.every((p,i)=>p&&p.index===i+1&&typeof p.color==='string'&&/^#[0-9a-f]{6}$/i.test(p.color)&&Number.isFinite(p.brightness)&&p.brightness>=0&&p.brightness<=100&&Object.keys(p).sort().join(',')==='brightness,color,index');}
function readSaved(){try{const data=JSON.parse(localStorage.getItem('steren-layouts')||'[]');if(!Array.isArray(data)||data.length>10)throw new Error();return data.filter(d=>d&&typeof d.name==='string'&&d.name.length<=40&&validateLayout(d.pixels));}catch{return [];}}
function savedMenu(){ $('saved').replaceChildren(new Option('Seleccionar…',''));readSaved().forEach((d,i)=>$('saved').add(new Option(d.name,String(i)))); }
$('save').onclick=()=>{const name=$('layout-name').value.trim();if(!name)return notice('Escribe un nombre para guardar.',true);try{let saved=readSaved().filter(d=>d.name!==name);saved.push({name,pixels});localStorage.setItem('steren-layouts',JSON.stringify(saved.slice(-10)));savedMenu();notice('Diseño guardado solo en este navegador.');}catch{notice('El navegador no permite guardar el diseño.',true);}};
$('load').onclick=()=>{const saved=readSaved();if($('saved').value===''||!saved[Number($('saved').value)])return notice('Selecciona un diseño válido.',true);pixels=saved[Number($('saved').value)].pixels;render();notice('Borrador cargado. No se enviaron comandos.');};
$('refresh').onclick=()=>refresh();$('reconnect').onclick=()=>refresh(true);
async function init(){render();preview();savedMenu();try{const session=await api('/api/session');token=session.token;demo=session.demo;await refresh();if(['CONECTADO / LOCAL','DEMO / SIN HARDWARE'].includes($('connection').textContent))notice('Conexión consultada. Diseña en el borrador y aplica solo cuando estés listo; ningún cambio se envía al abrir la página.');setInterval(()=>{if(!busy)refresh();},5000);}catch(error){$('connection').textContent='DESCONECTADO';notice(error.message,true);}}init();
