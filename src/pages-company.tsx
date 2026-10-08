import {useEffect,useMemo,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {Activity,AlertTriangle,BrainCircuit,CheckCircle2,Clock,FileText,ListChecks,MessageSquare,Mic2,Phone,PhoneOutgoing,Plus,Radio,Route as RouteIcon,Sparkles,Trash2,X} from 'lucide-react';
import {Area,AreaChart,CartesianGrid,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {Badge,Button,Empty,Field,PageHead,PresetPicker,Tabs,useApp,formatDate,formatDuration} from './app';
import {api} from './lib/api';
import {ConnectionSetup} from './connection-setup';
import {presetKindHelp,presetKindLabel,presetsOfKind} from './lib/presets';
import type {ChannelType,Persona,PresetKind} from './lib/types';

/**
 * Company-facing screens, rewritten so that no model name, threshold, provider
 * or tuning value appears. Behaviour is chosen through named presets; the values
 * behind them live in the platform portal.
 */

const CHANNEL_LABEL:Record<ChannelType,string>={sim:'Phone line',whatsapp_call:'WhatsApp calls',whatsapp_message:'WhatsApp messages'};
const CHANNEL_BLURB:Record<ChannelType,string>={
  sim:'A normal phone number people can dial from any phone.',
  whatsapp_call:'Voice calls placed and received inside WhatsApp.',
  whatsapp_message:'Written messages, for sending details in writing.',
};
/** What a company is told about a channel. Never the underlying status word. */
const STATUS_VIEW:Record<string,[string,string,string]>={
  connected:['success','Active','Answering calls now.'],
  verifying:['warning','Testing','We are placing test calls to make sure it works.'],
  pending:['warning','Being set up','We are doing the work. Nothing is needed from you.'],
  disconnected:['','Not requested','Ask us to add this and we will set it up.'],
  error:['danger','Needs our attention','We have been notified and are working on it.'],
};
const KB_STATE:Record<string,[string,string]>={ready:['success','Ready'],indexing:['warning','Preparing'],failed:['danger','Needs attention'],empty:['','No documents yet']};

// ------------------------------------------------------------------ dashboard

export function Dashboard(){
  const {store,org,t,readiness}=useApp();
  const calls=useMemo(()=>store.calls.filter(x=>x.orgId===org.id),[store.calls,org.id]);
  const live=calls.filter(c=>c.status==='active');
  const openQuestions=store.unanswered.filter(x=>x.orgId===org.id&&x.status==='open');
  const channels=store.channels.filter(x=>x.orgId===org.id);
  const needsAttention=channels.filter(c=>c.status==='error'||c.status==='pending'||c.status==='verifying');
  const dayMs=86400000, today=Date.now();
  const todayCalls=calls.filter(c=>today-new Date(c.startedAt).getTime()<dayMs);
  const talkMinutes=Math.round(todayCalls.reduce((n,c)=>n+c.durationSeconds,0)/60);
  const answered=todayCalls.filter(c=>c.status!=='failed').length;
  const answerRate=todayCalls.length?Math.round(answered/todayCalls.length*100):0;
  const messagesSent=store.messages.filter(m=>m.orgId===org.id).length;
  const series=useMemo(()=>Array.from({length:14},(_,i)=>{
    const start=today-(13-i)*dayMs;
    const inDay=calls.filter(c=>{const ts=new Date(c.startedAt).getTime();return ts>=start&&ts<start+dayMs});
    return {day:new Date(start).toLocaleDateString(undefined,{month:'short',day:'numeric'}),
      phone:inDay.filter(c=>c.channelType==='sim').length,
      whatsapp:inDay.filter(c=>c.channelType!=='sim').length};
  }),[calls,today]);
  const setupDone=org.onboardingStep>=6;

  if(!setupDone)return <div className="stack">
    <PageHead eyebrow="Welcome" title={'Let’s get '+org.name+' answering calls'} description="Work through these while we set your number up in the background."/>
    <div className="card stack">
      {[['Create your agent and write its greeting',readiness.personaReady,'/app/agents'],
        ['Upload the material it should answer from',readiness.knowledgeBaseReady,'/app/knowledge'],
        ['Decide which calls use which material',readiness.routingReady,'/app/routing'],
        ['Invite your team',store.memberships.filter(m=>m.orgId===org.id).length>1,'/app/team'],
        ['Your number goes live',readiness.phoneChannelReady||readiness.whatsappCallingReady,'/app/channels']].map(([label,done,to])=>
        <div className="row between checklist-row" key={label as string}>
          <span className="row">{done?<CheckCircle2 size={18} className="success"/>:<Clock size={18} className="muted"/>}{t(label as string)}</span>
          <Button small variant={done?'outline':''} to={to as string}>{done?t('Review'):t('Do this')}</Button>
        </div>)}
    </div>
  </div>;

  return <div className="stack dashboard-page">
    <PageHead eyebrow="Overview" title={org.name} description="What needs you right now."/>

    <div className="grid cols-3">
      <Link className="card lift attention-card" to="/app/live">
        <div className="row between"><span className="stat-label">{t('Calls live right now')}</span>{live.length>0&&<span className="dash-live-dot"/>}</div>
        <div className="stat-number mono">{live.length}</div>
        <div className="small muted">{live.length?t('Someone is on the line'):t('Nothing live at the moment')}</div>
      </Link>
      <Link className="card lift attention-card" to="/app/unanswered">
        <span className="stat-label">{t('Questions with no answer')}</span>
        <div className={'stat-number mono '+(openQuestions.length?'warning':'')}>{openQuestions.length}</div>
        <div className="small muted">{openQuestions.length?t('Callers asked these and left without an answer'):t('Your agent answered everything')}</div>
      </Link>
      <Link className="card lift attention-card" to="/app/channels">
        <span className="stat-label">{t('Numbers needing attention')}</span>
        <div className={'stat-number mono '+(needsAttention.length?'warning':'')}>{needsAttention.length}</div>
        <div className="small muted">{needsAttention.length?t('Setup or a fault in progress'):t('All your numbers are active')}</div>
      </Link>
    </div>

    {openQuestions.length>0&&<div className="card stack">
      <div className="row between"><h2>{t('Most asked, still unanswered')}</h2><Button small variant="outline" to="/app/unanswered">See all</Button></div>
      {openQuestions.slice(0,3).map(q=><div className="row between" key={q.id}>
        <span>{q.question}</span>
        <span className="row"><span className="small muted mono">{q.askedCount}×</span><Button small to={'/app/knowledge/'+q.knowledgeBaseId}>Add an answer</Button></span>
      </div>)}
    </div>}

    <div className="grid cols-4">
      <div className="card"><div className="stat-label">{t('Calls today')}</div><div className="stat-number mono">{todayCalls.length}</div></div>
      <div className="card"><div className="stat-label">{t('Talk time today')}</div><div className="stat-number mono">{talkMinutes}</div><div className="small muted">{t('minutes')}</div></div>
      <div className="card"><div className="stat-label">{t('Answered')}</div><div className="stat-number mono">{answerRate}%</div></div>
      <div className="card"><div className="stat-label">{t('Messages sent')}</div><div className="stat-number mono">{messagesSent}</div></div>
    </div>

    <div className="card chart">
      <h2>{t('Calls over the last two weeks')}</h2>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={series}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)"/>
          <XAxis dataKey="day" stroke="var(--muted-foreground)" fontSize={11}/>
          <YAxis stroke="var(--muted-foreground)" fontSize={11} allowDecimals={false}/>
          <Tooltip contentStyle={{background:'var(--card)',border:'1px solid var(--border)',borderRadius:8}}/>
          <Area type="monotone" dataKey="phone" name={t('Phone')} stackId="1" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.25}/>
          <Area type="monotone" dataKey="whatsapp" name={t('WhatsApp')} stackId="1" stroke="var(--chart-2)" fill="var(--chart-2)" fillOpacity={0.25}/>
        </AreaChart>
      </ResponsiveContainer>
    </div>

    <div className="card stack">
      <div className="row between"><h2>{t('Recent calls')}</h2><Button small variant="outline" to="/app/history">See all</Button></div>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('When')}</th><th>{t('Number')}</th><th>{t('Channel')}</th><th>{t('Knowledge used')}</th><th>{t('Length')}</th><th>{t('Result')}</th></tr></thead>
        <tbody>{calls.slice(0,8).map(c=><tr key={c.id}>
          <td className="small"><Link to={'/app/history/'+c.id}>{formatDate(c.startedAt)}</Link></td>
          <td className="mono">{c.phoneNumber}</td>
          <td className="small">{t(CHANNEL_LABEL[c.channelType])}</td>
          <td className="small">{c.knowledgeBaseName}</td>
          <td className="mono">{formatDuration(c.durationSeconds)}</td>
          <td>{c.outcome}</td>
        </tr>)}</tbody>
      </table></div>
    </div>
  </div>;
}

