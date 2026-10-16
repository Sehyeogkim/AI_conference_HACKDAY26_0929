/* Player UI. The server owns physics, timing, success, and recording. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const activePhases = new Set(['recording', 'running', 'playing']);
  const movementCodes = new Set(['KeyW','KeyS','KeyA','KeyD','KeyR','KeyF','KeyQ','KeyE','KeyZ','KeyX','KeyC','KeyV','ShiftLeft','ShiftRight']);
  const defaultTasks = [
    {id:'CoffeePressButton',title:'Start the machine',instruction:'Activate the coffee machine, then move your hand away.',description:'Press the button. A small task to find your rhythm.'},
    {id:'CoffeeSetupMug',title:'Set the mug',instruction:'Place the mug below the dispenser, then release it.',description:'Pick up the mug and position it beneath the dispenser.'},
    {id:'CoffeeServeMug',title:'Serve the coffee',instruction:'Move the mug onto the counter, then move your hand away.',description:'Bring the mug from the machine to the counter.'}
  ];
  let playerID = sessionStorage.getItem('coffee-player-id');
  if (!playerID) { playerID = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`); sessionStorage.setItem('coffee-player-id',playerID); }
  let state = {phase:'loading',task_id:'CoffeeSetupMug',mode:'practice',control_mode:'arm',gripper_closed:false,steps:0,sim_time:0};
  let tasks = defaultTasks;
  let selectedTask = 'CoffeeSetupMug';
  let controlMode = 'arm';
  let gripper = false;
  let socket;
  let reconnectTimer;
  let connected = false;
  let pending = false;
  let keys = new Set();
  let mainView = 'overview';
  let frameSeen = false;
  let submitError = false;
  let lastPhase;
  let slowSince = null;

  function safeText(value) { return String(value ?? ''); }
  function taskTitle(id) { return tasks.find(task=>task.id===id)?.title || id || 'Coffee task'; }
  function taskID(task) { return task.id || task.task_id; }
  function isActive() { return activePhases.has(state.phase); }
  function isInput(element) { return element && (element.matches('input,select,textarea') || element.isContentEditable); }
  function setMessage(message,error=false) { $('message').textContent=message; $('message').classList.toggle('error',error); $('message').hidden=!message; }

  async function api(path,body={}) {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Player-ID':playerID},body:JSON.stringify(body)});
    let payload;
    try {payload=await response.json();} catch {payload={error:`Server returned ${response.status}`};}
    if(!response.ok) {
      const detail=payload.detail || payload.error || `Request failed (${response.status})`;
      throw new Error(response.status===409 ? `Session already in use. ${typeof detail==='string'?detail:'Close the other player tab before taking control.'}` : typeof detail==='string'?detail:JSON.stringify(detail));
    }
    if (payload.status && typeof payload.status==='object') updateStatus(payload.status);
    else if(payload.phase) updateStatus(payload);
    return payload;
  }
  async function act(action) {
    if(pending) return;
    pending=true; renderActions();
    try { await action(); }
    catch(error) {setMessage(error.message,true);}
    finally {pending=false;renderActions();}
  }
  function inputMessage() {
    if(socket?.readyState!==WebSocket.OPEN || state.locked) return;
    socket.send(JSON.stringify({type:'input',keys:[...keys].filter(key=>!key.startsWith('Shift')),mode:controlMode,gripper,fine:keys.has('ShiftLeft')||keys.has('ShiftRight')}));
  }
  function releaseKeys(pause=false) {
    keys.clear();inputMessage();
    if(pause && isActive() && socket?.readyState===WebSocket.OPEN) socket.send(JSON.stringify({type:'pause'}));
  }
  function renderTasks() {
    $('tasks').replaceChildren();
    tasks.forEach((task,index)=>{
      const id=taskID(task);
      const card=document.createElement('button');card.className=`task-card${selectedTask===id?' selected':''}`;
      card.disabled=pending || !connected || !!state.locked || isActive() || ['loading','paused','submitting','completed','stopped','replaying'].includes(state.phase);
      card.setAttribute('aria-pressed',String(selectedTask===id));
      const top=document.createElement('span');top.className='task-top';
      const number=document.createElement('span');number.textContent=`${String(index+1).padStart(2,'0')} / ${index===0?'WARM UP':'COLLECT'}`;
      const check=document.createElement('span');check.className='task-check';check.textContent=selectedTask===id?'↗':'';top.append(number,check);
      const title=document.createElement('strong');title.textContent=task.title||defaultTasks.find(t=>t.id===id)?.title||id;
      const description=document.createElement('p');description.textContent=task.description||defaultTasks.find(t=>t.id===id)?.description||task.instruction||'';
      card.append(top,title,description);
      card.onclick=()=>act(async()=>{selectedTask=id;renderTasks();await resetScene();});
      $('tasks').append(card);
    });
  }
  function renderActions() {
    const phase=state.phase;
    const recording=isActive();
    const paused=phase==='paused';
    const finished=['completed','stopped'].includes(phase);
    const ready=['ready','submitted'].includes(phase);
    $('startButton').hidden=!ready;
    $('startButton').textContent=state.mode==='practice'?'Start practice →':'Start recording →';
    $('pauseButton').hidden=!recording;
    $('resumeButton').hidden=!paused;
    const hasSubmission=state.mode==='practice'||state.has_recording===true;
    $('submitButton').hidden=!(recording||paused||finished||(phase==='error'&&state.has_recording))||!hasSubmission||submitError;
    $('submitButton').textContent=state.mode==='practice'?'Finish practice ↗':finished?'Submit demonstration ↗':'Stop & submit ↗';
    $('retrySubmitButton').hidden=!submitError||state.has_recording===false;
    $('discardButton').hidden=!(recording||paused||finished||submitError);
    document.querySelectorAll('.session-actions button').forEach(button=>button.disabled=pending||!connected||!!state.locked);
    document.querySelectorAll('.episode-replay').forEach(button=>button.disabled=pending||!connected||!!state.locked||isActive()||['paused','completed','stopped','submitting','replaying','loading'].includes(phase));
    document.querySelectorAll('[data-view]').forEach(button=>button.disabled=pending||!connected||!!state.locked||['loading','replaying','submitting'].includes(phase));
    $('resetButton').disabled=pending||!connected||recording||paused||finished||['submitting','replaying'].includes(phase)||!!state.locked;
    $('sessionMode').disabled=$('seedInput').disabled=$('resetButton').disabled;
    const notes={loading:'The simulator is preparing the scene.',ready:state.mode==='practice'?'Practice is not saved as a demonstration.':'Ready to capture your next demonstration.',recording:state.mode==='practice'?'Practice session · no dataset recording.':'Recording observations before each action.',paused:'Paused · physics and recording are stopped.',completed:state.mode==='practice'?'Task complete. Finish to begin another practice.':'Task complete. Submit to save this episode.',stopped:'Session stopped. Submit to preserve this attempt.',submitting:'Saving and validating the recording…',submitted:'Session saved. Load a new scene to begin again.',replaying:'Playing saved simulator states · input is disabled.',error:'Check the error details below.'};
    $('actionNote').textContent=state.locked?'Another player tab owns this session.':notes[phase]||'Waiting for the simulator.';
    const overlay=paused||finished||phase==='submitting'||!!state.locked;
    $('pauseOverlay').hidden=!overlay;
    $('overlayResume').hidden=!paused||!!state.locked;
    $('overlayResume').disabled=pending||!connected;
    $('overlayIcon').textContent=state.locked?'◷':finished?(state.success?'✓':'□'):phase==='submitting'?'↗':'Ⅱ';
    $('overlayTitle').textContent=state.locked?'Session in use':phase==='completed'?(state.success?'Nicely done.':'Session complete'):phase==='stopped'?'Attempt finished':phase==='submitting'?'Saving your demonstration':'Paused';
    $('overlayText').textContent=state.locked?'This simulator is controlled by another browser tab. Close that player tab to continue.':phase==='completed'?'The simulator reported the task complete. Submit this episode to save its result.':phase==='stopped'?'This attempt has stopped. You can submit it or discard it.':phase==='submitting'?'Keeping observations, actions, and replay state together.':'Your robot and recording are paused. Click Resume when you are ready.';
  }
  function updateStatus(incoming) {
    const previous=state;
    state={...state,...incoming};
    if(Array.isArray(incoming.tasks)&&incoming.tasks.length) tasks=incoming.tasks.map(task=>({...task,id:taskID(task)})).sort((a,b)=>defaultTasks.findIndex(t=>t.id===a.id)-defaultTasks.findIndex(t=>t.id===b.id));
    if(incoming.task_id)selectedTask=incoming.task_id;
    if(incoming.control_mode)controlMode=incoming.control_mode;
    if(typeof incoming.gripper_closed==='boolean')gripper=incoming.gripper_closed;
    const phaseNames={loading:'Preparing scene',ready:'Ready to play',recording:state.mode==='practice'?'Practice in progress':'Recording',paused:'Paused',completed:'Task completed',stopped:'Stopped',submitting:'Saving episode',submitted:'Saved',replaying:'Replaying saved states',error:'Simulator error'};
    $('statusText').textContent=phaseNames[state.phase]||state.phase;
    $('statusDot').className=`status-dot ${state.phase}`;
    $('recordTag').hidden=!(isActive()&&state.mode!=='practice'&&state.has_recording===true);
    $('controlMode').textContent=controlMode.toUpperCase();
    $('gripperState').textContent=gripper?'CLOSED':'OPEN';
    const seconds=Number(state.sim_time)||0;
    $('simTime').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${(seconds%60).toFixed(2).padStart(5,'0')}`;
    $('stepCount').textContent=Number(state.steps||0).toLocaleString();
    const task=tasks.find(task=>task.id===state.task_id)||defaultTasks.find(task=>task.id===state.task_id);
    $('objective').textContent=task?.instruction||'Complete the selected coffee task.';
    if(document.activeElement!==$('sessionMode')) $('sessionMode').value=state.mode;
    if(document.activeElement!==$('seedInput')&&state.seed!==undefined)$('seedInput').value=state.seed;
    $('modeNote').textContent=state.mode==='practice'?'Get comfortable with the robot before collecting data.':state.mode==='benchmark'?'Original evaluation horizon: 720 control steps (36 seconds of simulation time).':'Your observations, executed actions, and replay state are recorded together.';
    $('captureState').textContent=isActive()&&state.has_recording===true?'Recording · synchronized observations':state.phase==='paused'&&state.has_recording===true?'Recording paused · no new samples':'Camera previews · capture begins in collection mode';
    const stepMs=Number(state.metrics?.last_step_wall_ms);
    if(isActive()&&Number.isFinite(stepMs)&&stepMs>50){
      slowSince??=performance.now();
      if(performance.now()-slowSince>=1000){$('performanceNotice').hidden=false;$('performanceNotice').textContent=`Simulator running slower than real time (${Math.round(stepMs)} ms last step). Recording timestamps still use 20 Hz of simulation time; preview and wall-clock speed may be lower.`;}
    }else{slowSince=null;$('performanceNotice').hidden=true;}
    if(state.error) setMessage(safeText(state.error),true);
    if(state.phase==='error') { $('emptyTitle').textContent='The simulator needs attention';$('emptyDescription').textContent=safeText(state.error)||'See the server error below.'; }
    if(state.phase!==lastPhase) {
      if(!isActive())releaseKeys(false);
      if(state.phase==='submitted') {submitError=false;loadEpisodes();setMessage(state.mode==='practice'?'Practice finished. No dataset was recorded.':'Episode saved. Check its validation status and download below.');}
      if(state.phase==='ready'&&previous.phase==='loading'){setMessage('');$('emptyTitle').textContent='Your station is ready';$('emptyDescription').textContent='Waiting for the first camera frame.';}
      lastPhase=state.phase;
    }
    renderTasks();renderActions();
  }
  function setImage(id,encoded) {
    if(!encoded)return;
    const img=$(id);img.src=encoded.startsWith('data:')?encoded:`data:image/jpeg;base64,${encoded}`;img.hidden=false;
    img.parentElement.querySelector('.camera-placeholder')?.setAttribute('hidden','');
  }
  function updateFrame(images) {
    if(!images)return;
    if(images.main){setImage('mainFrame',images.main);frameSeen=true;$('emptyFrame').hidden=true;}
    setImage('leftFrame',images.left);setImage('rightFrame',images.right);setImage('wristFrame',images.wrist);setImage('trainingWristFrame',images.wrist);
    if(images.wrist)$('wristInset').hidden=false;
  }
  function connect() {
    clearTimeout(reconnectTimer);
    socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws?client_id=${encodeURIComponent(playerID)}`);
    socket.onopen=()=>{connected=true;state.locked=false;$('connectionText').textContent='Connected';$('reconnectButton').hidden=true;renderTasks();renderActions();inputMessage();};
    socket.onmessage=(event)=>{try{const message=JSON.parse(event.data);if(message.type==='status')updateStatus(message.status||message);else if(message.type==='frame')updateFrame(message.images);else if(message.type==='error'){if(message.locked)updateStatus({locked:true});setMessage(message.error||message.message||'Simulator connection error.',true);}}catch(error){console.warn('Invalid simulator message',error);}};
    socket.onclose=(event)=>{connected=false;keys.clear();const locked=event.code===1013||state.locked;if(locked){state.locked=true;$('connectionText').textContent='Session in use';$('reconnectButton').hidden=false;}else{$('connectionText').textContent='Reconnecting…';reconnectTimer=setTimeout(connect,1800);}renderTasks();renderActions();if(frameSeen)$('focusHint').textContent='Connection lost · controls released';};
    socket.onerror=()=>{$('connectionText').textContent='Connection error';};
  }
  async function resetScene() {
    releaseKeys();submitError=false;setMessage('');
    const seed=Number($('seedInput').value);
    if(!Number.isInteger(seed)||seed<0)throw new Error('Enter a non-negative whole number for the scene seed.');
    const result=await api('/api/reset',{task_id:selectedTask,seed,mode:$('sessionMode').value});
    if(!result.phase&&!result.status) await refreshStatus();
  }
  async function refreshStatus() {
    try {const response=await fetch('/api/status');if(!response.ok)throw new Error('Unable to read simulator status.');updateStatus(await response.json());}
    catch(error){setMessage(error.message,true);}
  }
  function episodeQA(episode) {
    const qa=episode.qa||episode.validation||{};
    if(typeof qa==='string')return {label:qa,pending:!['passed','verified','valid'].includes(qa.toLowerCase())};
    if(qa.structural_valid===false||qa.passed===false||qa.valid===false||qa.status==='failed')return {label:'Validation failed',pending:true};
    if(qa.action_replay?.passed===true||qa.replay?.passed===true||qa.replay_passed===true||qa.replay_verified===true)return {label:'Replay verified',pending:false};
    if(qa.action_replay?.passed===false)return {label:'Action replay failed',pending:true};
    if(qa.structural_valid===true||qa.passed===true||qa.valid===true||qa.structural?.passed===true||qa.structural_passed===true||qa.status==='passed')return {label:'Structure valid · replay pending',pending:true};
    return {label:'Validation pending',pending:true};
  }
  async function loadEpisodes() {
    try {
      const response=await fetch('/api/episodes');if(!response.ok)throw new Error('Could not load saved episodes.');
      const payload=await response.json();const episodes=Array.isArray(payload)?payload:payload.episodes||[];
      const container=$('episodes');container.replaceChildren();
      if(!episodes.length){const empty=document.createElement('div');empty.className='episodes-empty';empty.textContent='No submitted demonstrations yet.';const sub=document.createElement('span');sub.textContent='Collected sessions will appear here with their validation results.';empty.append(sub);container.append(empty);return;}
      episodes.slice().sort((a,b)=>String(b.episode_id||b.id).localeCompare(String(a.episode_id||a.id))).forEach(episode=>{
        const id=episode.episode_id||episode.id;if(!id)return;
        const row=document.createElement('div');row.className='episode-row';
        const emblem=document.createElement('div');emblem.className='episode-emblem';emblem.textContent=episode.success===true?'✓':'↗';
        const detail=document.createElement('div');detail.className='episode-detail';
        const title=document.createElement('strong');title.textContent=taskTitle(episode.task_id);
        const metadata=document.createElement('p');let date='';
        const recordedAt=episode.created_at??episode.submitted_at;
        if(recordedAt!==undefined&&recordedAt!==null&&recordedAt!==''){
          const numeric=typeof recordedAt==='number'||(typeof recordedAt==='string'&&/^\d+(?:\.\d+)?$/.test(recordedAt))?Number(recordedAt):null;
          const parsed=new Date(numeric===null?recordedAt:Math.abs(numeric)<1e12?numeric*1000:numeric);
          if(!Number.isNaN(parsed.getTime()))date=`${parsed.toLocaleString()} · `;
        }
        metadata.textContent=`${date}${id}`;detail.append(title,metadata);
        const metrics=document.createElement('div');metrics.className='episode-metrics';const result=document.createElement('div');result.textContent=episode.success===true?'Task success':episode.success===false?'Incomplete attempt':'Result not reported';metrics.append(result);
        const stats=document.createElement('div');stats.textContent=`${episode.steps??episode.frames??'—'} steps${episode.mode?` · ${episode.mode}`:''}`;metrics.append(stats);
        const qa=episodeQA(episode);const badge=document.createElement('span');badge.className=`episode-badge${qa.pending?' pending':''}`;badge.textContent=qa.label;
        const download=document.createElement('a');download.className='download-link';download.href=`/api/episodes/${encodeURIComponent(id)}/download`;download.textContent='Download ↗';download.setAttribute('download','');
        const actions=document.createElement('div');actions.className='episode-actions';
        const replay=document.createElement('button');replay.className='quiet episode-replay';replay.textContent='Replay ↻';replay.disabled=pending||!connected||state.locked||isActive()||['paused','completed','stopped','submitting','replaying'].includes(state.phase);replay.onclick=()=>act(async()=>{releaseKeys();await api('/api/replay',{episode_id:id});});
        const report=document.createElement('a');report.className='quiet report-link';report.href=`/api/episodes/${encodeURIComponent(id)}/report`;report.target='_blank';report.rel='noopener';report.textContent='QA report';
        actions.append(replay,report,download);row.append(emblem,detail,metrics,badge,actions);container.append(row);
      });
    }catch(error){setMessage(error.message,true);}
  }
  $('resetButton').onclick=()=>act(resetScene);
  $('sessionMode').onchange=()=>act(resetScene);
  $('startButton').onclick=()=>act(async()=>{submitError=false;setMessage('');if(state.phase==='submitted')await resetScene();await api('/api/start');$('viewport').focus({preventScroll:true});});
  $('pauseButton').onclick=()=>act(async()=>{releaseKeys();await api('/api/pause');});
  const resume=()=>act(async()=>{releaseKeys();await api('/api/resume');$('viewport').focus({preventScroll:true});});
  $('resumeButton').onclick=resume;$('overlayResume').onclick=resume;
  const submit=()=>act(async()=>{releaseKeys();if(state.mode==='practice'){await api('/api/discard');setMessage('Practice finished. No dataset was recorded.');return;}try {const episode=await api('/api/submit');submitError=false;await refreshStatus();await loadEpisodes();if(episode.status==='empty')setMessage('No control steps were recorded. Start a new session to collect a demonstration.',true);}catch(error){submitError=true;throw error;}});
  $('submitButton').onclick=submit;$('retrySubmitButton').onclick=submit;
  $('discardButton').onclick=()=>act(async()=>{releaseKeys();await api('/api/discard');submitError=false;setMessage(state.mode==='practice'?'Practice reset. Ready for a fresh start.':'Attempt marked as discarded and preserved for traceability. Ready for a fresh start.');await loadEpisodes();});
  $('refreshEpisodes').onclick=loadEpisodes;
  $('reconnectButton').onclick=()=>{state.locked=false;setMessage('');$('connectionText').textContent='Connecting…';$('reconnectButton').hidden=true;connect();};
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>act(async()=>{await api('/api/view',{view:button.dataset.view});mainView=button.dataset.view;document.querySelectorAll('[data-view]').forEach(other=>other.classList.toggle('selected',other===button));$('mainCameraLabel').textContent=`PLAYER VIEW · ${mainView.toUpperCase()}`;}));
  const dialog=$('controlsDialog');
  $('helpButton').onclick=()=>{releaseKeys(true);dialog.showModal();};
  const closeHelp=()=>{dialog.close();$('viewport').focus({preventScroll:true});};
  $('closeHelp').onclick=closeHelp;$('gotItButton').onclick=closeHelp;
  dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeHelp();}});
  $('viewport').onfocus=()=>{$('focusHint').textContent='Keyboard control active';};
  $('viewport').onblur=()=>{releaseKeys(true);$('focusHint').textContent='Click the view to take control';};
  window.addEventListener('keydown',event=>{
    if(isInput(event.target)||dialog.open)return;
    if(event.key==='?'&&!event.repeat){event.preventDefault();releaseKeys(true);dialog.showModal();return;}
    if(event.code==='Escape'&&isActive()){event.preventDefault();releaseKeys(true);return;}
    if(document.activeElement!==$('viewport')||!isActive()||state.locked)return;
    if(movementCodes.has(event.code)||['Space','Tab'].includes(event.code))event.preventDefault();else return;
    if(event.code==='Space'){if(!event.repeat){gripper=!gripper;$('gripperState').textContent=gripper?'CLOSED':'OPEN';inputMessage();}return;}
    if(event.code==='Tab'){if(!event.repeat){keys.clear();controlMode=controlMode==='arm'?'base':'arm';$('controlMode').textContent=controlMode.toUpperCase();inputMessage();}return;}
    keys.add(event.code);inputMessage();
  });
  window.addEventListener('keyup',event=>{if(keys.delete(event.code)){if(!isInput(event.target))event.preventDefault();inputMessage();}});
  window.addEventListener('blur',()=>releaseKeys(true));
  document.addEventListener('visibilitychange',()=>{if(document.hidden)releaseKeys(true);});
  window.addEventListener('pagehide',()=>{releaseKeys(true);socket?.close();});
  setInterval(()=>{if(connected&&isActive())inputMessage();},100);
  renderTasks();renderActions();refreshStatus();loadEpisodes();connect();
})();
