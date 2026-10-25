const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money = cents => '$' + ((cents || 0)/100).toFixed(2);
const label = text => String(text || '').replaceAll('_', ' ');
let lastSignature = '', currentState = null, failures = 0, previousCartVersion = '';
const terminal = new Set(['completed','no_restock','held','cancelled','failed']);

function preferenceRows(md) {
  return md.split('\n').filter(line => /^- \[[a-z_]+\]/.test(line)).slice(0, 10)
    .map(line => line.replace(/^- \[([^\]]+)\]\s*/, (_,category) => label(category) + ': '));
}
function render(state) {
  currentState = state;
  const c = state.context || {}, cart = state.cart, order = state.order;
  const statuses = {waiting:'Waiting',running:'Agent running',completed:'Cycle complete',no_restock:'No confirmed need',held:'Cart saved',cancelled:'Cancelled',failed:'Needs attention'};
  $('status-pill').textContent = statuses[state.status] || state.status;
  $('status-pill').className = 'status-pill ' + state.status;
  $('model-name').textContent = state.model;
  $('session-id').textContent = 'SESSION ' + state.run_id;
  $('perception-label').textContent = 'Perception: ' + (state.perception === 'live_image' ? 'DeepSeek image analysis' : 'synthetic replay');
  $('model-label').textContent = 'Agent: ' + (state.mode === 'deepseek' ? 'live DeepSeek' : 'offline rules · no LLM');
  $('voice-label').textContent = 'Voice: ' + ({scripted:'scripted replies',mic:'live microphone',text:'developer text'}[state.voice_mode] || state.voice_mode);
  $('pace-note').textContent = state.pace_seconds ? 'Pacing: ' + state.pace_seconds + 's / tool' : 'No added pacing';
  $('image-label').textContent = state.perception === 'live_image' ? 'ANALYZED FRAME' : 'REFERENCE STILL · NOT ANALYZED';
  $('image-note').textContent = state.perception === 'live_image' ? (c.capacity?.note || 'This image is sent to DeepSeek; counts remain estimates.') : 'Reference photo only. The inventory below comes from labelled synthetic observations.';
  $('capacity').textContent = c.capacity?.level && c.capacity.level !== 'unknown' ? label(c.capacity.level) + ' fill' : 'Waiting';
  $('frame-id').textContent = state.frame?.frame_id?.slice(0,12) || '—';
  $('sensor-note').textContent = 'Presence: ' + (state.presence === 'home' ? 'home' : 'away') + ' · simulated sensor';
  const needs = new Map((c.restock_needs || []).map(n => [n.category, n]));
  const ordered = new Set((order?.lines || []).map(l => c.catalog?.[l.sku]?.category));
  const important = new Set([...needs.keys(), ...(cart?.lines || []).map(l => c.catalog?.[l.sku]?.category)]);
  const inventoryRows = [...(c.inventory || [])];
  for (const category of (c.uncertain_categories || [])) if (!inventoryRows.some(i=>i.category===category)) inventoryRows.push({category,quantity:null});
  const inventory = inventoryRows.sort((a,b) => Number(important.has(b.category))-Number(important.has(a.category)) || Number(a.quantity===null)-Number(b.quantity===null) || a.category.localeCompare(b.category));
  $('inventory-count').textContent = inventory.length + ' categories' + ((c.uncertain_categories || []).length ? ' · ' + c.uncertain_categories.length + ' unconfirmed' : '');
  if (inventory.length) $('inventory').innerHTML = inventory.map(item => {
    const uncertain = (c.uncertain_categories || []).includes(item.category);
    const note = uncertain ? 'uncertain' : item.assumed_empty ? 'assumed empty' : needs.has(item.category) ? 'restock' : ordered.has(item.category) ? 'ordered' : '';
    return `<div class="inventory-row ${needs.has(item.category)?'low':''} ${item.assumed_empty?'assumed':''}"><span class="inventory-name"><i class="food-mark"></i>${escape(label(item.category))}</span><span class="inventory-amount">${uncertain?'?':escape(item.quantity)}<span class="stock-note">${escape(note)}</span></span></div>`;
  }).join('');
  const prefs = preferenceRows(c.preferences_markdown || '');
  if (prefs.length) $('preferences').innerHTML = prefs.map(p => `<div class="preference-row"><span>↳</span><p>${escape(p)}</p></div>`).join('');
  const active = [...state.steps].reverse().find(s => s.status === 'running');
  const last = state.steps.at(-1);
  $('step-count').textContent = state.steps.filter(s=>s.status==='completed').length + ' completed';
  $('activity-summary').textContent = state.error || (active ? active.title + '…' : state.status === 'completed' ? 'Restock completed with a verified simulated order result.' : state.status === 'no_restock' ? (c.uncertain_categories?.length ? c.uncertain_categories.length + ' categories remain unconfirmed. No confirmed shortage was sent to checkout.' : 'No confirmed shortage was calculated from this run. No order was submitted.') : last ? last.summary : 'Waiting for the next camera event.');
  const timeline = $('timeline'), nearBottom = timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight < 90;
  const opened = new Set([...timeline.querySelectorAll('details[open]')].map(e=>e.dataset.step));
  if (state.steps.length) timeline.innerHTML = state.steps.map(step => `<article class="step ${escape(step.status)}" data-step-id="${escape(step.id)}"><span class="step-mark">${step.status==='completed'?'✓':step.status==='failed'?'!':''}</span><div class="step-head"><span class="step-title">${escape(step.title)}</span><span class="step-time">${step.execution_ms == null?'':step.execution_ms+' ms'}</span></div><div class="step-code"><code>${escape(step.name)}()</code><span class="step-state">${escape(step.status)}</span></div><p class="step-summary">${escape(step.summary)}</p><details data-step="${escape(step.id)}" ${opened.has(step.id)?'open':''}><summary>Inspect event</summary><pre>${escape(JSON.stringify({input:step.input,result:step.result,presentation_delay_ms:step.presentation_delay_ms},null,2))}</pre></details></article>`).join('');
  if (nearBottom) timeline.scrollTop=timeline.scrollHeight;
  if (cart) {
    $('cart-version').textContent = 'V' + cart.version + ' · ' + (cart.status === 'simulated_submitted' ? 'SUBMITTED' : cart.status.toUpperCase());
    $('cart-lines').innerHTML = cart.lines.map(line => {
      const p = c.catalog?.[line.sku] || {};
      return `<div class="cart-line"><div class="cart-line-head"><strong>${escape(p.singular_en || p.name || label(line.sku))}</strong><span class="cart-line-price">${money((p.cents||0)*line.packs)}</span></div><div class="cart-qty">QTY ${escape(line.packs)} · ${escape(line.sku)}</div><p class="cart-reason">${escape(line.reason)}</p></div>`;
    }).join('');
    $('cart-total').textContent = money(cart.total_cents);
    const versionKey = cart.id + ':' + cart.version;
    if (previousCartVersion !== versionKey) $('cart-lines').scrollTop=$('cart-lines').scrollHeight;
    previousCartVersion=versionKey;
  }
  if (order) {
    $('order').className='order-box submitted';
    $('order').innerHTML=`<span class="order-icon">✓</span><div><h3>Simulated order submitted</h3><p>${escape(order.order_id)} · ${money(order.total_cents)}<br>No payment or delivery was initiated.</p></div>`;
  } else if (state.error) {
    $('order').className='order-box error';
    $('order').innerHTML=`<span class="order-icon">!</span><div><h3>Workflow paused</h3><p>${escape(state.error)}</p></div>`;
  } else if (state.status==='no_restock') {
    $('order').innerHTML='<span class="order-icon">—</span><div><h3>No confirmed restock</h3><p>' + (c.uncertain_categories?.length ? c.uncertain_categories.length + ' categories remain unconfirmed. ' : '') + 'No order was submitted.</p></div>';
  } else if (state.status==='held' || state.status==='cancelled') {
    $('order').innerHTML=`<span class="order-icon">—</span><div><h3>${state.status==='held'?'Cart saved':'Purchase cancelled'}</h3><p>No order was submitted.</p></div>`;
  }
  const speech = state.steps.findLast(s => (s.name==='speak'||s.name==='listen') && s.status==='running');
  document.body.classList.toggle('speaking', speech?.name==='speak');
  document.body.classList.toggle('listening', speech?.name==='listen');
  $('voice-state').textContent = speech?.name==='speak' ? 'Assistant speaking' : speech?.name==='listen' ? 'Listening for additions' : terminal.has(state.status) ? 'Conversation complete' : 'Quiet by default';
  const recentUser = state.dialogue.findLast(d=>d.role==='user');
  let recentAssistant = state.dialogue.findLast(d=>d.role==='assistant');
  if (speech?.name==='speak') recentAssistant={role:'assistant',text:speech.input.text,at:speech.started_at};
  const dialogue=[recentUser,recentAssistant].filter(Boolean).sort((a,b)=>a.at.localeCompare(b.at));
  if (dialogue.length) $('dialogue').innerHTML=dialogue.map(d=>`<div class="dialogue-line ${escape(d.role)}"><span class="dialogue-role">${d.role==='user'?(state.voice_mode==='scripted'?'REPLAY USER':'HOUSEHOLD'):'ASSISTANT'}</span><span class="dialogue-text">${escape(d.text)}</span></div>`).join('');
  tick();
}
function tick(){if(!currentState)return;const end=terminal.has(currentState.status)&&currentState.completed_at?new Date(currentState.completed_at):new Date();const secs=Math.max(0,Math.floor((end-new Date(currentState.started_at))/1000));$('elapsed').textContent=String(Math.floor(secs/60)).padStart(2,'0')+':'+String(secs%60).padStart(2,'0');}
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const state=await r.json();const sig=JSON.stringify(state);if(sig!==lastSignature){render(state);lastSignature=sig;}failures=0;$('connection').textContent='Connected';$('connection').parentElement.classList.add('connected');}catch(e){failures++;$('connection').textContent=failures>2?'Disconnected':'Reconnecting';$('connection').parentElement.classList.remove('connected');}finally{setTimeout(poll,450);}}
setInterval(tick,1000);poll();