// ------------------------------------------------------------------ knowledge

export function Knowledge(){
  const {store,org,run,canManage,t}=useApp();
  const bases=store.knowledgeBases.filter(x=>x.orgId===org.id);
  const [creating,setCreating]=useState(false);
  const [name,setName]=useState('');
  const [purpose,setPurpose]=useState('');
  const create=async()=>{
    await run(()=>api.createKnowledgeBase(org.id,name,purpose),'Knowledge base created. Add some documents next.');
    setCreating(false);setName('');setPurpose('');
  };
  return <div className="stack">
    <PageHead eyebrow="Knowledge" title="What your agent knows"
      description="Keep separate subjects in separate knowledge bases. Your agent searches a smaller, more relevant set that way."
      action={canManage&&<Button onClick={()=>setCreating(true)}><Plus size={15}/> New knowledge base</Button>}/>
    {bases.length===0
      ?<Empty icon={BrainCircuit} title="Nothing uploaded yet" body="Add the material your agent should answer from — admissions, fees, anything your callers ask about." action={canManage&&<Button onClick={()=>setCreating(true)}>Create the first one</Button>}/>
      :<div className="grid cols-2">{bases.map(kb=>{
        const [tone,label]=KB_STATE[kb.status];
        const docs=store.documents.filter(d=>d.kbId===kb.id);
        const agents=store.agents.filter(a=>a.orgId===org.id&&(a.defaultKnowledgeBaseId===kb.id||a.knowledgeBaseIds.includes(kb.id)));
        const rules=store.routingRules.filter(r=>r.orgId===org.id&&r.outcome.knowledgeBaseIds?.includes(kb.id));
        return <Link className="card lift stack" to={'/app/knowledge/'+kb.id} key={kb.id}>
          <div className="row between">
            <strong>{kb.name}{kb.isDefault&&<span className="small muted"> · {t('default')}</span>}</strong>
            <Badge tone={tone}>{t(label)}</Badge>
          </div>
          <p className="small muted">{kb.purpose}</p>
          <div className="row between small">
            <span className="mono">{docs.length} {t('documents')}</span>
            <span className="mono">{kb.answeredLast30d} {t('answers in 30 days')}</span>
          </div>
          <div className="small muted">
            {agents.length>0&&<>{t('Used by')} {agents.map(a=>a.name).join(', ')}</>}
            {rules.length>0&&<> · {rules.length} {t('routing rules')}</>}
            {agents.length===0&&rules.length===0&&t('Not used by any agent yet')}
          </div>
        </Link>;
      })}</div>}
    {creating&&<div className="modal-backdrop" onClick={()=>setCreating(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('New knowledge base')}</h2><button className="icon-btn" onClick={()=>setCreating(false)}><X size={18}/></button></div>
      <Field label="Name" value={name} onChange={setName} placeholder="Admissions 2026"/>
      <Field label="What is it for?" value={purpose} onChange={setPurpose} rows={3} placeholder="Entry requirements, deadlines and the application process." help="This is just a note for your team."/>
      <div className="row"><Button variant="outline" onClick={()=>setCreating(false)}>Cancel</Button><Button onClick={create} disabled={!name.trim()}>Create</Button></div>
    </div></div>}
  </div>;
}

// ----------------------------------------------------------------- agents ---

export function AgentDetail(){
  const {store,org,run,canManage,t}=useApp();
  const {id}=useParams();
  const agent=store.agents.find(x=>x.id===id&&x.orgId===org.id);
  const [tab,setTab]=useState('Persona');
  const [saved,setSaved]=useState('');
  const [testNumber,setTestNumber]=useState('');
  const [testKb,setTestKb]=useState('');
  const [testFrom,setTestFrom]=useState('');
  const [confirmDelete,setConfirmDelete]=useState(false);
  const navigate=useNavigate();
  if(!agent)return <Empty icon={Mic2} title="Agent not found" body="It may have been deleted." action={<Button to="/app/agents">Back to agents</Button>}/>;
  const persona=store.personas.find(x=>x.id===agent.personaId)!;
  const bases=store.knowledgeBases.filter(x=>x.orgId===org.id);
  const channels=store.channels.filter(x=>x.orgId===org.id);
  const savePersona=(patch:Partial<Persona>)=>{setSaved('Saving…');run(()=>api.updatePersona(persona.id,patch)).then(()=>setSaved('Saved'))};
  const compiled=[
    'You are '+persona.agentName+'.',
    persona.roleDescription,
    'Open every call with exactly: "'+persona.greeting+'"',
    persona.languagePolicy,
    'Tone: '+persona.toneNotes,
    persona.forbiddenPhrases.length?'Never say: '+persona.forbiddenPhrases.join('; '):'',
    'Escalation: '+persona.escalationRules,
    'Closing: '+persona.closingBehaviour,
    'Answer only from the material provided. If it is not there, say you will find out.',
  ].filter(Boolean).join('\n\n');
  const toggleKb=(kbId:string)=>{
    const list=agent.knowledgeBaseIds.includes(kbId)?agent.knowledgeBaseIds.filter(x=>x!==kbId):[...agent.knowledgeBaseIds,kbId];
    run(()=>api.updateAgent(agent.id,{knowledgeBaseIds:list}),'Saved');
  };
  const toggleChannel=(channelId:string)=>{
    const list=agent.channelIds.includes(channelId)?agent.channelIds.filter(x=>x!==channelId):[...agent.channelIds,channelId];
    run(()=>api.updateAgent(agent.id,{channelIds:list}),'Saved');
  };

  return <div className="stack">
    <PageHead eyebrow="Agent" title={agent.name} description={persona.roleDescription}
      action={<div className="row">
        <Badge tone={agent.status==='live'?'live':agent.status==='paused'?'warning':''}>{t(agent.status)}</Badge>
        {canManage&&<Button variant="outline" onClick={()=>run(()=>api.updateAgent(agent.id,{status:agent.status==='live'?'paused':'live'}),agent.status==='live'?'Agent paused.':'Agent is live.')}>{agent.status==='live'?t('Pause'):t('Go live')}</Button>}
        {canManage&&<Button variant="outline" onClick={()=>setConfirmDelete(true)}><Trash2 size={15}/> Delete</Button>}
      </div>}/>
    <Tabs items={['Persona','How it speaks','Knowledge','Where it answers','Try it']} active={tab} onChange={setTab}/>

    {tab==='Persona'&&<div className="split">
      <div className="card stack">
        <div className="row between"><h2>{t('Who your agent is')}</h2><span className="small muted">{saved}</span></div>
        <Field label="Name callers hear" value={persona.agentName} onChange={v=>savePersona({agentName:v})} disabled={!canManage}/>
        <div className="field"><label>{t('Speaks about itself as')}</label>
          <select className="select" value={persona.gender} disabled={!canManage} onChange={e=>savePersona({gender:e.target.value})}>
            <option value="female">{t('A woman')}</option><option value="male">{t('A man')}</option><option value="neutral">{t('Neither')}</option>
          </select>
          <div className="help">{t('In Urdu and many other languages the verbs change with the speaker. This keeps your agent consistent.')}</div></div>
        <Field label="Greeting, said word for word" value={persona.greeting} onChange={v=>savePersona({greeting:v})} rows={2} disabled={!canManage}
          help="Keep it short. It is the first thing every caller hears, on a line that may be poor."/>
        <Field label="What it is here to do" value={persona.roleDescription} onChange={v=>savePersona({roleDescription:v})} rows={3} disabled={!canManage}/>
        <Field label="Which language it replies in" value={persona.languagePolicy} onChange={v=>savePersona({languagePolicy:v})} rows={2} disabled={!canManage}/>
        <Field label="How it should sound" value={persona.toneNotes} onChange={v=>savePersona({toneNotes:v})} rows={2} disabled={!canManage}/>
        <Field label="Things it must never say" value={persona.forbiddenPhrases.join('; ')} onChange={v=>savePersona({forbiddenPhrases:v.split(';').map(x=>x.trim()).filter(Boolean)})} rows={2} disabled={!canManage} help="Separate each with a semicolon."/>
        <Field label="When to hand over to a person" value={persona.escalationRules} onChange={v=>savePersona({escalationRules:v})} rows={2} disabled={!canManage}/>
        <Field label="How to close a call" value={persona.closingBehaviour} onChange={v=>savePersona({closingBehaviour:v})} rows={2} disabled={!canManage}/>
      </div>
      <div className="card stack side-panel">
        <h2>{t('What your agent has been told')}</h2>
        <p className="small muted">{t('This is built from the form beside it, and updates as you type.')}</p>
        <pre className="compiled">{compiled}</pre>
      </div>
    </div>}

    {tab==='How it speaks'&&<div className="stack">
      <PresetGroup kind="pace"/>
      <PresetGroup kind="voiceQuality"/>
      <PresetGroup kind="strictness"/>
    </div>}

    {tab==='Knowledge'&&<div className="card stack">
      <h2>{t('What this agent can answer from')}</h2>
      <p className="small muted">{t('Tick everything this agent should be able to look in. The one marked default is used when nothing else decides.')}</p>
      {bases.length===0
        ?<Empty icon={BrainCircuit} title="No knowledge bases yet" body="Create one and upload your material." action={<Button to="/app/knowledge">Go to knowledge</Button>}/>
        :<div className="stack">{bases.map(kb=>{
          const on=agent.knowledgeBaseIds.includes(kb.id);
          const isDefault=agent.defaultKnowledgeBaseId===kb.id;
          return <div className="row between kb-pick" key={kb.id}>
            <label className="row"><input type="checkbox" checked={on} disabled={!canManage} onChange={()=>toggleKb(kb.id)}/>
              <span><strong>{kb.name}</strong><div className="small muted">{kb.purpose}</div></span></label>
            <div className="row">
              {isDefault
                ?<Badge tone="success">{t('Default for this agent')}</Badge>
                :on&&canManage&&<Button small variant="outline" onClick={()=>run(()=>api.updateAgent(agent.id,{defaultKnowledgeBaseId:kb.id}),'Default changed.')}>Make default</Button>}
            </div>
          </div>;
        })}</div>}
      <div className="notice"><RouteIcon size={15}/> {t('A routing rule can override this for particular calls.')} <Link to="/app/routing">{t('Set up routing')}</Link></div>
    </div>}

    {tab==='Where it answers'&&<div className="card stack">
      <h2>{t('Which numbers this agent answers on')}</h2>
      <p className="small muted">{t('Tick every number this agent should pick up. Other agents can answer the rest.')}</p>
      {channels.length===0
        ?<Empty icon={Radio} title="No numbers yet" body="Ask us for a number and it will appear here." action={<Button to="/app/channels">Your numbers</Button>}/>
        :(['sim','whatsapp_call','whatsapp_message'] as ChannelType[]).map(type=>{
          const group=channels.filter(c=>c.type===type);
          if(!group.length)return null;
          return <div className="stack" key={type}>
            <div className="nav-group">{t(CHANNEL_LABEL[type])}</div>
            {group.map(ch=>{
              const ready=ch.status==='connected';
              const takenBy=store.agents.filter(a=>a.id!==agent.id&&a.channelIds.includes(ch.id)).map(a=>a.name);
              return <div className="row between kb-pick" key={ch.id}>
                <label className="row"><input type="checkbox" checked={agent.channelIds.includes(ch.id)} disabled={!canManage||!ready} onChange={()=>toggleChannel(ch.id)}/>
                  <span><strong>{ch.label}</strong><div className="small muted mono">{ch.displayNumber||t('Number not assigned yet')}</div></span></label>
                <span className="small muted">
                  {!ready&&t('Not active yet')}
                  {ready&&takenBy.length>0&&t('Also answered by')+' '+takenBy.join(', ')}
                </span>
              </div>;
            })}
          </div>;
        })}
    </div>}

    {tab==='Try it'&&<div className="card stack">
      <h2>{t('Place a test call')}</h2>
      <p className="small muted">{t('We will call the number you enter and your agent will answer it.')}</p>
      <div className="field-grid">
        <Field label="Number to call" value={testNumber} onChange={setTestNumber} placeholder="+92 300 123 4567"/>
        <div className="field"><label>{t('Call from')}</label>
          <select className="select" value={testFrom} onChange={e=>setTestFrom(e.target.value)}>
            {channels.filter(c=>c.status==='connected').map(c=><option key={c.id} value={c.id}>{c.label} — {c.displayNumber}</option>)}
          </select></div>
        <div className="field"><label>{t('Knowledge to use')}</label>
          <select className="select" value={testKb} onChange={e=>setTestKb(e.target.value)}>
            <option value="">{t('The agent default')} — {bases.find(b=>b.id===agent.defaultKnowledgeBaseId)?.name}</option>
            {bases.filter(b=>b.id!==agent.defaultKnowledgeBaseId).map(b=><option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <div className="help">{t('Choosing here overrides every rule, just for this call.')}</div></div>
      </div>
      <div className="row"><Button disabled={!canManage||!testNumber.trim()} onClick={()=>run(()=>api.createTestCall(org.id,agent.id,testNumber,testKb||undefined,testFrom||undefined),'Calling now. Watch it on the Live screen.')}><Phone size={15}/> Call now</Button>
        <Button variant="outline" to="/app/live">Open live</Button></div>
    </div>}

    {confirmDelete&&<div className="modal-backdrop" onClick={()=>setConfirmDelete(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('Delete')} {agent.name}?</h2><button className="icon-btn" onClick={()=>setConfirmDelete(false)}><X size={18}/></button></div>
      <p>{t('This removes the agent and its persona. Any routing rule pointing at it will fall back to your default agent. Calls it already handled stay in your history.')}</p>
      <div className="row"><Button variant="outline" onClick={()=>setConfirmDelete(false)}>Cancel</Button>
        <Button variant="danger" onClick={async()=>{await run(()=>api.deleteAgent(agent.id),'Agent deleted.');navigate('/app/agents')}}>Delete it</Button></div>
    </div></div>}
  </div>;
}

/** One named choice, with its consequence. No numbers anywhere. */
function PresetGroup({kind}:{kind:PresetKind}){
  const {store,org,run,canManage,t}=useApp();
  const options=presetsOfKind(store.presets,kind);
  const field=kind==='strictness'?'strictnessPreset':kind==='pace'?'pacePreset':'voiceQualityPreset';
  const value=org[field as 'strictnessPreset'|'pacePreset'|'voiceQualityPreset'];
  return <div className="card stack">
    <div><h2>{t(presetKindLabel[kind])}</h2><p className="small muted">{t(presetKindHelp[kind])}</p></div>
    <PresetPicker options={options} value={value} disabled={!canManage}
      onChange={id=>run(()=>api.updateOrganization(org.id,{[field]:id}),'Saved. New calls use this straight away.')}/>
  </div>;
}

// ---------------------------------------------------------------- channels --

export function Channels(){
  const {store,org,run,canManage,t}=useApp();
  const channels=store.channels.filter(x=>x.orgId===org.id);
  const tasks=store.provisioning.filter(x=>x.orgId===org.id);
  const [adding,setAdding]=useState<ChannelType|null>(null);
  const [label,setLabel]=useState('');
  const viewFor=(ch:typeof channels[number])=>{
    const task=tasks.find(x=>x.channelId===ch.id);
    const key=task&&task.state!=='active'
      ?(task.state==='blocked'?'error':task.state==='testing'?'verifying':task.state==='requested'?'disconnected':'pending')
      :ch.status;
    return STATUS_VIEW[key]||STATUS_VIEW.disconnected;
  };
  const add=async()=>{
    if(!adding||!label.trim())return;
    await run(()=>api.addChannel(org.id,adding,label),'Requested. We will set it up and let you know.');
    setAdding(null);setLabel('');
  };
  return <div className="stack">
    <PageHead eyebrow="Numbers" title="Your numbers"
      description="Hold as many as you need. Give each one a purpose, then send its calls wherever you like."/>

    {(['sim','whatsapp_call','whatsapp_message'] as ChannelType[]).map(type=>{
      const group=channels.filter(c=>c.type===type);
      return <div className="card stack" key={type}>
        <div className="row between">
          <div><h2>{t(CHANNEL_LABEL[type])}</h2><p className="small muted">{t(CHANNEL_BLURB[type])}</p></div>
          {canManage&&<Button small variant="outline" onClick={()=>{setAdding(type);setLabel('')}}><Plus size={14}/> Add another</Button>}
        </div>
        {group.length===0
          ?<Empty icon={Radio} title={'No '+t(CHANNEL_LABEL[type]).toLowerCase()+' yet'} body="Ask us for one and we will set it up for you." action={canManage&&<Button small onClick={()=>{setAdding(type);setLabel('')}}>Request one</Button>}/>
          :<div className="stack">{group.map(ch=>{
            const [tone,statusLabel,blurb]=viewFor(ch);
            const agents=store.agents.filter(a=>a.orgId===org.id&&a.channelIds.includes(ch.id));
            const calls=store.calls.filter(c=>c.channelId===ch.id).length;
            return <div className="row between number-row" key={ch.id}>
              <div className="number-main">
                <span className="row" style={{gap:8}}>
                  <Link to={'/app/channels/'+ch.id}><strong>{ch.label}</strong></Link>
                  {ch.isPrimary&&<Badge tone="success">{t('Main')}</Badge>}
                </span>
                <div className="mono">{ch.displayNumber||t('Number not assigned yet')}</div>
                <div className="small muted">
                  {agents.length?t('Answered by')+' '+agents.map(a=>a.name).join(', '):t('No agent answers this yet')}
                  {calls>0&&' · '+calls+' '+t('calls')}
                </div>
              </div>
              <div className="number-side">
                <Badge tone={tone}>{t(statusLabel)}</Badge>
                <div className="small muted">{t(blurb)}</div>
                {canManage&&!ch.isPrimary&&<button className="link-btn small" onClick={()=>run(()=>api.setPrimaryChannel(org.id,ch.id),'Set as the main number.')}>{t('Make main')}</button>}
              </div>
            </div>;
          })}</div>}
      </div>;
    })}

    {adding&&<div className="modal-backdrop" onClick={()=>setAdding(null)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('Request another')} {t(CHANNEL_LABEL[adding]).toLowerCase()}</h2><button className="icon-btn" onClick={()=>setAdding(null)}><X size={18}/></button></div>
      <Field label="What is this number for?" value={label} onChange={setLabel} placeholder="Fee office line"
        help="A name your team will recognise. You can change it later."/>
      <div className="notice">{t('We will get the number, set it up and test it. You will see its progress here and we will email you when it is live.')}</div>
      <div className="row"><Button variant="outline" onClick={()=>setAdding(null)}>Cancel</Button><Button onClick={add} disabled={!label.trim()}>Request it</Button></div>
    </div></div>}
  </div>;
}

export function ChannelDetail(){
  const {store,org,run,canManage,t}=useApp();
  const {id}=useParams();
  const navigate=useNavigate();
  const channel=store.channels.find(x=>x.orgId===org.id&&x.id===id);
  const task=store.provisioning.find(x=>x.channelId===id);
  const calls=store.calls.filter(c=>c.channelId===id);
  const [usage,setUsage]=useState<{agents:string[];rules:string[];campaigns:string[]}|null>(null);
  const [confirmRemove,setConfirmRemove]=useState(false);
  useEffect(()=>{if(id)api.channelUsage(id).then(setUsage)},[id,store.agents,store.routingRules,store.campaigns]);
  if(!channel)return <Empty icon={Radio} title="Number not found" body="It may have been removed." action={<Button to="/app/channels">Your numbers</Button>}/>;
  const steps:[string,string][]=[['requested','Requested'],['setting_up','Being set up'],['testing','Testing'],['active','Active']];
  const currentStep=task?steps.findIndex(s=>s[0]===task.state):(channel.status==='connected'?3:0);
  const [tone,statusLabel,blurb]=STATUS_VIEW[channel.status]||STATUS_VIEW.disconnected;
  const agents=store.agents.filter(a=>a.orgId===org.id&&a.channelIds.includes(channel.id));
  const rules=store.routingRules.filter(r=>r.orgId===org.id&&r.condition.kind==='number'&&r.condition.value===channel.id);
  const inUse=(usage?.agents.length||0)+(usage?.rules.length||0)+(usage?.campaigns.length||0);
  const remove=async()=>{await run(()=>api.removeChannel(channel.id),'Number removed.');navigate('/app/channels')};

  return <div className="stack">
    <PageHead eyebrow={t(CHANNEL_LABEL[channel.type])} title={channel.label} description={t(CHANNEL_BLURB[channel.type])}
      action={canManage&&<div className="row">
        {!channel.isPrimary&&<Button variant="outline" onClick={()=>run(()=>api.setPrimaryChannel(org.id,channel.id),'Set as the main number.')}>Make main</Button>}
        <Button variant="outline" onClick={()=>setConfirmRemove(true)}><Trash2 size={15}/> Remove</Button>
      </div>}/>

    <div className="card stack">
      <div className="row between">
        <div><div className="stat-label">{t('Your number')}</div><div className="stat-number mono">{channel.displayNumber||'—'}</div></div>
        <Badge tone={tone}>{t(statusLabel)}</Badge>
      </div>
      <div className="setup-steps">{steps.map(([key,stepLabel],i)=>
        <div className={'setup-step '+(i<=currentStep?'done':'')} key={key}>
          <span className="setup-dot">{i<=currentStep?<CheckCircle2 size={14}/>:i+1}</span>{t(stepLabel)}
        </div>)}</div>
      <p className="small muted">{t(blurb)}</p>
      {channel.status==='error'&&<div className="notice warning"><AlertTriangle size={15}/> {t('We have been notified and are working on it. You do not need to do anything.')}</div>}
      {canManage&&<Field label="What this number is for" value={channel.label} onChange={v=>run(()=>api.renameChannel(channel.id,v))}/>}
    </div>

    <div className="grid cols-3">
      <div className="card"><div className="stat-label">{t('Calls on this number')}</div><div className="stat-number mono">{calls.length}</div></div>
      <div className="card"><div className="stat-label">{t('Last 7 days')}</div><div className="stat-number mono">{calls.filter(c=>Date.now()-new Date(c.startedAt).getTime()<7*86400000).length}</div></div>
      <div className="card"><div className="stat-label">{t('Last checked')}</div><div>{formatDate(channel.lastCheckedAt)}</div></div>
    </div>

    <ConnectionSetup orgId={org.id} channelId={channel.id}/>

    <div className="card stack">
      <h2>{t('What happens when this number rings')}</h2>
      {agents.length===0&&rules.length===0
        ?<div className="notice"><AlertTriangle size={15}/> {t('No agent answers this number yet, so calls fall through to your default agent.')} <Link to="/app/agents">{t('Assign an agent')}</Link></div>
        :<div className="stack">
          {agents.map(a=><div className="row between" key={a.id}>
            <span>{t('Answered by')} <strong>{a.name}</strong></span>
            <Link className="small" to={'/app/agents/'+a.id}>{t('Open agent')}</Link>
          </div>)}
          {rules.map(r=><div className="row between" key={r.id}>
            <span>{t('Routing rule')} <strong>{r.name}</strong></span>
            <Link className="small" to="/app/routing">{t('Open routing')}</Link>
          </div>)}
        </div>}
    </div>

    {confirmRemove&&<div className="modal-backdrop" onClick={()=>setConfirmRemove(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('Remove')} {channel.label}?</h2><button className="icon-btn" onClick={()=>setConfirmRemove(false)}><X size={18}/></button></div>
      {inUse>0
        ?<div className="notice warning"><strong>{usage?.agents.length} {t('agents')}, {usage?.rules.length} {t('routing rules')} {t('and')} {usage?.campaigns.length} {t('campaigns')} {t('use this number.')}</strong><p>{t('They will fall back to your main number.')}</p></div>
        :<p>{t('Nothing is using this number.')}</p>}
      <p className="small muted">{t('Calls already made on it stay in your history.')}</p>
      <div className="row"><Button variant="outline" onClick={()=>setConfirmRemove(false)}>Cancel</Button><Button variant="danger" onClick={remove}>Remove it</Button></div>
    </div></div>}
  </div>;
}

// ---------------------------------------------------------------- settings --

export function SettingsPage(){
  const {store,org,run,canManage,t}=useApp();
  const [tab,setTab]=useState('Company');
  const [confirmName,setConfirmName]=useState('');
  const save=(patch:Record<string,unknown>)=>run(()=>api.updateOrganization(org.id,patch),'Saved');
  const members=store.memberships.filter(m=>m.orgId===org.id).length;
  return <div className="stack">
    <PageHead eyebrow="Settings" title="Settings" description="Your company details and how your calls are handled."/>
    <Tabs items={['Company','How calls are handled','Recording','Danger zone']} active={tab} onChange={setTab}/>

    {tab==='Company'&&<div className="card stack">
      <Field label="Company name" value={org.name} onChange={v=>save({name:v})} disabled={!canManage}/>
      <div className="field-grid">
        <div className="field"><label>{t('Language your agent starts in')}</label>
          <select className="select" value={org.defaultLanguage} disabled={!canManage} onChange={e=>save({defaultLanguage:e.target.value})}>
            <option value="ur">{t('Urdu')}</option><option value="en">{t('English')}</option>
            <option value="es">{t('Spanish')}</option><option value="fr">{t('French')}</option>
          </select>
          <div className="help">{t('Your agent still replies in whatever language the caller uses.')}</div></div>
        <Field label="Country code" value={org.countryCode} onChange={v=>save({countryCode:v})} disabled={!canManage}
          help="Used when someone types a local number, so it still reaches the right person."/>
        <Field label="Time zone" value={org.timezone} onChange={v=>save({timezone:v})} disabled={!canManage}/>
      </div>
      <div className="field-grid">
        <Field label="Business hours start" type="time" value={org.businessHoursStart} onChange={v=>save({businessHoursStart:v})} disabled={!canManage}/>
        <Field label="Business hours end" type="time" value={org.businessHoursEnd} onChange={v=>save({businessHoursEnd:v})} disabled={!canManage}/>
      </div>
      <div className="field"><label>{t('Your colour')}</label>
        <input className="input" type="color" disabled={!canManage}
          value={'#5a6bd8'} onChange={e=>save({accentColor:e.target.value})}/>
        <div className="help">{t('Used across your workspace.')}</div></div>
    </div>}

    {tab==='How calls are handled'&&<div className="stack">
      <PresetGroup kind="strictness"/>
      <PresetGroup kind="pace"/>
      <PresetGroup kind="voiceQuality"/>
      <div className="card stack">
        <h2>{t('Call length')}</h2>
        <Field label="End calls after (minutes)" type="number" value={org.endCallsAfterMinutes} onChange={v=>save({endCallsAfterMinutes:Number(v)})} disabled={!canManage}
          help="A safety limit. Your agent warns the caller before it reaches this."/>
      </div>
    </div>}

    {tab==='Recording'&&<div className="card stack">
      <h2>{t('Recording and how long you keep it')}</h2>
      <label className="row"><input type="checkbox" checked={org.recordCalls} disabled={!canManage} onChange={e=>save({recordCalls:e.target.checked})}/> {t('Record calls')}</label>
      <label className="row"><input type="checkbox" checked={org.recordBothSides} disabled={!canManage||!org.recordCalls} onChange={e=>save({recordBothSides:e.target.checked})}/> {t('Record both sides of the conversation')}</label>
      <div className="notice">{t('Where you operate may require you to tell callers they are being recorded. Put that in your greeting if so.')}</div>
      <div className="field"><label>{t('Keep recordings for')}</label>
        <select className="select" value={org.retentionDays} disabled={!canManage} onChange={e=>save({retentionDays:Number(e.target.value)})}>
          {[30,90,180,365].map(d=><option key={d} value={d}>{d} {t('days')}</option>)}
        </select></div>
    </div>}

    {tab==='Danger zone'&&<div className="card stack">
      <h2 className="danger">{t('Close this account')}</h2>
      <p className="small muted">{t('This removes every call, recording, document and team member for')} <strong>{org.name}</strong>. {t('It cannot be undone.')} {members>1&&t('Your team will lose access immediately.')}</p>
      <Field label={'Type '+org.name+' to confirm'} value={confirmName} onChange={setConfirmName} disabled={!canManage}/>
      <div className="row"><Button variant="danger" disabled={!canManage||confirmName!==org.name} onClick={()=>run(()=>api.deleteOrganization(org.id),'Account closed.')}>Close the account permanently</Button></div>
    </div>}
  </div>;
}
