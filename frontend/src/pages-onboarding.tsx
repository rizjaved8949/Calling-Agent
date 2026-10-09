import {useEffect,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {AlertTriangle,CheckCircle2,Clock,Download,MessageSquare,Sparkles} from 'lucide-react';
import {Badge,Button,Empty,Field,PageHead,Tabs} from './app';
import {useApp} from './app-context';
import {formatDate,formatDuration} from './lib/format';
import {api} from './lib/api';
import type {Call,ChannelType} from './lib/types';

/**
 * Setup and call detail. Both are written around managed provisioning: the
 * company answers business questions, and we do the technical work behind it.
 */

const CHANNEL_LABEL:Record<ChannelType,string>={sim:'Phone line',whatsapp_call:'WhatsApp calls',whatsapp_message:'WhatsApp messages'};
const CHANNEL_BLURB:Record<ChannelType,string>={
  sim:'A normal phone number people can dial from any phone.',
  whatsapp_call:'Voice calls placed and received inside WhatsApp.',
  whatsapp_message:'Written messages, for sending details in writing.',
};
const SETUP_STATE:Record<string,[string,string,string]>={
  requested:['','Requested','We have your request and will start shortly.'],
  setting_up:['warning','Being set up','We are doing the work. Nothing is needed from you.'],
  testing:['warning','Testing','We are placing test calls to make sure it all works.'],
  active:['success','Active','This number is live and answering.'],
  blocked:['danger','Needs our attention','We have hit something and are sorting it out.'],
};

export function Onboarding(){
  const {store,org,run,t,readiness}=useApp();
  const navigate=useNavigate();
  const [step,setStep]=useState(Math.min(org.onboardingStep,4));
  const [agentName,setAgentName]=useState('');
  const [greeting,setGreeting]=useState('Assalam o alaikum, main aap ki kya madad kar sakti hoon?');
  const [kbName,setKbName]=useState('');
  const [wanted,setWanted]=useState<ChannelType[]>(['sim']);
  const tasks=store.provisioning.filter(x=>x.orgId===org.id);
  const steps=['About you','Your agent','What it knows','Where it answers','Go live'];
  const toggleWanted=(type:ChannelType)=>setWanted(w=>w.includes(type)?w.filter(x=>x!==type):[...w,type]);
  const finish=async()=>{await run(()=>api.updateOrganization(org.id,{onboardingStep:6}),'All set. Welcome aboard.');navigate('/app/dashboard')};

  return <div className="stack">
    <PageHead eyebrow="Setup" title={'Get ' + org.name + ' answering calls'} description="Five short steps. We handle the technical side for you."/>
    <div className="setup-steps">{steps.map((label,i)=>
      <div className={'setup-step '+(i<=step?'done':'')} key={label}>
        <span className="setup-dot">{i<step?<CheckCircle2 size={14}/>:i+1}</span>{t(label)}
      </div>)}</div>

    {step===0&&<div className="card stack">
      <h2>{t('About your company')}</h2>
      <Field label="Company name" value={org.name} onChange={v=>run(()=>api.updateOrganization(org.id,{name:v}))}/>
      <div className="field-grid">
        <Field label="Country code" value={org.countryCode} onChange={v=>run(()=>api.updateOrganization(org.id,{countryCode:v}))}/>
        <div className="field"><label>{t('Language your agent starts in')}</label>
          <select className="select" value={org.defaultLanguage} onChange={e=>run(()=>api.updateOrganization(org.id,{defaultLanguage:e.target.value as 'en'|'ur'}))}>
            <option value="ur">{t('Urdu')}</option><option value="en">{t('English')}</option>
          </select></div>
      </div>
    </div>}

    {step===1&&<div className="card stack">
      <h2>{t('Create your agent')}</h2>
      <Field label="What should callers call it?" value={agentName} onChange={setAgentName} placeholder="Aisha"/>
      <Field label="Greeting, said word for word on every call" value={greeting} onChange={setGreeting} rows={2}
        help="Keep it short. It is the first thing every caller hears."/>
      <div className="row"><Button disabled={!agentName.trim()} onClick={()=>run(()=>api.createAgent(org.id,agentName),'Agent created.')}>Create the agent</Button></div>
    </div>}

    {step===2&&<div className="card stack">
      <h2>{t('What should it be able to answer?')}</h2>
      <p className="small muted">{t('Give it your handbooks, price lists and policies — whatever your callers ask about. You can add more at any time.')}</p>
      <Field label="Name this set of material" value={kbName} onChange={setKbName} placeholder="Admissions 2026"/>
      <div className="row">
        <Button disabled={!kbName.trim()} onClick={()=>run(()=>api.createKnowledgeBase(org.id,kbName,'Added during setup'),'Created. Upload your documents next.')}>Create it</Button>
        <Button variant="outline" to="/app/knowledge">Upload documents</Button>
      </div>
      {readiness.knowledgeBaseReady&&<div className="notice"><CheckCircle2 size={15}/> {t('Your material is ready. Your agent can answer from it.')}</div>}
    </div>}

    {step===3&&<div className="card stack">
      <h2>{t('How should people reach you?')}</h2>
      <p className="small muted">{t('Choose what you need. We set the numbers up for you, so there is nothing for you to configure.')}</p>
      <div className="grid cols-3">{(['sim','whatsapp_call','whatsapp_message'] as ChannelType[]).map(type=>
        <button type="button" key={type} className={'preset-card '+(wanted.includes(type)?'selected':'')} onClick={()=>toggleWanted(type)}>
          <strong>{t(CHANNEL_LABEL[type])}</strong>
          <span>{t(CHANNEL_BLURB[type])}</span>
          <em>{t('Usually live within two working days.')}</em>
        </button>)}</div>
    </div>}

    {step===4&&<div className="stack">
      <div className="card stack">
        <h2>{t('Where your setup is')}</h2>
        {tasks.length===0
          ?<div className="notice">{t('We have your request and will begin shortly. You will get an email when your number is live.')}</div>
          :tasks.map(task=>{
            const [tone,label,blurb]=SETUP_STATE[task.state];
            return <div className="row between checklist-row" key={task.id}>
              <span><strong>{t(CHANNEL_LABEL[task.kind])}</strong><div className="small muted">{t(blurb)}</div></span>
              <Badge tone={tone}>{t(label)}</Badge>
            </div>;
          })}
      </div>
      <div className="card stack">
        <h2>{t('Ready to go?')}</h2>
        {([['Your agent has a greeting',readiness.personaReady],
          ['Your material is ready to answer from',readiness.knowledgeBaseReady],
          ['At least one number is live',readiness.phoneChannelReady||readiness.whatsappCallingReady]] as [string,boolean][]).map(([label,done])=>
          <div className="row between" key={label}>
            <span className="row">{done?<CheckCircle2 size={17} className="success"/>:<Clock size={17} className="muted"/>}{t(label)}</span>
            {!done&&<span className="small muted">{t('Not yet')}</span>}
          </div>)}
        <div className="row"><Button onClick={finish}><Sparkles size={15}/> Finish setup</Button></div>
      </div>
    </div>}

    <div className="row between">
      <Button variant="outline" disabled={step===0} onClick={()=>setStep(s=>s-1)}>Back</Button>
      <Button disabled={step===4} onClick={()=>{setStep(s=>s+1);run(()=>api.updateOrganization(org.id,{onboardingStep:Math.max(org.onboardingStep,step+1)}))}}>Continue</Button>
    </div>
  </div>;
}

/** Honest about which material answered, and where the agent came up short. */
/** What a recording actually captured, in the company's words rather than ours. */
const SCOPE:Record<string,string>={two_way:'Both sides of the conversation',caller_only:'Only the caller',agent_only:'Only the agent',none:'Nothing was recorded'};

/**
 * The audio player.
 *
 * The URL is fetched rather than read off the call, because a media element
 * sends no Authorization header: the server mints a short-lived link that
 * carries its own proof. Requested when the tab is opened rather than with the
 * call list, so browsing history does not mint a playable link for every
 * recording the company has ever made.
 */
function RecordingPlayer({call}:{call:Call}){
  const {t}=useApp();
  const [url,setUrl]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{
    let cancelled=false;
    setUrl('');setError('');
    api.playbackUrl(call.id)
      .then(href=>{if(!cancelled)setUrl(href||'')})
      .catch(cause=>{if(!cancelled)setError(cause instanceof Error?cause.message:'The recording could not be loaded.')});
    return()=>{cancelled=true};
  },[call.id]);
  if(error)return <div className="notice danger">{error}</div>;
  if(!url)return <div className="small muted">{t('Loading the recording…')}</div>;
  return <>
    <audio controls src={url} style={{width:'100%'}}/>
    <div className="row between">
      <div className="small muted">{t('This recording contains')}: {t(SCOPE[call.recordingScope])}</div>
      <Button small variant="outline" onClick={()=>api.downloadRecording(call.id)}>
        <Download size={14}/> {t('Download')}
      </Button>
    </div>
  </>;
}

