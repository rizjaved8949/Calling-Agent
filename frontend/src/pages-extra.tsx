import {useEffect,useMemo,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {ArrowDown,ArrowUp,BookOpen,BrainCircuit,CheckCircle2,FileText,Headphones,ListChecks,Pause,PhoneOutgoing,Play,Plus,Route as RouteIcon,Search,Trash2,Upload,X} from 'lucide-react';
import {Badge,Button,Empty,Field,PageHead} from './app';
import {useApp} from './app-context';
import {formatDate,formatDuration} from './lib/format';
import {api} from './lib/api';
import type {Call,Campaign,KbDocument,UnansweredQuestion} from './lib/types';

const CHANNEL_LABEL:Record<string,string>={sim:'Phone line',whatsapp_call:'WhatsApp call',whatsapp_message:'WhatsApp message'};
const KB_STATE:Record<string,[string,string]>={ready:['success','Ready'],indexing:['warning','Preparing'],failed:['danger','Needs attention'],empty:['','No documents yet']};



// ---------------------------------------------------------------- knowledge --

export function KnowledgeDetail(){
  const {store,org,run,canManage,t}=useApp();
  const {kbId}=useParams();
  const navigate=useNavigate();
  const kb=store.knowledgeBases.find(x=>x.id===kbId&&x.orgId===org.id);
  const docs=useMemo(()=>store.documents.filter(x=>x.kbId===kbId),[store.documents,kbId]);
  const [usage,setUsage]=useState<{agents:string[];rules:string[];campaigns:string[]}|null>(null);
  const [question,setQuestion]=useState('');
  const [answer,setAnswer]=useState<{answered:boolean;text:string;source?:string;page?:number}|null>(null);
  const [asking,setAsking]=useState(false);
  const [confirmDelete,setConfirmDelete]=useState(false);
  useEffect(()=>{if(kbId)api.knowledgeUsage(kbId).then(setUsage)},[kbId,store.agents,store.callSetups]);
  if(!kb)return <Empty icon={BrainCircuit} title="Knowledge base not found" body="It may have been deleted." action={<Button to="/app/knowledge">Back to knowledge</Button>}/>;
  const [tone,label]=KB_STATE[kb.status];

  const ask=async()=>{
    if(!question.trim())return;
    setAsking(true);
    const hits=await api.searchKnowledge(kb.id,question);
    setAsking(false);
    // The company sees a verdict and a source, never a score.
    const best=hits[0];
    const good=best&&best.score>=kb.similarityThreshold;
    setAnswer(good
      ?{answered:true,text:best.preview,source:best.title,page:best.page}
      :{answered:false,text:'The agent would not answer this from '+kb.name+'. It would tell the caller it will find out and pass the question on.'});
  };
  const upload=(files:FileList|null)=>{
    if(!files?.length)return;
    run(()=>api.uploadDocuments(kb.id,Array.from(files).map(f=>({name:f.name,size:f.size,type:f.type}))),'Documents added. They are being prepared now.');
  };
  const remove=async()=>{
    await run(()=>api.deleteKnowledgeBase(kb.id),'Knowledge base deleted.');
    navigate('/app/knowledge');
  };
  const inUse=(usage?.agents.length||0)+(usage?.rules.length||0)+(usage?.campaigns.length||0);

  return <div className="stack">
    <PageHead eyebrow="Knowledge" title={kb.name} description={kb.purpose}
      action={canManage&&<div className="row">
        <Button variant="outline" onClick={()=>run(()=>api.reindex(kb.id),'Preparing your documents again.')}>Refresh</Button>
        <Button variant="outline" onClick={()=>setConfirmDelete(true)}><Trash2 size={15}/> Delete</Button>
      </div>}/>

    <div className="grid cols-4">
      <div className="card"><div className="stat-label">Status</div><Badge tone={tone}>{label}</Badge></div>
      <div className="card"><div className="stat-label">Documents</div><div className="stat-number mono">{docs.length}</div></div>
      <div className="card"><div className="stat-label">Answered in 30 days</div><div className="stat-number mono">{kb.answeredLast30d}</div></div>
      <div className="card"><div className="stat-label">Last updated</div><div>{formatDate(kb.lastIndexedAt)}</div></div>
    </div>

    {kb.isDefault&&<div className="notice">{t('This is your default knowledge base. Calls use it when nothing else decides.')}</div>}
    {!kb.isDefault&&canManage&&<div className="row between card">
      <span>{t('Calls fall back to your default knowledge base when no rule applies.')}</span>
      <Button small variant="outline" onClick={()=>run(()=>api.setDefaultKnowledgeBase(org.id,kb.id),'Set as your default.')}>Make this the default</Button>
    </div>}

    <div className="card stack">
      <div className="row between"><h2>{t('Ask your documents')}</h2><span className="small muted">{t('See what the agent would say before a caller does')}</span></div>
      <div className="row">
        <input className="input" value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')ask()}} placeholder={t('What are the entry requirements for BS Computer Science?')}/>
        <Button onClick={ask} disabled={asking||!question.trim()}>{asking?'Checking…':'Ask'}</Button>
      </div>
      {answer&&<div className={'result-card '+(answer.answered?'':'discarded')}>
        <div className="row between">
          <strong>{answer.answered?t('The agent would answer'):t('The agent would not answer')}</strong>
          {answer.answered&&answer.source&&<span className="small muted">{t('From')} {answer.source}{answer.page?', page '+answer.page:''}</span>}
        </div>
        <p>{answer.text}</p>
        {!answer.answered&&<p className="small muted">{t('Add this answer to your documents and the agent will handle it next time.')}</p>}
      </div>}
    </div>

    <div className="card stack">
      <div className="row between"><h2>{t('Documents')}</h2>
        {canManage&&<label className="button outline" style={{cursor:'pointer'}}><Upload size={15}/> {t('Add documents')}
          <input type="file" multiple hidden onChange={e=>upload(e.target.files)}/>
        </label>}
      </div>
      {docs.length===0
        ?<Empty icon={FileText} title="No documents yet" body="Upload the material your agent should answer from. PDF, Word, text and Markdown all work."/>
        :<div className="table-wrap"><table className="table">
          <thead><tr><th>{t('File')}</th><th>{t('Pages')}</th><th>{t('Status')}</th><th>{t('Added by')}</th><th/></tr></thead>
          <tbody>{docs.map((d:KbDocument)=><tr key={d.id}>
            <td><strong>{d.filename}</strong>{d.error&&<div className="small danger">{d.error}</div>}</td>
            <td className="mono">{d.pages||'—'}</td>
            <td><Badge tone={d.status==='ready'?'success':d.status==='failed'?'danger':'warning'}>{d.status==='ready'?t('Ready'):d.status==='failed'?t('Could not read'):t('Preparing')}</Badge></td>
            <td className="small muted">{d.uploadedBy}</td>
            <td>{canManage&&<button className="icon-btn" title={t('Remove')} onClick={()=>run(()=>api.deleteDocument(d.id),'Document removed.')}><Trash2 size={15}/></button>}</td>
          </tr>)}</tbody>
        </table></div>}
    </div>

    {confirmDelete&&<div className="modal-backdrop" onClick={()=>setConfirmDelete(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('Delete')} {kb.name}?</h2><button className="icon-btn" onClick={()=>setConfirmDelete(false)}><X size={18}/></button></div>
      {inUse>0
        ?<div className="notice warning"><strong>{usage?.agents.length} {t('agents')}, {usage?.rules.length} {t('call setups')} {t('and')} {usage?.campaigns.length} {t('campaigns')} {t('use this.')}</strong><p>{t('Deleting it will send those calls to your default knowledge base instead.')}</p></div>
        :<p>{t('Nothing is using this knowledge base. Its documents will be removed permanently.')}</p>}
      <div className="row"><Button variant="outline" onClick={()=>setConfirmDelete(false)}>Cancel</Button><Button variant="danger" onClick={remove}>Delete it</Button></div>
    </div></div>}
  </div>;
}

// ---------------------------------------------------------------- campaigns --

const CAMPAIGN_TONE:Record<Campaign['status'],string>={running:'live',paused:'warning',draft:'',done:'success'};

/** A campaign is a list of numbers worked through using one outgoing setup. */
export function Campaigns(){
  const {store,org,run,canManage,t}=useApp();
  const campaigns=store.campaigns.filter(x=>x.orgId===org.id);
  const setups=store.callSetups.filter(x=>x.orgId===org.id&&x.direction==='OUTBOUND');
  const [creating,setCreating]=useState(false);
  const [name,setName]=useState('');
  const [setupId,setSetupId]=useState(setups[0]?.id||'');
  const describe=(id:string)=>{
    const sp=store.callSetups.find(x=>x.id===id); if(!sp)return t('No setup');
    const ch=store.channels.find(c=>c.id===sp.channelId);
    const kb=sp.knowledgeBaseIds.map(k=>store.knowledgeBases.find(b=>b.id===k)?.name).filter(Boolean).join(', ');
    return sp.name+' · '+(ch?.label||'')+(kb?' · '+kb:'');
  };
  const create=async()=>{
    await run(()=>api.createCampaign(org.id,{name,setupId}),'Campaign created as a draft.');
    setCreating(false);setName('');
  };
  return <div className="stack">
    <PageHead eyebrow="Outbound" title="Campaigns" description="Work through a list of numbers using one of your outgoing call setups."
      action={canManage&&setups.length>0&&<Button onClick={()=>setCreating(true)}><Plus size={15}/> New campaign</Button>}/>
    {setups.length===0
      ?<Empty icon={PhoneOutgoing} title="No outgoing setups yet" body="A campaign runs on an outgoing setup, which decides the agent, the knowledge and the tone." action={<Button to="/app/setups">Create one</Button>}/>
      :campaigns.length===0
      ?<Empty icon={PhoneOutgoing} title="No campaigns yet" body="Pick an outgoing setup, upload your list, and the agent works through it." action={canManage&&<Button onClick={()=>setCreating(true)}>Create one</Button>}/>
      :<div className="grid cols-2">{campaigns.map(c=>{
        const progress=c.total?Math.round(c.attempted/c.total*100):0;
        return <Link className="card lift" to={'/app/campaigns/'+c.id} key={c.id}>
          <div className="row between"><strong>{c.name}</strong><Badge tone={CAMPAIGN_TONE[c.status]}>{t(c.status)}</Badge></div>
          <div className="small muted">{describe(c.setupId)}</div>
          <div className="meter"><span style={{width:progress+'%'}}/></div>
          <div className="row between small"><span className="mono">{c.attempted} / {c.total} {t('called')}</span><span className="mono success">{c.connected} {t('connected')}</span></div>
        </Link>;
      })}</div>}
    {creating&&<div className="modal-backdrop" onClick={()=>setCreating(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('New campaign')}</h2><button className="icon-btn" onClick={()=>setCreating(false)}><X size={18}/></button></div>
      <Field label="Campaign name" value={name} onChange={setName} placeholder="Merit list follow-up"/>
      <div className="field"><label>{t('Which outgoing setup')}</label>
        <select className="select" value={setupId} onChange={e=>setSetupId(e.target.value)}>
          {setups.map(sp=><option key={sp.id} value={sp.id}>{describe(sp.id)}</option>)}
        </select>
        <div className="help">{t('The setup decides the agent, the knowledge and the tone, so there is nothing else to choose here.')}</div></div>
      <div className="row"><Button variant="outline" onClick={()=>setCreating(false)}>Cancel</Button><Button onClick={create} disabled={!name.trim()||!setupId}>Create draft</Button></div>
    </div></div>}
  </div>;
}

export function CampaignDetail(){
  const {store,org,run,canManage,t}=useApp();
  const {id}=useParams();
  const navigate=useNavigate();
  const [confirmDelete,setConfirmDelete]=useState(false);
  const campaign=store.campaigns.find(x=>x.id===id&&x.orgId===org.id);
  const contacts=store.campaignContacts.filter(x=>x.campaignId===id);
  if(!campaign)return <Empty icon={PhoneOutgoing} title="Campaign not found" body="It may have been deleted." action={<Button to="/app/campaigns">Back to campaigns</Button>}/>;
  const setup=store.callSetups.find(x=>x.id===campaign.setupId);
  const agent=store.agents.find(a=>a.id===setup?.agentId);
  const channel=store.channels.find(c=>c.id===setup?.channelId);
  const kbNames=(setup?.knowledgeBaseIds||[]).map(k=>store.knowledgeBases.find(b=>b.id===k)?.name).filter(Boolean);
  const progress=campaign.total?Math.round(campaign.attempted/campaign.total*100):0;
  return <div className="stack">
    <PageHead eyebrow="Campaign" title={campaign.name}
      description={setup?t('Using the setup')+' "'+setup.name+'"':t('This campaign has no setup any more.')}
      action={canManage&&<div className="row">
        {campaign.status!=='running'&&<Button onClick={()=>run(()=>api.setCampaignStatus(campaign.id,'running'),'Campaign started.')}><Play size={15}/> Start</Button>}
        {campaign.status==='running'&&<Button variant="outline" onClick={()=>run(()=>api.setCampaignStatus(campaign.id,'paused'),'Campaign paused.')}><Pause size={15}/> Pause</Button>}
        <Button variant="outline" onClick={()=>setConfirmDelete(true)}><Trash2 size={15}/> Delete</Button>
      </div>}/>
    {setup&&<div className="card stack">
      <div className="row between"><span>{t('Calling from')}</span><strong>{channel?.label} · <span className="mono">{channel?.displayNumber}</span></strong></div>
      <div className="row between"><span>{t('Answered by')}</span><strong>{agent?.name||'—'}</strong></div>
      <div className="row between"><span>{t('Knowledge used')}</span><strong>{kbNames.join(t(' first, then '))||t('your default')}</strong></div>
      <div className="row"><Button small variant="outline" to="/app/setups">Change the setup</Button></div>
    </div>}
    <div className="grid cols-4">
      <div className="card"><div className="stat-label">{t('Called')}</div><div className="stat-number mono">{campaign.attempted}</div><div className="small muted">{t('of')} {campaign.total}</div></div>
      <div className="card"><div className="stat-label">{t('Connected')}</div><div className="stat-number mono success">{campaign.connected}</div></div>
      <div className="card"><div className="stat-label">{t('No answer')}</div><div className="stat-number mono">{campaign.unanswered}</div></div>
      <div className="card"><div className="stat-label">{t('Calling hours')}</div><div className="mono">{campaign.windowStart}–{campaign.windowEnd}</div><div className="small muted">{campaign.maxAttempts} {t('attempts')}</div></div>
    </div>
    <div className="card"><div className="meter"><span style={{width:progress+'%'}}/></div><div className="small muted">{progress}% {t('of the list attempted')}</div></div>
    <div className="card stack">
      <h2>{t('People on this list')}</h2>
      {contacts.length===0
        ?<Empty icon={PhoneOutgoing} title="No one on the list yet" body="Upload a list of numbers and the campaign will work through it."/>
        :<div className="table-wrap"><table className="table">
          <thead><tr><th>{t('Name')}</th><th>{t('Number')}</th><th>{t('Attempts')}</th><th>{t('Last result')}</th></tr></thead>
          <tbody>{contacts.map(c=><tr key={c.id}><td>{c.name||'—'}</td><td className="mono">{c.number}</td><td className="mono">{c.attempts}</td><td>{c.lastOutcome||'—'}</td></tr>)}</tbody>
        </table></div>}
    </div>
    {confirmDelete&&<div className="modal-backdrop" onClick={()=>setConfirmDelete(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('Delete')} {campaign.name}?</h2><button className="icon-btn" onClick={()=>setConfirmDelete(false)}><X size={18}/></button></div>
      <p>{t('This removes the campaign and its contact list. The setup it used stays, and calls already made stay in your history.')}</p>
      <div className="row"><Button variant="outline" onClick={()=>setConfirmDelete(false)}>Cancel</Button>
        <Button variant="danger" onClick={async()=>{await run(()=>api.deleteCampaign(campaign.id),'Campaign deleted.');navigate('/app/campaigns')}}>Delete it</Button></div>
    </div></div>}
  </div>;
}

// --------------------------------------------------------------- unanswered --

export function Unanswered(){
  const {store,org,run,t}=useApp();
  const all=store.unanswered.filter(x=>x.orgId===org.id);
  const [filter,setFilter]=useState<'open'|'answered'|'ignored'>('open');
  const rows=all.filter(x=>x.status===filter).sort((a,b)=>b.askedCount-a.askedCount);
  const bases=store.knowledgeBases.filter(x=>x.orgId===org.id);
  return <div className="stack">
    <PageHead eyebrow="Improve" title="Questions your agent could not answer"
      description="Every one of these is a caller who did not get what they came for. Add the answer and the agent handles it from then on."/>
    <div className="tabs" role="tablist">
      {(['open','answered','ignored'] as const).map(k=>
        <button role="tab" key={k} aria-selected={filter===k} className={'tab '+(filter===k?'active':'')} onClick={()=>setFilter(k)}>
          {t(k==='open'?'Needs an answer':k==='answered'?'Answered':'Ignored')} ({all.filter(x=>x.status===k).length})
        </button>)}
    </div>
    {rows.length===0
      ?<Empty icon={ListChecks} title="Nothing here" body={filter==='open'?'Your agent answered everything it was asked. That will change as callers find new gaps.':'Nothing in this list yet.'}/>
      :<div className="stack">{rows.map((q:UnansweredQuestion)=>{
        const kb=bases.find(b=>b.id===q.knowledgeBaseId);
        return <div className="card unanswered-row" key={q.id}>
          <div className="unanswered-count mono"><strong>{q.askedCount}</strong><span>{t('times')}</span></div>
          <div className="unanswered-body">
            <strong>{q.question}</strong>
            <div className="small muted">{t('Asked of')} {kb?.name||t('your documents')} · {t('last')} {formatDate(q.lastAskedAt)}</div>
            <div className="small">{q.exampleCallIds.map((cid,i)=><Link className="link-inline" to={'/app/history/'+cid} key={cid}>{t(i===0?'Listen to a call':'Another example')}</Link>)}</div>
          </div>
          <div className="unanswered-actions">
            {q.status==='open'&&<>
              <Button small to={'/app/knowledge/'+q.knowledgeBaseId}>Add an answer</Button>
              <Button small variant="outline" onClick={()=>run(()=>api.resolveUnanswered(q.id,'answered'),'Marked as answered.')}>Mark answered</Button>
              <Button small variant="outline" onClick={()=>run(()=>api.resolveUnanswered(q.id,'ignored'),'Ignored.')}>Ignore</Button>
            </>}
            {q.status!=='open'&&<Button small variant="outline" onClick={()=>run(()=>api.resolveUnanswered(q.id,'open'),'Back on the list.')}>Reopen</Button>}
          </div>
        </div>;
      })}</div>}
  </div>;
}

// -------------------------------------------------------------------- staff --

export function StaffQueue(){
  const {store,org,session,run,t}=useApp();
  const mine=store.calls.filter(c=>c.orgId===org.id&&c.assignedToUserId===session?.userId);
  const active=mine.filter(c=>c.status==='active');
  const recent=mine.filter(c=>c.status!=='active').slice(0,12);
  const campaigns=store.campaigns.filter(c=>c.orgId===org.id&&c.status==='running');
  return <div className="stack">
    <PageHead eyebrow="My work" title="My queue" description="Calls waiting for you, and the list you are working through."/>
    <div className="card stack">
      <h2>{t('Live now')}</h2>
      {active.length===0
        ?<Empty icon={Headphones} title="Nothing live right now" body="When a call is sent to you it appears here straight away."/>
        :active.map(c=><div className="row between card live-row" key={c.id}>
          <div><strong className="mono">{c.phoneNumber}</strong><div className="small muted">{t(CHANNEL_LABEL[c.channelType])} · {t('using')} {c.knowledgeBaseName}</div></div>
          <div className="row"><Button small to={'/app/live'}>Open</Button><Button small variant="outline" onClick={()=>run(()=>api.takeOverCall(c.id,session?.userId),'You are on the call.')}>Take over</Button></div>
        </div>)}
    </div>
    {campaigns.length>0&&<div className="card stack">
      <h2>{t('Lists being worked')}</h2>
      {campaigns.map(c=><div className="row between" key={c.id}><span>{c.name}</span><span className="small mono muted">{c.attempted}/{c.total}</span></div>)}
    </div>}
    <OperatorDial/>
    <div className="card stack">
      <h2>{t('My recent calls')}</h2>
      {recent.length===0
        ?<Empty icon={Headphones} title="No calls yet" body="Calls you handle will be listed here."/>
        :<div className="table-wrap"><table className="table">
          <thead><tr><th>{t('When')}</th><th>{t('Number')}</th><th>{t('Length')}</th><th>{t('Result')}</th></tr></thead>
          <tbody>{recent.map(c=><tr key={c.id}><td>{formatDate(c.startedAt)}</td><td className="mono"><Link to={'/app/history/'+c.id}>{c.phoneNumber}</Link></td><td className="mono">{formatDuration(c.durationSeconds)}</td><td>{c.outcome}</td></tr>)}</tbody>
        </table></div>}
    </div>
  </div>;
}

/** A person places the call themselves, from one of the company's numbers. */
export function OperatorDial(){
  const {store,org,session,run,t}=useApp();
  const navigate=useNavigate();
  const numbers=store.channels.filter(c=>c.orgId===org.id&&c.status==='connected'&&!!c.config.operatorEnabled);
  const [fromId,setFromId]=useState(numbers.find(n=>n.isPrimary)?.id||numbers[0]?.id||'');
  const [to,setTo]=useState('');
  if(numbers.length===0)return <div className="card stack">
    <h2>{t('Call someone yourself')}</h2>
    <Empty icon={PhoneOutgoing} title="No number is set up for this yet"
      body="Ask an admin to turn on 'Let your team answer instead' for one of your numbers."/>
  </div>;
  const dial=async()=>{
    const row=await run(()=>api.createOperatorCall(org.id,fromId,to.trim(),session?.userId||''),'Calling now. You are on the line.');
    if(row)navigate('/app/live');
  };
  return <div className="card stack">
    <div><h2>{t('Call someone yourself')}</h2>
      <p className="small muted">{t('You speak, not the agent. The call still shows in history and is recorded like any other.')}</p></div>
    <div className="field-grid">
      <div className="field"><label>{t('Call from')}</label>
        <select className="select" value={fromId} onChange={e=>setFromId(e.target.value)}>
          {numbers.map(c=><option key={c.id} value={c.id}>{c.label} — {c.displayNumber}</option>)}
        </select>
        <div className="help">{t('This is the number they will see calling.')}</div></div>
      <Field label="Number to call" value={to} onChange={setTo} placeholder="+92 300 123 4567"/>
    </div>
    <div className="row"><Button disabled={!to.trim()||!fromId} onClick={dial}><PhoneOutgoing size={15}/> Call now</Button></div>
  </div>;
}

export function StaffLookup(){
  const {store,org,t}=useApp();
  const [query,setQuery]=useState('');
  const matches=useMemo(()=>{
    if(query.trim().length<3)return [] as Call[];
    const needle=query.replace(/\s/g,'');
    return store.calls.filter(c=>c.orgId===org.id&&c.phoneNumber.replace(/\s/g,'').includes(needle)).slice(0,30);
  },[query,store.calls,org.id]);
  return <div className="stack">
    <PageHead eyebrow="Help me" title="Customer lookup" description="Search a number to see everything that has happened with that person."/>
    <div className="card">
      <div className="row"><input className="input" value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('Type at least three digits of a number')}/></div>
    </div>
    {query.trim().length>=3&&(matches.length===0
      ?<Empty icon={Search} title="No calls with that number" body="Check the digits, or try fewer of them."/>
      :<div className="card stack">
        <div className="small muted">{matches.length} {t('calls with')} <span className="mono">{matches[0].phoneNumber}</span></div>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>{t('When')}</th><th>{t('Direction')}</th><th>{t('Length')}</th><th>{t('Knowledge used')}</th><th>{t('Result')}</th></tr></thead>
          <tbody>{matches.map(c=><tr key={c.id}>
            <td><Link to={'/app/history/'+c.id}>{formatDate(c.startedAt)}</Link></td>
            <td>{c.direction==='INBOUND'?t('They called'):t('We called')}</td>
            <td className="mono">{formatDuration(c.durationSeconds)}</td>
            <td className="small">{c.knowledgeBaseName}</td>
            <td>{c.outcome}</td>
          </tr>)}</tbody>
        </table></div>
      </div>)}
  </div>;
}

export function StaffAsk(){
  const {store,org,t}=useApp();
  const bases=store.knowledgeBases.filter(x=>x.orgId===org.id&&x.status==='ready');
  const [kbId,setKbId]=useState(bases[0]?.id||'');
  const [question,setQuestion]=useState('');
  const [busy,setBusy]=useState(false);
  const [answer,setAnswer]=useState<{answered:boolean;text:string;source?:string;page?:number}|null>(null);
  const ask=async()=>{
    if(!question.trim()||!kbId)return;
    setBusy(true);
    const kb=store.knowledgeBases.find(x=>x.id===kbId)!;
    const hits=await api.searchKnowledge(kbId,question);
    setBusy(false);
    const best=hits[0];
    setAnswer(best&&best.score>=kb.similarityThreshold
      ?{answered:true,text:best.preview,source:best.title,page:best.page}
      :{answered:false,text:'There is no answer to this in '+kb.name+'. Tell the caller you will find out, and it will be added.'});
  };
  if(bases.length===0)return <Empty icon={BookOpen} title="No documents are ready yet" body="Once your team uploads material you can look things up here while you are on a call."/>;
  return <div className="stack">
    <PageHead eyebrow="Help me" title="Ask the documents" description="Check an answer mid-call without putting the caller on hold."/>
    <div className="card stack">
      <div className="field"><label>{t('Look in')}</label><select className="select" value={kbId} onChange={e=>setKbId(e.target.value)}>{bases.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></div>
      <div className="row"><input className="input" autoFocus value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')ask()}} placeholder={t('What is the fee for BS Computer Science?')}/><Button onClick={ask} disabled={busy||!question.trim()}>{busy?'Checking…':'Ask'}</Button></div>
      {answer&&<div className={'result-card big '+(answer.answered?'':'discarded')}>
        <p>{answer.text}</p>
        {answer.answered&&answer.source&&<div className="small muted">{t('From')} {answer.source}{answer.page?', page '+answer.page:''}</div>}
      </div>}
    </div>
  </div>;
}
