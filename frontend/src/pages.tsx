import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {Activity,ArrowDownLeft,ArrowRight,ArrowUpRight,AudioLines,BookOpen,BrainCircuit,Check,CheckCircle2,ChevronRight,CircleHelp,Clock,Copy,Download,FileText,Headphones,MessageSquare,Mic2,MoreHorizontal,Pause,Phone,Play,Plus,Radio,Search,Send,ShieldCheck,SlidersHorizontal,Sparkles,Trash2,Upload,Users,Volume2,WifiOff,X} from 'lucide-react';
import {Area,AreaChart,Bar,BarChart,CartesianGrid,Legend,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {api,LIVE,setAuthToken} from './lib/api';
import {friendlyAuthError,loginWithEmail,loginWithGoogle,completeGoogleSignup,registerWithEmail,sendPasswordReset,NeedsSignup} from './lib/api/auth';
import {exportCallsXlsx} from './lib/exportXlsx';
import type {Call,ChannelType,Message,Persona,Role} from './lib/types';
import {REAL_GUIDES} from './lib/guides-content';
import {Badge,Button,Empty,Field,PageHead,Tabs} from './app';
import {useApp} from './app-context';
import {formatDate,formatDuration} from './lib/format';
import {HistoryControls} from './history-navigation';

const channelNames:Record<ChannelType,string>={sim:'Phone number',whatsapp_call:'WhatsApp calling',whatsapp_message:'WhatsApp messaging'};
const channelPaths:Record<ChannelType,string>={sim:'phone',whatsapp_call:'whatsapp-calling',whatsapp_message:'whatsapp-messaging'};
const pathTypes:Record<string,ChannelType>={'phone':'sim','whatsapp-calling':'whatsapp_call','whatsapp-messaging':'whatsapp_message'};
const statusTone=(s:string)=>s==='connected'||s==='ready'||s==='live'||s==='approved'||s==='Resolved'?'success':s==='error'||s==='failed'||s==='rejected'?'danger':s==='pending'||s==='indexing'||s==='paused'?'warning':'';
const money=(n:number)=>'$'+n.toLocaleString();
const selectClass='select';
function Stat({label,value,hint}:{label:string;value:string|number;hint?:string}){const {t}=useApp();return <div className="card"><div className="stat-label">{t(label)}</div><div className="stat-number">{value}</div>{hint&&<div className="stat-trend">{t(hint)}</div>}</div>}
function Confirm({title,body,confirm,onClose,danger=true}:{title:string;body:string;confirm:()=>void;onClose:()=>void;danger?:boolean}){const {t}=useApp();return <div className="modal-backdrop" onClick={onClose}><div className="modal" onClick={e=>e.stopPropagation()}><h2>{t(title)}</h2><p>{t(body)}</p><div className="row" style={{justifyContent:'flex-end'}}><Button variant="secondary" onClick={onClose}>{t('Cancel')}</Button><Button variant={danger?'danger':''} onClick={()=>{confirm();onClose()}}>{t('Confirm')}</Button></div></div></div>}
function MiniEmpty({text}:{text:string}){return <p className="muted">{text}</p>}
function FieldSelect({label,value,onChange,options,help,labels}:{label:string;value:string;onChange:(v:string)=>void;options:string[];help?:string;labels?:Record<string,string>}){const {t}=useApp();return <div className="field"><label>{t(label)}</label><select className={selectClass} value={value} onChange={e=>onChange(e.target.value)}>{options.map(x=><option key={x} value={x}>{t(labels?.[x]??x)}</option>)}</select>{help&&<div className="help">{t(help)}</div>}</div>}
function CheckRow({label,checked,onChange,help}:{label:string;checked:boolean;onChange:(v:boolean)=>void;help?:string}){const {t}=useApp();return <label className="row between" style={{padding:'9px 0'}}><span><strong>{t(label)}</strong>{help&&<div className="help">{t(help)}</div>}</span><input type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)} aria-label={t(label)}/></label>}
function ToolDisabled({reason,guide}:{reason:string;guide:string}){const {t}=useApp();return <span className="help" title={reason}>{t(reason)} <Link to={'/app/guides/'+guide} style={{textDecoration:'underline'}}>{t('Read guide')}</Link></span>}
function CallTable({calls,limit}:{calls:Call[];limit?:number}){
  const {store,t}=useApp();const shown=calls.slice(0,limit);
  return <><div className="table-wrap desktop-calls"><table className="table"><thead><tr>{['Time','Direction','Number','Channel','Handled by','Duration','Outcome','Recording'].map(x=><th key={x}>{t(x)}</th>)}</tr></thead><tbody>{shown.map(c=><tr key={c.id}><td><Link to={'/app/history/'+c.id}>{formatDate(c.startedAt)}</Link></td><td>{c.direction==='INBOUND'?<ArrowDownLeft size={16}/>:<ArrowUpRight size={16}/>}</td><td className="phone"><Link to={'/app/history/'+c.id}>{c.phoneNumber}</Link></td><td>{t(channelNames[c.channelType])}</td><td>{c.mode==='operator'?t('A person'):store.agents.find(x=>x.id===c.agentId)?.name}</td><td className="mono">{formatDuration(c.durationSeconds)}</td><td><Badge tone={statusTone(c.outcome)}>{t(c.outcome)}</Badge></td><td><Badge tone={c.recordingState==='READY'?'success':c.recordingState==='NONE'?'':'warning'}>{c.recordingState}</Badge></td></tr>)}</tbody></table></div>
  <div className="mobile-calls">{shown.map(c=><Link className="result-card" to={'/app/history/'+c.id} key={c.id}><div className="row between"><strong className="phone">{c.phoneNumber}</strong><Badge tone={statusTone(c.outcome)}>{t(c.outcome)}</Badge></div><div className="small muted">{formatDate(c.startedAt)} · {t(channelNames[c.channelType])}</div><div className="row between small" style={{marginTop:9}}><span>{t(c.mode==='operator'?'A person':'The agent')} · {formatDuration(c.durationSeconds)}</span><span>{c.recordingState}</span></div></Link>)}</div></>
}
/**
 * Real sign-in when this build talks to a backend (`LIVE`); the original
 * fixture-only flow otherwise, unchanged, so the offline demo keeps working
 * exactly as it always has.
 *
 * `finish` is the one seam both paths funnel through: store the company API
 * key `setAuthToken` already exists for, then a full navigation rather than
 * client-side routing — `hydrate()` on the next mount is what actually loads
 * this company's data, and that only runs once, on mount (see `app.tsx`).
 */