export function CallDetail(){
  const {store,org,t}=useApp();
  const {id}=useParams();
  const call=store.calls.find(x=>x.id===id&&x.orgId===org.id);
  const [tab,setTab]=useState('Transcript');
  const [query,setQuery]=useState('');
  if(!call)return <Empty title="Call not found" body="Choose another call from your history." action={<Button to="/app/history">Back to history</Button>}/>;
  const transcript=store.transcripts.filter(x=>x.callId===id&&x.text.toLowerCase().includes(query.toLowerCase()));
  const lookups=store.ragQueries.filter(x=>x.callId===id);
  const declined=lookups.filter(x=>!x.answered);
  const resolution={setup:'the call setup for this number',explicit:'chosen for this call',agent:'the agent default',company:'your company default'}[call.resolvedBy];
  const RECORDING:Record<string,[string,string]>={
    RECORDING:['warning','This call is still going, so there is nothing to play yet.'],
    PENDING:['warning','The recording is still being prepared. It is usually ready within a few minutes.'],
    READY:['success','Ready to play.'],
    NONE:['','This call was never recorded.'],
  };
  const [recTone,recBlurb]=RECORDING[call.recordingState];

  return <div className="stack">
    <PageHead eyebrow="Call" title={call.phoneNumber}
      description={formatDate(call.startedAt) + ' · ' + t(CHANNEL_LABEL[call.channelType]) + ' · ' + t(call.mode==='operator'?'Handled by a person':'Handled by your agent')}
      action={<Badge tone={call.status==='failed'?'danger':call.status==='active'?'live':'success'}>{t(call.status)}</Badge>}/>

    <div className="grid cols-4">
      <div className="card"><div className="stat-label">{t('Direction')}</div><div>{call.direction==='INBOUND'?t('They called us'):t('We called them')}</div></div>
      <div className="card"><div className="stat-label">{t('Length')}</div><div className="stat-number mono">{formatDuration(call.durationSeconds)}</div></div>
      <div className="card"><div className="stat-label">{t('Result')}</div><div>{call.outcome}</div></div>
      <div className="card"><div className="stat-label">{t('Recording')}</div><Badge tone={recTone}>{call.recordingState==='READY'?t('Ready'):call.recordingState==='NONE'?t('None'):t('Not yet')}</Badge></div>
    </div>

    <div className="card stack">
      <h2>{t('Which material answered this call')}</h2>
      <div className="row between">
        <span><strong>{call.knowledgeBaseName}</strong> <span className="small muted">— {t(resolution||'your company default')}</span></span>
        <Button small variant="outline" to={'/app/knowledge/'+call.knowledgeBaseId}>Open it</Button>
      </div>
      {lookups.length>0&&<div className="small muted">{t('Your agent looked something up')} {lookups.length} {t('times on this call')}{declined.length>0?', '+t('and could not answer')+' '+declined.length+'.':''}</div>}
      {declined.length>0&&<div className="notice warning"><AlertTriangle size={15}/>
        <div><strong>{t('It could not answer these')}:</strong>
          <ul>{declined.map(d=><li key={d.id}>{d.query}</li>)}</ul>
          <Link to="/app/unanswered">{t('Add the answers')}</Link></div>
      </div>}
    </div>

    {call.summary&&<div className="card"><h2>{t('What happened')}</h2><p>{call.summary}</p></div>}

    <div className="card stack">
      <Tabs items={['Transcript','Recording','What it looked up']} active={tab} onChange={setTab}/>
      {tab==='Transcript'&&<div className="stack">
        <input className="input" value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('Search what was said')}/>
        {transcript.length===0
          ?<Empty icon={MessageSquare} title="No transcript" body="Nothing was transcribed for this call."/>
          :transcript.map(line=><div className="row transcript-line" key={line.id}>
            <span className="small muted mono">{new Date(line.timestamp).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</span>
            <span><strong>{line.speaker}</strong> {line.text}</span>
          </div>)}
      </div>}
      {tab==='Recording'&&<div className="stack">
        <div className="notice">{t(recBlurb)}</div>
        {call.recordingState==='READY'&&<RecordingPlayer call={call}/>}
        {call.recordingState==='PENDING'&&<div className="row"><Button variant="outline" onClick={()=>location.reload()}>Check again</Button></div>}
      </div>}
      {tab==='What it looked up'&&<div className="stack">
        {lookups.length===0
          ?<Empty title="Nothing looked up" body="Your agent answered this call without needing to check your documents."/>
          :lookups.map(q=><div className={'result-card '+(q.answered?'':'discarded')} key={q.id}>
            <div className="row between"><strong>{q.query}</strong><Badge tone={q.answered?'success':'warning'}>{t(q.answered?'Answered':'Could not answer')}</Badge></div>
            {q.answered&&q.documentName&&<div className="small muted">{t('From')} {q.documentName}{q.page?', '+t('page')+' '+q.page:''}</div>}
            {!q.answered&&<div className="small muted">{t('Nothing in your documents covered this. The agent offered to find out.')}</div>}
          </div>)}
      </div>}
    </div>
  </div>;
}