function finish(session:{apiKey:string;phoneNumberId:string;companyName:string;email:string;role:'owner'|'staff'}){
  setAuthToken(session.apiKey);
  localStorage.setItem('ca-session',JSON.stringify({email:session.email,name:session.companyName,userId:session.phoneNumberId,orgId:session.phoneNumberId,portal:'company',role:session.role}));
  window.location.href=session.role==='staff'?'/app/queue':'/app/dashboard';
}
export function AuthScreen({mode}:{mode:'login'|'signup'|'forgot'|'verify'|'invite'}){
  const {t,setSession,store,toast}=useApp();const navigate=useNavigate();
  const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[name,setName]=useState('');
  const [companyName,setCompanyName]=useState('');
  const [needsCompanyName,setNeedsCompanyName]=useState(false); // set after a first-time Google sign-in
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const title={login:'Sign in',signup:'Create an account',forgot:'Reset your password',verify:'Check your inbox',invite:'Accept invitation'}[mode];

  async function submitLive(e:React.FormEvent){
    e.preventDefault();setError('');
    if(needsCompanyName){
      if(!companyName.trim()){setError('Give your company a name.');return}
      setBusy(true);
      try{finish(await completeGoogleSignup(companyName.trim()))}
      catch(cause){setError(friendlyAuthError(cause));setBusy(false)}
      return;
    }
    if(mode==='forgot'){
      if(!email.includes('@')){setError('Enter a valid email address.');return}
      setBusy(true);
      try{await sendPasswordReset(email);navigate('/verify')}
      catch(cause){setError(friendlyAuthError(cause))}
      setBusy(false);return;
    }
    if(!email.includes('@')){setError('Enter a valid email address.');return}
    if(password.length<6){setError('Password must be at least six characters.');return}
    if(mode==='signup'&&!companyName.trim()){setError('Give your company a name.');return}
    setBusy(true);
    try{
      const session=mode==='signup'
        ?await registerWithEmail(email,password,companyName.trim())
        :await loginWithEmail(email,password);
      finish(session);
    }catch(cause){
      if(cause instanceof NeedsSignup){
        setError('No company is linked to this account yet. Create one below.');
        navigate('/signup');
      }else{
        setError(friendlyAuthError(cause));
      }
      setBusy(false);
    }
  }
  async function submitGoogle(){
    setError('');setBusy(true);
    try{finish(await loginWithGoogle())}
    catch(cause){
      if(cause instanceof NeedsSignup){setNeedsCompanyName(true);setBusy(false)}
      else{setError(friendlyAuthError(cause));setBusy(false)}
    }
  }
  // Real accounts, real credentials — not fixtures. Whoever clicks these signs
  // into the actual "Calling Agent" demo company, which can place real calls
  // and send real WhatsApp messages. Kept here deliberately for showing the
  // product around without typing a password each time.
  async function demoLogin(email:string){
    setError('');setBusy(true);
    try{finish(await loginWithEmail(email,'Demo@1234'))}
    catch(cause){setError(friendlyAuthError(cause));setBusy(false)}
  }
  function submitMock(e:React.FormEvent){
    e.preventDefault();
    if(mode==='forgot'){navigate('/verify');return}
    if(mode==='verify'){navigate('/login');return}
    if(!email.includes('@')){setError('Enter a valid email address.');return}
    if(password.length<6){setError('Password must be at least six characters.');return}
    setSession({email,name:name||email.split('@')[0],userId:'user-1',orgId:store.organizations[0].id,portal:'company'});
    toast(t('Signed in'));navigate('/app/dashboard');
  }

  return <div className="auth-page"><div className="card auth-card">
    <div className="auth-history-row"><HistoryControls/></div>
    <Link to="/" className="brand"><span className="brand-mark"><Activity size={21}/></span>calling agent</Link>
    <h1 style={{fontSize:'1.8rem'}}>{t(needsCompanyName?'Name your company':title)}</h1>
    <p className="muted">{t(LIVE?'Sign in to run your calling agent.':'One place to build, run and understand your voice agents.')}</p>
    {mode==='verify'?<div className="stack">
      <div className="notice">{t(LIVE?'Check your inbox for the reset link. It can take a minute to arrive.':'We simulated a verification email. Your account is ready to use.')}</div>
      <Button to="/login">{t('Back to sign in')}</Button>
    </div>:<form className="stack" onSubmit={LIVE?submitLive:submitMock}>
      {needsCompanyName
        ?<Field label="Company name" value={companyName} onChange={setCompanyName} help="What your customers call you." required/>
        :<>
          {mode==='signup'&&!LIVE&&<Field label="Full name" value={name} onChange={setName} required/>}
          {mode==='signup'&&LIVE&&<Field label="Company name" value={companyName} onChange={setCompanyName} help="What your customers call you." required/>}
          <Field label="Email" value={email} onChange={setEmail} type="email" required/>
          {mode!=='forgot'&&<Field label="Password" value={password} onChange={setPassword} type="password" help="Use at least six characters." required/>}
        </>}
      {error&&<div className="notice danger">{t(error)}</div>}
      <Button type="submit" disabled={busy}>{t(busy?'Please wait…':needsCompanyName?'Create company':mode==='forgot'?'Send reset link':mode==='invite'?'Accept invitation':mode==='signup'?'Create account':'Sign in')}</Button>
      {(mode==='login'||mode==='signup')&&!needsCompanyName&&(LIVE
        ?<button className="button outline" type="button" disabled={busy} onClick={submitGoogle}>{t('Continue with Google')}</button>
        :<button className="button outline" type="button" onClick={()=>{setSession({email:'demo@example.com',name:'Demo User',userId:'user-1',orgId:store.organizations[0].id,portal:'company'});navigate('/app/dashboard')}}>{t('Continue with Google')}</button>)}
      {mode==='login'&&LIVE&&!needsCompanyName&&<div className="demo-logins">
        <div className="small muted">{t('Or try the live demo — real data, not a fixture')}</div>
        <div className="demo-row">
          <button className="button outline small" type="button" disabled={busy}
            onClick={()=>void demoLogin('admin@amgoc.us')}>{t('Demo: company owner')}</button>
          <button className="button outline small" type="button" disabled={busy}
            onClick={()=>void demoLogin('employee@amgoc.us')}>{t('Demo: employee')}</button>
        </div>
      </div>}
      {mode==='login'&&!LIVE&&<div className="demo-logins"><div className="small muted">{t('Or open one of the three portals')}</div><div className="demo-row"><button className="button outline small" type="button" onClick={()=>{setSession({email:'samira@northstar.edu',name:'Samira Khan',userId:'user-1',orgId:'org-northstar',portal:'company'});navigate('/app/dashboard')}}>{t('Company admin')}</button><button className="button outline small" type="button" onClick={()=>{setSession({email:'omar@northstar.edu',name:'Omar Farooq',userId:'user-2',orgId:'org-northstar',portal:'company'});navigate('/app/queue')}}>{t('Company staff')}</button><button className="button outline small" type="button" onClick={()=>{api.setProfile('user-platform');setSession({email:'ops@platform.internal',name:'Platform Operations',userId:'user-platform',orgId:'org-northstar',portal:'platform',platformRole:'superadmin'});navigate('/platform/companies')}}>{t('Platform (us)')}</button></div></div>}
    </form>}
    {!needsCompanyName&&<div className="row between small" style={{marginTop:24}}><Link to={mode==='login'?'/signup':'/login'}>{t(mode==='login'?'Create an account':'Sign in')}</Link><Link to="/forgot-password">{t('Forgot password?')}</Link></div>}
  </div></div>;
}
function UploadZone({kbId}:{kbId:string}){const {t,run}=useApp();const ref=useRef<HTMLInputElement>(null);const upload=(files:FileList|null)=>{if(!files||!kbId)return;const valid=Array.from(files).filter(f=>/\.(pdf|docx|txt|md)$/i.test(f.name)&&f.size<=20*1024*1024);if(valid.length)run(()=>api.uploadDocuments(kbId,valid.map(f=>({name:f.name,size:f.size,type:f.type}))),'Documents added for indexing')};return <div className="dropzone" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();upload(e.dataTransfer.files)}} onClick={()=>ref.current?.click()}><Upload size={27} color="var(--accent)"/><strong style={{display:'block',marginTop:10}}>{t('Upload documents')}</strong><div className="small muted">{t('Drop PDF, DOCX, TXT or MD files here · 20 MB maximum')}</div><input ref={ref} type="file" multiple accept=".pdf,.docx,.txt,.md" style={{display:'none'}} onChange={e=>upload(e.target.files)}/></div>}
function SettingField({setting,label,initial='',type='text',help,placeholder}:{setting:string;label:string;initial?:string|number;type?:string;help?:string;placeholder?:string}){const {store,org,run}=useApp();const value=store.settings[org.id]?.[setting]??initial;return <Field label={label} value={String(value)} type={type} help={help} placeholder={placeholder} onChange={x=>run(()=>api.updateSetting(org.id,setting,type==='number'?Number(x):x),'Settings saved')}/>}

function SettingCheck({setting,label,initial=false,help}:{setting:string;label:string;initial?:boolean;help?:string}){const {store,org,run}=useApp();return <CheckRow label={label} checked={Boolean(store.settings[org.id]?.[setting]??initial)} help={help} onChange={x=>run(()=>api.updateSetting(org.id,setting,x),'Settings saved')}/>}

function SettingSelect({setting,label,options,initial}:{setting:string;label:string;options:string[];initial:string}){const {store,org,run}=useApp();return <FieldSelect label={label} value={String(store.settings[org.id]?.[setting]??initial)} options={options} onChange={x=>run(()=>api.updateSetting(org.id,setting,x),'Settings saved')}/>}
export function Agents(){const {store,org,t,run}=useApp();const navigate=useNavigate();const agents=store.agents.filter(x=>x.orgId===org.id);const [name,setName]=useState(''),[creating,setCreating]=useState(false);return <><PageHead eyebrow="Build" title="Agents" description="Give every conversation a consistent voice." action={<Button onClick={()=>setCreating(true)}><Plus size={16}/>{t('New agent')}</Button>}/>{agents.length?<div className="grid cols-3">{agents.map(agent=><Link to={'/app/agents/'+agent.id} className="card lift" key={agent.id}><div className="row between"><div className="brand-mark" style={{width:45,height:45,fontSize:'1rem'}}>{agent.name.slice(0,1)}</div><Badge tone={statusTone(agent.status)}>{t(agent.status)}</Badge></div><h2 style={{marginTop:18,marginBottom:5}}>{agent.name}</h2><p className="small muted">{store.channels.filter(c=>agent.channelIds.includes(c.id)).map(c=>c.label).join(' · ')||t('No numbers yet')}</p><div className="row between" style={{marginTop:24}}><span className="small muted">{store.calls.filter(x=>x.agentId===agent.id&&Date.now()-new Date(x.startedAt).getTime()<7*86400000).length} {t('calls this week')}</span><ArrowRight size={16}/></div></Link>)}</div>:<Empty icon={Mic2} title="No agents yet" body="Create an agent to start designing its voice and persona." action={<Button onClick={()=>setCreating(true)}>{t('New agent')}</Button>}/>} {creating&&<div className="modal-backdrop" onClick={()=>setCreating(false)}><div className="modal" onClick={e=>e.stopPropagation()}><h2>{t('Create an agent')}</h2><Field label="Agent name" value={name} onChange={setName}/><div className="row" style={{justifyContent:'flex-end',marginTop:20}}><Button variant="secondary" onClick={()=>setCreating(false)}>{t('Cancel')}</Button><Button disabled={!name.trim()} onClick={async()=>{const row=await run(()=>api.createAgent(org.id,name),'Agent created');if(row){setCreating(false);navigate('/app/agents/'+row.id)}}}>{t('Create agent')}</Button></div></div></div>}</>}
const personaTemplates:Record<string,Partial<Persona>>={
  Admissions:{roleDescription:'Guide prospective students through programmes, eligibility and admissions dates.',toneNotes:'Reassuring, concise and welcoming.',escalationRules:'Escalate individual eligibility decisions to admissions staff.'},
  'Customer support':{roleDescription:'Resolve common support requests using the approved knowledge base.',toneNotes:'Patient and practical.',escalationRules:'Transfer account-specific problems to a human.'},
  Collections:{roleDescription:'Discuss outstanding balances with clarity and respect.',toneNotes:'Calm, firm and non-judgmental.',escalationRules:'Transfer disputes or hardship requests to a human.'},
  'Appointment booking':{roleDescription:'Help callers find and confirm a convenient appointment.',toneNotes:'Efficient and friendly.',escalationRules:'Transfer urgent medical concerns to a human.'},
  'Lead qualification':{roleDescription:'Understand caller needs and route qualified enquiries.',toneNotes:'Curious and concise.',escalationRules:'Transfer complex pricing requests to sales.'}
};
function compiled(p:Persona){return ['You are '+p.agentName+'.','Greeting: '+p.greeting,'Role: '+p.roleDescription,'Language: '+p.languagePolicy,'Tone: '+p.toneNotes,'Avoid: '+p.forbiddenPhrases.join(', '),'Escalate: '+p.escalationRules,'Closing: '+p.closingBehaviour,'Speaker gender: '+p.gender+'. Apply correct grammatical agreement where relevant.'].join('\n\n')}
export function Live(){const {store,org,t,run,setConnection}=useApp();const [selected,setSelected]=useState<string|null>(null),[listening,setListening]=useState(false),[offline,setOffline]=useState(false);const bottom=useRef<HTMLDivElement>(null);useEffect(()=>api.startLiveSimulation(),[]);const calls=store.calls.filter(x=>x.orgId===org.id&&x.status==='active');const chosen=calls.find(x=>x.id===(selected||calls[0]?.id));const transcript=store.transcripts.filter(x=>x.callId===chosen?.id),queries=store.ragQueries.filter(x=>x.callId===chosen?.id);useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'})},[transcript.length]);return <><PageHead eyebrow="Operations" title="Live monitoring" description="Follow the conversation and every retrieval decision as it happens." action={<Badge tone={offline?'danger':'live'}>{t(offline?'offline':'live')}</Badge>}/>{offline&&<div className="notice danger" style={{marginBottom:18}}>{t('The simulated connection is offline. Live events are paused.')} <Button small variant="outline" onClick={()=>{setOffline(false);setConnection('reconnecting');setTimeout(()=>setConnection('live'),1200)}}>{t('Reconnect')}</Button></div>}{calls.length?<div className="split"><div className="card"><h2>{t('Active calls')} · {calls.length}</h2><div className="stack">{calls.map(c=><button className="result-card" style={{textAlign:'start',background:selected===c.id?'var(--surface)':'var(--card)',color:'var(--foreground)',width:'100%'}} key={c.id} onClick={()=>setSelected(c.id)}><div className="row between"><strong className="phone">{c.phoneNumber}</strong><Badge tone="live">{t('live')}</Badge></div><div className="small muted">{t(channelNames[c.channelType])} · {t(c.direction)} · {store.agents.find(x=>x.id===c.agentId)?.name}</div></button>)}</div><button className="ghost-btn small" onClick={()=>setOffline(x=>{setConnection(x?'live':'offline');return !x})} style={{marginTop:20}}><WifiOff size={14}/>{t('Simulate connection drop')}</button></div>{chosen&&<div className="stack"><div className="card"><div className="row between"><div className="row"><div className="live-orb"/><div><strong>{store.agents.find(x=>x.id===chosen.agentId)?.name}</strong><div className="small muted">{chosen.mode==='operator'?t('A person'):t('Your agent is handling this')}</div></div></div><span className="mono">{formatDuration(Math.round((Date.now()-new Date(chosen.startedAt).getTime())/1000))}</span></div><div className="row wrap" style={{marginTop:25}}><Button variant="outline" onClick={()=>setListening(x=>!x)}>{listening?t('Stop listening'):t('Listen in')}</Button><Button variant="outline" disabled={chosen.mode==='operator'} onClick={()=>run(()=>api.takeOverCall(chosen.id),'You are on the call now')}>{t('Take over')}</Button><Button variant="danger" onClick={()=>run(()=>api.endCall(chosen.id),'Call ended')}>{t('End call')}</Button></div></div><div className="card"><h2>{t('Live transcript')}</h2><div aria-live="polite" style={{height:250,overflowY:'auto'}}>{transcript.map(line=><div className="result-card" key={line.id}><div className="small muted">{formatDate(line.timestamp)} · {line.speaker}</div><p>{line.text}</p></div>)}<div ref={bottom}/></div></div><div className="card"><h2>{t('Retrieval activity')}</h2>{queries.length?queries.map(q=><div className="notice" key={q.id} style={{marginBottom:10,borderColor:q.answered?'var(--success)':'var(--warning)'}}><strong>{q.query}</strong><div className="small">{q.documentName?t('From')+' '+q.documentName:t('Nothing in your documents covered this')} · {t(q.answered?'Answered':'Offered to find out')}</div></div>):<MiniEmpty text={t('This shows what your agent looks up while it is on the call.')}/>}</div></div>}</div>:<Empty icon={AudioLines} title="No active calls" body="Calls will appear here with live transcripts and retrieval events." action={<Button to="/app/history" variant="outline">{t('View history')}</Button>}/>}</>}
export function HistoryPage(){const {store,org,t}=useApp();const calls=store.calls.filter(x=>x.orgId===org.id);const [search,setSearch]=useState(''),[channel,setChannel]=useState('all'),[numberId,setNumberId]=useState('all'),[direction,setDirection]=useState('all'),[mode,setMode]=useState('all'),[outcome,setOutcome]=useState('all'),[agent,setAgent]=useState('all'),[days,setDays]=useState('90'),[view,setView]=useState('all');const filtered=calls.filter(c=>(!search||c.phoneNumber.includes(search)||store.transcripts.some(x=>x.callId===c.id&&x.text.toLowerCase().includes(search.toLowerCase())))&&(channel==='all'||c.channelType===channel)&&(numberId==='all'||c.channelId===numberId)&&(direction==='all'||c.direction===direction)&&(mode==='all'||c.mode===mode)&&(outcome==='all'||c.outcome===outcome)&&(agent==='all'||c.agentId===agent)&&(Date.now()-new Date(c.startedAt).getTime()<Number(days)*86400000)&&(view!=='needs-attention'||c.outcome==='Escalated'||c.status==='failed'));const csv=['Time,Direction,Caller,Number called,Channel,Handled by,Duration,Outcome,Knowledge used,Recording',...filtered.map(c=>[c.startedAt,c.direction,c.phoneNumber,store.channels.find(x=>x.id===c.channelId)?.label||'',c.channelType,c.mode,c.durationSeconds,c.outcome,c.knowledgeBaseName,c.recordingState].map(x=>'"'+String(x).replaceAll('"','""')+'"').join(','))].join('\r\n');const download=(name:string,content:string,type:string)=>{const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};return <><PageHead eyebrow="Operations" title="Call history" description={filtered.length+' '+t('calls match your filters')} action={<div className="row"><Button variant="outline" onClick={()=>download('calls.csv',csv,'text/csv')}>{t('Export CSV')}</Button><Button variant="outline" onClick={()=>exportCallsXlsx(filtered)}>{t('Export Excel')}</Button></div>}/><div className="card"><div className="field-grid" style={{marginBottom:18}}><Field label="Search number or transcript" value={search} onChange={setSearch}/><FieldSelect label="Saved view" value={view} onChange={setView} options={['all','needs-attention']} labels={{all:'All calls','needs-attention':'Needs attention'}}/><FieldSelect label="Date range" value={days} onChange={setDays} options={['7','30','90']} labels={{'7':'Last 7 days','30':'Last 30 days','90':'Last 90 days'}}/><FieldSelect label="Channel" value={channel} onChange={setChannel} options={['all','sim','whatsapp_call']} labels={{all:'Every channel',sim:'Phone line',whatsapp_call:'WhatsApp'}}/><FieldSelect label="Number called" value={numberId} onChange={setNumberId} options={['all',...store.channels.filter(x=>x.orgId===org.id).map(x=>x.id)]} labels={Object.fromEntries([['all','Any of your numbers'],...store.channels.filter(x=>x.orgId===org.id).map(x=>[x.id,x.label])])}/><FieldSelect label="Direction" value={direction} onChange={setDirection} options={['all','INBOUND','OUTBOUND']} labels={{all:'Both ways',INBOUND:'They called us',OUTBOUND:'We called them'}}/><FieldSelect label="Handled by" value={mode} onChange={setMode} options={['all','agent','operator']} labels={{all:'Anyone',agent:'The agent',operator:'A person'}}/><FieldSelect label="Outcome" value={outcome} onChange={setOutcome} options={['all',...Array.from(new Set(calls.map(x=>x.outcome)))]} labels={{all:'Any outcome'}}/><FieldSelect label="Agent" value={agent} onChange={setAgent} options={['all',...store.agents.filter(x=>x.orgId===org.id).map(x=>x.id)]} labels={Object.fromEntries([['all','Any agent'],...store.agents.filter(x=>x.orgId===org.id).map(x=>[x.id,x.name])])}/></div>{filtered.length?<CallTable calls={filtered} limit={100}/>:<Empty title="No matching calls" body="Try broadening the date range or clearing a filter."/>}{filtered.length>100&&<p className="small muted">{t('Showing first 100 calls. Export includes all filtered calls.')}</p>}</div></>}
/** A short clock time for a chat bubble — "6:48 PM", not a full date. The
 * contact list keeps the full `formatDate` since a day boundary matters
 * there and a bare time would be ambiguous for anything not from today. */
function chatTime(iso:string):string{
  return new Date(iso).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});
}

/**
 * Messages — one contact at a time, the way WhatsApp Web itself is laid
 * out: a fixed-height shell, a contact list on the left that never grows
 * the page, a thread on the right that scrolls inside itself with the
 * compose box pinned to its bottom. The old version was a flat, undated
 * list behind a plain dropdown filter; this groups by number because that
 * is what "who are we talking to" actually means here — not a filter on
 * one long log, a set of conversations.
 */
export function Messages(){
  const {store,org,t,run,readiness}=useApp();
  const messages=store.messages.filter(x=>x.orgId===org.id),templates=store.templates.filter(x=>x.orgId===org.id);
  const approved=templates.filter(x=>x.status==='approved');
  useEffect(()=>{if(LIVE)void api.getTemplates(org.id)},[org.id]);
  const whatsappConnected=LIVE
    ?store.channels.some(x=>x.orgId===org.id&&x.type==='whatsapp_message'&&x.status==='connected')
    :readiness.whatsappMessagingReady;

  const contacts=useMemo(()=>{
    const byNumber=new Map<string,Message[]>();
    for(const m of messages){
      const list=byNumber.get(m.toNumber);
      if(list)list.push(m);else byNumber.set(m.toNumber,[m]);
    }
    return Array.from(byNumber.entries())
      .map(([number,msgs])=>({number,msgs:msgs.slice().sort((a,b)=>+new Date(a.sentAt)-+new Date(b.sentAt))}))
      .sort((a,b)=>+new Date(b.msgs[b.msgs.length-1].sentAt)-+new Date(a.msgs[a.msgs.length-1].sentAt));
  },[messages]);

  const [selected,setSelected]=useState<string|null>(null);
  const [startingNew,setStartingNew]=useState(false);
  const [newNumber,setNewNumber]=useState('');
  useEffect(()=>{if(!selected&&contacts.length)setSelected(contacts[0].number)},[contacts,selected]);
  const active=selected??(startingNew?newNumber:'');
  const thread=contacts.find(c=>c.number===selected)?.msgs??[];

  const [body,setBody]=useState(''),[template,setTemplate]=useState('free');
  const [sending,setSending]=useState(false);
  const bottom=useRef<HTMLDivElement>(null);
  useEffect(()=>{bottom.current?.scrollIntoView({block:'end'})},[thread.length,selected]);

  const send=async()=>{
    if(!active||!body||sending)return;
    setSending(true);
    const row=await run(()=>api.sendMessage(org.id,active,body,template==='free'?undefined:template),undefined);
    setSending(false);
    if(row){setBody('');setTemplate('free');if(startingNew){setStartingNew(false);setSelected(active);setNewNumber('')}}
  };

  return <><PageHead eyebrow="Operations" title="Messages" description="One conversation at a time, the way WhatsApp itself shows them."/>
    {!whatsappConnected&&<div className="notice warning" style={{marginBottom:16}}>{t('WhatsApp messaging is not connected yet.')} <Link to="/app/channels">{t('Connect it')} →</Link></div>}
    <div className="chat-shell">
      <div className="chat-contacts">
        <div className="chat-contacts-head">
          <Button small variant="outline" onClick={()=>{setStartingNew(true);setSelected(null);setNewNumber('')}}><Plus size={14}/> {t('New conversation')}</Button>
        </div>
        <div className="chat-contacts-list">
          {contacts.length===0&&!startingNew&&<div style={{padding:20}}><MiniEmpty text={t('No conversations yet. Start one above.')}/></div>}
          {contacts.map(c=>{const last=c.msgs[c.msgs.length-1];return (
            <button key={c.number} className={'chat-contact'+(selected===c.number&&!startingNew?' active':'')}
              onClick={()=>{setSelected(c.number);setStartingNew(false)}}>
              <div className="chat-contact-top"><span className="mono">{c.number}</span><span className="chat-contact-time">{formatDate(last.sentAt)}</span></div>
              <div className="chat-contact-preview">{last.direction==='OUTBOUND'?t('You')+': ':''}{last.body}</div>
            </button>
          )})}
        </div>
      </div>
      <div className="chat-thread">
        {startingNew
          ?<div className="chat-thread-head"><Field label="" value={newNumber} onChange={setNewNumber} placeholder="+923001234567"/></div>
          :active&&<div className="chat-thread-head mono">{active}</div>}
        {!active&&!startingNew
          ?<div className="chat-empty">{t('Choose a conversation, or start a new one.')}</div>
          :<>
            <div className="chat-thread-body">
              {thread.map(m=><div className={'chat-bubble '+(m.direction==='OUTBOUND'?'out':'in')} key={m.id}>
                <div>{m.body}</div>
                <div className="chat-bubble-meta"><span>{chatTime(m.sentAt)}</span>{m.direction==='OUTBOUND'&&<Badge tone={statusTone(m.status)}>{t(m.status)}</Badge>}</div>
              </div>)}
              <div ref={bottom}/>
            </div>
            <div className="chat-compose">
              {approved.length>0&&<select className="select" style={{maxWidth:160}} value={template} onChange={e=>{setTemplate(e.target.value);if(e.target.value!=='free')setBody(templates.find(y=>y.id===e.target.value)?.bodyPreview||'')}}>
                <option value="free">{t('Free text')}</option>
                {approved.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}
              </select>}
              <textarea className="textarea" rows={1} value={body} onChange={e=>setBody(e.target.value)}
                placeholder={t('Type a message')}
                onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void send()}}}/>
              <Button disabled={!whatsappConnected||!active||!body||sending}
                title={!whatsappConnected?t('Connect WhatsApp messaging in Numbers first'):undefined}
                onClick={send}><Send size={16}/></Button>
            </div>
          </>}
      </div>
    </div>
    <div className="notice" style={{marginTop:14}}>{t('A free-text message only reaches someone inside the 24-hour window since they last messaged you. Outside it, use an approved template.')}</div>
  </>;
}
export function Guides(){const {store,t}=useApp();const [search,setSearch]=useState(''),[category,setCategory]=useState('All');const allGuides=LIVE?REAL_GUIDES:store.guides;const categories=['All',...Array.from(new Set(allGuides.map(x=>x.category)))];const guides=allGuides.filter(x=>(category==='All'||x.category===category)&&(x.title.toLowerCase().includes(search.toLowerCase())||x.bodyMd.toLowerCase().includes(search.toLowerCase())));return <><PageHead eyebrow="Self-serve help" title="Guides" description="Practical setup and troubleshooting for every part of the platform."/><div className="split"><div className="card"><Field label="Search guides" value={search} onChange={setSearch} placeholder="Search by topic or error"/><div className="row wrap" style={{marginTop:16}}>{categories.map(x=><button key={x} className={'badge '+(category===x?'success':'')} style={{border:0,cursor:'pointer'}} onClick={()=>setCategory(x)}>{t(x)}</button>)}</div></div><div className="card"><h2>{t('Start here')}</h2><p className="muted">{t('New here? Follow the 30-minute guide, then use the pre-launch checklist before enabling calls.')}</p><Button to="/app/guides/getting-started">{t('Read getting started')} <ArrowRight size={16}/></Button></div></div><div className="grid cols-3" style={{marginTop:18}}>{guides.map(g=><Link className="card lift" to={'/app/guides/'+g.slug} key={g.slug}><div className="eyebrow">{t(g.category)}</div><h2>{t(g.title)}</h2><p className="small muted">{g.bodyMd.slice(0,115)}...</p><div className="row between small muted"><span>{g.readingMinutes} {t('min read')}</span><ArrowRight size={16}/></div></Link>)}</div>{!guides.length&&<Empty icon={BookOpen} title="No matching guides" body="Try another search term or category."/>}</>}
export function GuideDetail(){const {slug}=useParams();const {store,t,toast}=useApp();const allGuides=LIVE?REAL_GUIDES:store.guides;const guide=allGuides.find(x=>x.slug===slug);const [helpful,setHelpful]=useState<string|null>(null);if(!guide)return <Empty title="Guide not found" body="Browse the guide library for another topic." action={<Button to="/app/guides">{t('Guides')}</Button>}/>;const actions:Record<string,string>={'getting-started':'/app/agents','knowledge-writing':'/app/knowledge','persona':'/app/agents','multiple-knowledge':'/app/knowledge','routing':'/app/setups','campaigns':'/app/campaigns','unanswered':'/app/unanswered','staff':'/app/team','recording':'/app/history','numbers':'/app/channels','go-live':'/app/setups'};return <><PageHead eyebrow={guide.category} title={guide.title} description={guide.readingMinutes+' '+t('min read')+' · '+t('Updated')+' '+formatDate(guide.updatedAt)} action={<Button to="/app/guides" variant="outline">{t('All guides')}</Button>}/><div className="split"><article className="card"><p style={{fontSize:'1.05rem',lineHeight:1.9}}>{guide.bodyMd}</p><h2>{t('Do this now')}</h2><p className="muted">{t('Open the relevant workspace page and apply this step.')}</p><Button to={actions[guide.slug]||'/app/dashboard'}>{t('Open it')} <ArrowRight size={16}/></Button><div style={{marginTop:30}} className="notice"><div className="row between"><strong>{t('Was this helpful?')}</strong><div className="row"><Button small variant={helpful==='yes'?'':'outline'} onClick={()=>{setHelpful('yes');toast(t('Thanks for the feedback'))}}>{t('Yes')}</Button><Button small variant={helpful==='no'?'':'outline'} onClick={()=>{setHelpful('no');toast(t('Thanks for the feedback'))}}>{t('No')}</Button></div></div></div></article><div className="card"><h2>{t('Related guides')}</h2>{allGuides.filter(x=>x.slug!==guide.slug&&x.category===guide.category).slice(0,4).map(x=><Link to={'/app/guides/'+x.slug} className="nav-link" key={x.slug}><BookOpen size={16}/>{t(x.title)}</Link>)}</div></div></>}
export function Team(){
  const {store,org,session,t,run}=useApp();
  const members=store.memberships.filter(x=>x.orgId===org.id), invitations=store.invitations.filter(x=>x.orgId===org.id&&x.status==='pending'),audit=store.audit.filter(x=>x.orgId===org.id);
  const role=members.find(x=>x.userId===session?.userId)?.role||'viewer',canManage=role==='owner'||role==='admin';
  const [email,setEmail]=useState(''),[inviteRole,setInviteRole]=useState('staff'),[auditSearch,setAuditSearch]=useState(''),[remove,setRemove]=useState<string|null>(null);
  return <><PageHead eyebrow="People" title="Team" description="Manage access to this workspace and review changes."/>
    <div className="split"><div className="card"><h2>{t('Members')}</h2>
      {members.map(m=>{const person=store.profiles.find(p=>p.id===m.userId);return <div className="row between result-card wrap" key={m.userId}><div className="row"><div className="brand-mark">{person?.fullName.slice(0,1)||'?'}</div><div><strong>{person?.fullName||m.userId}</strong><div className="small muted">{person?.email||'—'}</div></div></div><div className="row">{canManage&&m.role!=='owner'&&m.userId!==session?.userId?<><select className="select" value={m.role} onChange={e=>run(()=>api.changeMemberRole(org.id,m.userId,e.target.value as Role),'Role updated')} aria-label={t('Role')}><option value="admin">{t('admin')}</option><option value="staff">{t('staff')}</option></select><button className="icon-btn" title={t('Remove member')} onClick={()=>setRemove(m.userId)}><Trash2 size={16}/></button></>:<Badge>{t(m.role)}</Badge>}</div></div>})}
      <h2 style={{marginTop:25}}>{t('Pending invitations')}</h2>
      {invitations.length?invitations.map(inv=><div className="row between result-card wrap" key={inv.id}><div><strong>{inv.email}</strong><div className="small muted">{t(inv.role)} · {t('Expires')} {formatDate(inv.expiresAt)}</div></div><div className="row"><Button small variant="outline" disabled={!canManage} onClick={()=>run(()=>api.resendInvitation(inv.id),'Invitation renewed')}>{t('Resend')}</Button><Button small variant="outline" disabled={!canManage} onClick={()=>run(()=>api.revokeInvitation(inv.id),'Invitation revoked')}>{t('Revoke')}</Button></div></div>):<MiniEmpty text={t('No pending invitations.')}/>}
    </div><div className="card stack"><h2>{t('Invite a teammate')}</h2><Field label="Email" value={email} onChange={setEmail} type="email"/><FieldSelect label="Role" value={inviteRole} onChange={setInviteRole} options={['admin','staff']} labels={{admin:'Admin — can change everything',staff:'Staff — takes calls only'}}/><Button disabled={!canManage||!email.includes('@')} title={!canManage?'Only owners and admins can invite members':undefined} onClick={async()=>{const r=await run(()=>api.invite(org.id,email,inviteRole as Role),'Invitation created');if(r)setEmail('')}}>{t('Create invitation')}</Button><div className="notice">{t('Invitations are simulated in this frontend. No email is sent.')}</div></div></div>
    <div className="card" style={{marginTop:18}}><h2>{t('Permissions')}</h2><div className="table-wrap"><table className="table"><thead><tr>{['Capability','Owner','Admin','Operator','Viewer'].map(x=><th key={x}>{t(x)}</th>)}</tr></thead><tbody>{[['Manage billing','✓','—','—','—'],['Manage channels','✓','✓','—','—'],['Handle live calls','✓','✓','✓','—'],['View reports','✓','✓','✓','✓']].map(row=><tr key={row[0]}>{row.map((x,i)=><td key={i}>{t(x)}</td>)}</tr>)}</tbody></table></div></div>
    <div className="card" style={{marginTop:18}}><div className="row between"><h2>{t('Audit log')}</h2><input className="input" style={{maxWidth:250}} value={auditSearch} onChange={e=>setAuditSearch(e.target.value)} placeholder={t('Filter activity')}/></div><div className="table-wrap"><table className="table"><thead><tr>{['Time','Actor','Action','Target'].map(x=><th key={x}>{t(x)}</th>)}</tr></thead><tbody>{audit.filter(x=>(x.action+' '+x.actorName).toLowerCase().includes(auditSearch.toLowerCase())).map(x=><tr key={x.id}><td>{formatDate(x.createdAt)}</td><td>{x.actorName}</td><td>{x.action}</td><td className="mono">{x.targetType} · {x.targetId}</td></tr>)}</tbody></table></div></div>
    {remove&&<Confirm title="Remove team member?" body="This person will lose access to the workspace immediately." onClose={()=>setRemove(null)} confirm={()=>run(()=>api.removeMember(org.id,remove),'Member removed')}/>}
  </>
}
export function Usage(){const {store,org,t,run}=useApp();const plan=store.plans.find(x=>x.name===org.plan)||store.plans[0];const calls=store.calls.filter(x=>x.orgId===org.id),messages=store.messages.filter(x=>x.orgId===org.id),agents=store.agents.filter(x=>x.orgId===org.id),seats=store.memberships.filter(x=>x.orgId===org.id);const metrics=[['Call minutes',Math.round(calls.reduce((n,x)=>n+x.durationSeconds,0)/60),plan.includedMinutes],['Messages',messages.length,plan.includedMessages],['Storage MB',Math.round(store.documents.filter(x=>store.knowledgeBases.some(k=>k.orgId===org.id&&k.id===x.kbId)).reduce((n,x)=>n+x.bytes,0)/1048576),plan.storageMb],['Agents',agents.length,plan.agentLimit],['Seats',seats.length,plan.seatLimit]] as const;const data=Array.from({length:14},(_,i)=>{const d=new Date(Date.now()-(13-i)*86400000);return {day:d.toLocaleDateString(undefined,{month:'short',day:'numeric'}),minutes:Math.round(calls.filter(x=>new Date(x.startedAt).toDateString()===d.toDateString()).reduce((n,x)=>n+x.durationSeconds,0)/60)}});return <><PageHead eyebrow="Billing" title="Usage" description={'Current plan: '+plan.name}/><div className="grid cols-3">{metrics.map(([label,used,max])=>{const pct=Math.round(Number(used)/Number(max)*100);return <div className="card" key={label}><div className="row between"><h2>{t(label)}</h2><Badge tone={pct>=100?'danger':pct>=80?'warning':'success'}>{pct}%</Badge></div><div className="stat-number">{Number(used).toLocaleString()} <span className="small muted">/ {Number(max).toLocaleString()}</span></div><div className="meter" style={{marginTop:16}}><span style={{width:Math.min(pct,100)+'%',background:pct>=100?'var(--destructive)':pct>=80?'var(--warning)':'var(--accent)'}}/></div>{pct>=80&&<p className="small" style={{color:pct>=100?'var(--destructive)':'var(--warning)'}}>{t(pct>=100?'Plan limit reached':'Approaching plan limit')}</p>}</div>})}</div><div className="split" style={{marginTop:18}}><div className="card"><h2>{t('Daily call minutes')}</h2><div className="chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data}><CartesianGrid stroke="var(--border)" vertical={false}/><XAxis dataKey="day" tick={{fill:'var(--muted)',fontSize:11}}/><YAxis tick={{fill:'var(--muted)',fontSize:11}}/><Tooltip contentStyle={{background:'var(--card)',border:'1px solid var(--border)',borderRadius:9}}/><Bar dataKey="minutes" fill="var(--chart-1)" radius={[5,5,0,0]}/></BarChart></ResponsiveContainer></div></div><div className="card"><h2>{t('Cost by channel')}</h2>{(['sim','whatsapp_call'] as ChannelType[]).map(type=><div className="row between result-card" key={type}><span>{t(channelNames[type])}</span><strong>{money(Math.round(calls.filter(x=>x.channelType===type).length*0.32))}</strong></div>)}<p className="small muted">{t('Estimated from fixture usage. No charges are made.')}</p></div></div><div className="card" style={{marginTop:18}}><h2>{t('Plans')}</h2><div className="grid cols-3">{store.plans.map(p=><div className="side-panel" key={p.id}><h3>{p.name}</h3><div className="stat-number">{money(p.priceMonthly)}<span className="small">/{t('month')}</span></div><p className="small muted">{p.includedMinutes.toLocaleString()} {t('minutes')} · {p.agentLimit} {t('agents')}</p><Badge tone={p.name===org.plan?'success':''}>{t(p.name===org.plan?'Current plan':'Available')}</Badge>{p.name!==org.plan&&<div style={{marginTop:14}}><Button small variant="outline" onClick={()=>run(()=>api.changePlan(org.id,p.name),'Plan changed')}>{t('Choose plan')}</Button></div>}</div>)}</div></div><div className="card" style={{marginTop:18}}><h2>{t('Invoice history')}</h2><Empty title="No invoices yet" body="Invoices will appear here when billing is connected." /></div></>}










