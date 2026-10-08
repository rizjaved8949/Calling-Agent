import {useEffect,useMemo,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {AlertTriangle,Building2,CheckCircle2,Eye,Plus,ServerCog,ShieldCheck,X} from 'lucide-react';
import {Badge,Button,Empty,Field,PageHead} from './app';
import {useApp} from './app-context';
import {formatDate} from './lib/format';
import {api} from './lib/api';
import {ConnectionSetup} from './connection-setup';
import {presetKindLabel,presetsOfKind} from './lib/presets';
import type {ChannelType,Preset,PresetKind,ProvisioningTask} from './lib/types';

/**
 * The platform portal. Everything a company is not allowed to see lives here:
 * provider names, credentials, thresholds, model names and raw errors.
 */

const CHANNEL_LABEL:Record<ChannelType,string>={sim:'Phone line',whatsapp_call:'WhatsApp calling',whatsapp_message:'WhatsApp messaging'};
const STATE_TONE:Record<ProvisioningTask['state'],string>={requested:'',setting_up:'warning',testing:'warning',active:'success',blocked:'danger'};
const STATE_LABEL:Record<ProvisioningTask['state'],string>={requested:'Requested',setting_up:'Being set up',testing:'Testing',active:'Active',blocked:'Blocked'};
const HEALTH_TONE:Record<string,string>={ok:'success',warning:'warning',error:'danger'};

type CompanyRow=Awaited<ReturnType<typeof api.getPlatformCompanies>>[number];

export function PlatformCompanies(){
  const {run,t,session,setSession}=useApp();
  const navigate=useNavigate();
  const [rows,setRows]=useState<CompanyRow[]>([]);
  const [creating,setCreating]=useState(false);
  const [name,setName]=useState('');
  const [country,setCountry]=useState('+92');
  const load=()=>api.getPlatformCompanies().then(setRows);
  useEffect(()=>{load()},[]);
  const create=async()=>{await run(()=>api.createCompany(name,country),'Company created.');setCreating(false);setName('');load()};
  const impersonate=async(orgId:string)=>{
    await api.startImpersonation(orgId);
    if(session)setSession({...session,impersonating:{orgId,startedAt:new Date().toISOString()}});
    navigate('/app/dashboard');
  };
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Companies" description="Every customer on the platform."
      action={<Button onClick={()=>setCreating(true)}><Plus size={15}/> New company</Button>}/>
    <div className="card table-wrap"><table className="table">
      <thead><tr><th>{t('Company')}</th><th>{t('Plan')}</th><th>{t('Status')}</th><th>{t('Minutes')}</th><th>{t('Calls')}</th><th>{t('Last call')}</th><th>{t('Health')}</th><th/></tr></thead>
      <tbody>{rows.map(row=><tr key={row.org.id}>
        <td><Link to={'/platform/companies/'+row.org.id}><strong>{row.org.name}</strong></Link><div className="small muted mono">{row.org.id}</div></td>
        <td>{row.org.plan}</td>
        <td><Badge tone={row.org.status==='active'?'success':row.org.status==='suspended'?'danger':'warning'}>{t(row.org.status)}</Badge></td>
        <td className="mono">{row.minutesThisPeriod}</td>
        <td className="mono">{row.callCount}</td>
        <td className="small">{formatDate(row.lastCallAt)}</td>
        <td><Badge tone={HEALTH_TONE[row.health]}>{row.openIssues?row.openIssues+' '+t('issues'):t('healthy')}</Badge></td>
        <td><Button small variant="outline" onClick={()=>impersonate(row.org.id)}><Eye size={14}/> View as</Button></td>
      </tr>)}</tbody>
    </table></div>
    {creating&&<div className="modal-backdrop" onClick={()=>setCreating(false)}><div className="modal" onClick={e=>e.stopPropagation()}>
      <div className="row between"><h2>{t('New company')}</h2><button className="icon-btn" onClick={()=>setCreating(false)}><X size={18}/></button></div>
      <Field label="Company name" value={name} onChange={setName} placeholder="Harbor Health"/>
      <Field label="Country code" value={country} onChange={setCountry} placeholder="+92"/>
      <div className="row"><Button variant="outline" onClick={()=>setCreating(false)}>Cancel</Button><Button onClick={create} disabled={!name.trim()}>Create</Button></div>
    </div></div>}
  </div>;
}

export function PlatformCompanyDetail(){
  const {store,run,t}=useApp();
  const {id}=useParams();
  const org=store.organizations.find(x=>x.id===id);
  const tasks=store.provisioning.filter(x=>x.orgId===id);
  const channels=store.channels.filter(x=>x.orgId===id);
  const credentials=store.credentials.filter(x=>x.orgId===id);
  const members=store.memberships.filter(x=>x.orgId===id);
  const [secret,setSecret]=useState('');
  const [keyName,setKeyName]=useState('accessToken');
  const [channelId,setChannelId]=useState(channels[0]?.id||'');
  if(!org)return <Empty icon={Building2} title="Company not found" body="It may have been deleted." action={<Button to="/platform/companies">Back</Button>}/>;
  const saveSecret=async()=>{
    if(secret.length<4)return;
    await run(()=>api.setCredential(org.id,channelId,keyName,secret.slice(-4)),'Credential stored. The value was discarded.');
    setSecret('');
  };
  return <div className="stack">
    <PageHead eyebrow="Company" title={org.name} description={'Plan ' + org.plan + ' · ' + org.timezone + ' · created ' + formatDate(org.createdAt)}
      action={<div className="row">
        <Button variant="outline" onClick={()=>run(()=>api.setCompanyStatus(org.id,org.status==='suspended'?'active':'suspended'),'Status changed.')}>{org.status==='suspended'?'Reactivate':'Suspend'}</Button>
      </div>}/>

    <div className="card stack">
      <h2>{t('Numbers and setup')}</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('Purpose')}</th><th>{t('Channel')}</th><th>{t('Number')}</th><th>{t('Provider')}</th><th>{t('Company sees')}</th><th>{t('Raw status')}</th></tr></thead>
        <tbody>{channels.map(ch=>{
          const task=tasks.find(x=>x.channelId===ch.id);
          return <tr key={ch.id}>
            <td><strong>{ch.label}</strong>{ch.isPrimary&&<span className="small muted"> · main</span>}</td>
            <td>{t(CHANNEL_LABEL[ch.type])}</td>
            <td className="mono">{ch.displayNumber||'—'}</td>
            <td className="small muted">{ch.provider}</td>
            <td><Badge tone={task?STATE_TONE[task.state]:ch.status==='connected'?'success':'warning'}>{task?t(STATE_LABEL[task.state]):t(ch.status)}</Badge></td>
            <td className="small mono danger">{ch.errorDetail||'—'}</td>
          </tr>;
        })}</tbody>
      </table></div>
    </div>

    <ConnectionSetup orgId={org.id}/>

    <div className="grid cols-2">
      <div className="card stack">
        <h2>{t('Chosen behaviour')}</h2>
        <div className="row between"><span>{t('Answer strictness')}</span><strong>{store.presets.find(p=>p.id===org.strictnessPreset)?.label}</strong></div>
        <div className="row between"><span>{t('Conversation pace')}</span><strong>{store.presets.find(p=>p.id===org.pacePreset)?.label}</strong></div>
        <div className="row between"><span>{t('Voice quality')}</span><strong>{store.presets.find(p=>p.id===org.voiceQualityPreset)?.label}</strong></div>
        <Button small variant="outline" to="/platform/technical">Override the values</Button>
      </div>
      <div className="card stack">
        <h2>{t('People')}</h2>
        {members.map(m=>{const p=store.profiles.find(x=>x.id===m.userId);return <div className="row between" key={m.userId}><span>{p?.fullName||m.userId}</span><Badge>{t(m.role)}</Badge></div>})}
      </div>
    </div>
  </div>;
}

export function PlatformProvisioning(){
  const {store,run,t}=useApp();
  const tasks=store.provisioning;
  const [numberDraft,setNumberDraft]=useState<Record<string,string>>({});
  const order:ProvisioningTask['state'][]=['blocked','requested','setting_up','testing','active'];
  const sorted=[...tasks].sort((a,b)=>order.indexOf(a.state)-order.indexOf(b.state));
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Setup queue" description="Turning signed-up companies into working ones. The company never sees any of this detail."/>
    {sorted.length===0?<Empty icon={ServerCog} title="Nothing waiting" body="New requests appear here as companies sign up."/>:
    <div className="stack">{sorted.map(task=>{
      const org=store.organizations.find(x=>x.id===task.orgId);
      const channel=store.channels.find(c=>c.id===task.channelId);
      return <div className="card stack" key={task.id}>
        <div className="row between">
          <div><strong>{org?.name}</strong> <span className="small muted">· {channel?.label||t(CHANNEL_LABEL[task.kind])} · {t(CHANNEL_LABEL[task.kind])}</span></div>
          <Badge tone={STATE_TONE[task.state]}>{t(STATE_LABEL[task.state])}</Badge>
        </div>
        {task.note&&<div className="small muted">{task.note}</div>}
        <div className="row">
          <input className="input" placeholder={t('Assign a number, e.g. +92 42 111 000 222')} value={numberDraft[task.id]??channel?.displayNumber??''} onChange={e=>setNumberDraft(d=>({...d,[task.id]:e.target.value}))}/>
          <Button small variant="outline" onClick={()=>run(()=>api.assignNumber(task.orgId,task.channelId,numberDraft[task.id]||''),'Number assigned.')} disabled={!(numberDraft[task.id]||'').trim()}>Assign</Button>
        </div>
        <div className="row wrap">
          {order.map(state=><Button key={state} small variant={task.state===state?'':'outline'} onClick={()=>run(()=>api.updateProvisioning(task.id,{state}),'Setup moved to '+STATE_LABEL[state]+'.')}>{t(STATE_LABEL[state])}</Button>)}
        </div>
        <div className="small muted">{t('The company currently sees')}: <strong>{t(STATE_LABEL[task.state])}</strong>. {t('Updated')} {formatDate(task.updatedAt)}.</div>
      </div>;
    })}</div>}
  </div>;
}

export function PlatformTechnical(){
  const {store,run,t}=useApp();
  const [orgId,setOrgId]=useState(store.organizations[0]?.id||'');
  const org=store.organizations.find(x=>x.id===orgId);
  const bases=store.knowledgeBases.filter(x=>x.orgId===orgId);
  const voices=store.voices.filter(x=>x.orgId===orgId);
  if(!org)return <Empty icon={ShieldCheck} title="No companies" body="Create a company first."/>;
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Technical controls"
      description="The real values behind every choice a company makes. Nothing on this page is visible to customers."/>
    <div className="card"><div className="field"><label>{t('Company')}</label>
      <select className="select" value={orgId} onChange={e=>setOrgId(e.target.value)}>{store.organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></div></div>

    <div className="card stack">
      <h2>{t('Retrieval, per knowledge base')}</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('Knowledge base')}</th><th>similarityThreshold</th><th>topK</th><th>chunkSize</th><th>chunkOverlap</th><th>maxContextChars</th><th>embeddingModel</th></tr></thead>
        <tbody>{bases.map(kb=><tr key={kb.id}>
          <td><strong>{kb.name}</strong></td>
          <td><input className="input small-input mono" type="number" step="0.01" value={kb.similarityThreshold} onChange={e=>run(()=>api.updateKnowledgeBase(kb.id,{similarityThreshold:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={kb.topK} onChange={e=>run(()=>api.updateKnowledgeBase(kb.id,{topK:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={kb.chunkSize} onChange={e=>run(()=>api.updateKnowledgeBase(kb.id,{chunkSize:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={kb.chunkOverlap} onChange={e=>run(()=>api.updateKnowledgeBase(kb.id,{chunkOverlap:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={kb.maxContextChars} onChange={e=>run(()=>api.updateKnowledgeBase(kb.id,{maxContextChars:Number(e.target.value)}))}/></td>
          <td className="small mono muted">{kb.embeddingModel}</td>
        </tr>)}</tbody>
      </table></div>
    </div>

    <div className="card stack">
      <h2>{t('Voice and turn-taking, per agent')}</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('Voice')}</th><th>speed</th><th>vadThreshold</th><th>vadSilenceMs</th><th>vadPrefixPaddingMs</th><th>sttModel</th><th>turnDetection</th></tr></thead>
        <tbody>{voices.map(v=><tr key={v.id}>
          <td><strong>{v.voiceName}</strong><div className="small muted mono">{v.language}</div></td>
          <td><input className="input small-input mono" type="number" step="0.05" value={v.speed} onChange={e=>run(()=>api.updateVoice(v.id,{speed:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" step="0.05" value={v.vadThreshold} onChange={e=>run(()=>api.updateVoice(v.id,{vadThreshold:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={v.vadSilenceMs} onChange={e=>run(()=>api.updateVoice(v.id,{vadSilenceMs:Number(e.target.value)}))}/></td>
          <td><input className="input small-input mono" type="number" value={v.vadPrefixPaddingMs} onChange={e=>run(()=>api.updateVoice(v.id,{vadPrefixPaddingMs:Number(e.target.value)}))}/></td>
          <td className="small mono muted">{v.sttModel}</td>
          <td className="small mono muted">{v.turnDetection}</td>
        </tr>)}</tbody>
      </table></div>
    </div>

    <div className="card stack">
      <h2>{t('Model and recording internals')}</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('Key')}</th><th>{t('Value')}</th></tr></thead>
        <tbody>{Object.entries(store.settings[orgId]||{}).map(([k,v])=><tr key={k}>
          <td className="mono small">{k}</td>
          <td><input className="input small-input mono" value={String(v)} onChange={e=>run(()=>api.updateSetting(orgId,k,e.target.value))}/></td>
        </tr>)}</tbody>
      </table></div>
    </div>
  </div>;
}

export function PlatformPresets(){
  const {store,run,t}=useApp();
  const [counts,setCounts]=useState<Record<string,number>>({});
  useEffect(()=>{Promise.all(store.presets.map(p=>api.countCompaniesOnPreset(p.id).then(n=>[p.id,n] as const))).then(pairs=>setCounts(Object.fromEntries(pairs)))},[store.presets,store.organizations]);
  const kinds:PresetKind[]=['strictness','pace','voiceQuality'];
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Presets" description="What each plain-language choice actually does. Changing one moves every company on it."/>
    {kinds.map(kind=><div className="card stack" key={kind}>
      <h2>{t(presetKindLabel[kind])}</h2>
      {presetsOfKind(store.presets,kind).map((preset:Preset)=><div className="preset-edit" key={preset.id}>
        <div className="row between">
          <div><strong>{preset.label}</strong>{preset.isDefault&&<span className="small muted"> · {t('default')}</span>}</div>
          <Badge tone={counts[preset.id]?'warning':''}>{counts[preset.id]||0} {t('companies on this')}</Badge>
        </div>
        <div className="small muted">{preset.description}</div>
        <div className="chip-row">{Object.entries(preset.values).map(([k,v])=>
          <label className="value-chip" key={k}><span className="mono small">{k}</span>
            <input className="input small-input mono" defaultValue={String(v)}
              onBlur={e=>{const next={...preset.values,[k]:isNaN(Number(e.target.value))?e.target.value:Number(e.target.value)};run(()=>api.savePreset(preset.id,{values:next}),'Preset updated for '+(counts[preset.id]||0)+' companies.')}}/>
          </label>)}</div>
      </div>)}
    </div>)}
  </div>;
}

export function PlatformHealth(){
  const {store,t}=useApp();
  const failedChannels=store.channels.filter(c=>c.status==='error');
  const blocked=store.provisioning.filter(p=>p.state==='blocked');
  const failedCalls=store.calls.filter(c=>c.status==='failed').slice(0,20);
  const stuckIndex=store.knowledgeBases.filter(k=>k.status==='failed');
  const orgName=(id:string)=>store.organizations.find(o=>o.id===id)?.name||id;
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Health" description="Raw provider failures, with the reference code the company was shown."/>
    <div className="grid cols-4">
      <div className="card"><div className="stat-label">{t('Channels in error')}</div><div className="stat-number mono danger">{failedChannels.length}</div></div>
      <div className="card"><div className="stat-label">{t('Blocked setups')}</div><div className="stat-number mono danger">{blocked.length}</div></div>
      <div className="card"><div className="stat-label">{t('Failed calls')}</div><div className="stat-number mono warning">{store.calls.filter(c=>c.status==='failed').length}</div></div>
      <div className="card"><div className="stat-label">{t('Indexing failures')}</div><div className="stat-number mono">{stuckIndex.length}</div></div>
    </div>
    <div className="card stack">
      <h2>{t('Channels in error')}</h2>
      {failedChannels.length===0?<Empty icon={CheckCircle2} title="All channels healthy" body="Nothing is failing right now."/>:
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('Company')}</th><th>{t('Channel')}</th><th>{t('Code shown')}</th><th>{t('Raw provider error')}</th></tr></thead>
        <tbody>{failedChannels.map(c=><tr key={c.id}>
          <td>{orgName(c.orgId)}</td><td>{c.label} · {t(CHANNEL_LABEL[c.type])}</td>
          <td className="mono small">{c.errorCode||'—'}</td>
          <td className="mono small danger">{c.errorDetail}</td>
        </tr>)}</tbody>
      </table></div>}
    </div>
    <div className="card stack">
      <h2>{t('Recent failed calls')}</h2>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('When')}</th><th>{t('Company')}</th><th>{t('Number')}</th><th>{t('Code shown')}</th><th>{t('Raw provider error')}</th></tr></thead>
        <tbody>{failedCalls.map(c=><tr key={c.id}>
          <td className="small">{formatDate(c.startedAt)}</td><td>{orgName(c.orgId)}</td>
          <td className="mono small">{c.phoneNumber}</td><td className="mono small">{c.errorCode||'—'}</td>
          <td className="mono small danger">{c.error||'—'}</td>
        </tr>)}</tbody>
      </table></div>
    </div>
    {blocked.length>0&&<div className="card stack">
      <h2>{t('Blocked setups')}</h2>
      {blocked.map(b=><div className="row between" key={b.id}>
        <span><AlertTriangle size={14}/> {orgName(b.orgId)} · {store.channels.find(c=>c.id===b.channelId)?.label||t(CHANNEL_LABEL[b.kind])}</span>
        <span className="small muted">{b.note}</span>
      </div>)}
    </div>}
  </div>;
}

/** The scored search console, moved off the company side. */
export function PlatformSearch(){
  const {store,t}=useApp();
  const [kbId,setKbId]=useState(store.knowledgeBases[0]?.id||'');
  const [query,setQuery]=useState('');
  const [hits,setHits]=useState<Awaited<ReturnType<typeof api.searchKnowledge>>>([]);
  const [busy,setBusy]=useState(false);
  const kb=store.knowledgeBases.find(x=>x.id===kbId);
  const run=async()=>{if(!query.trim())return;setBusy(true);setHits(await api.searchKnowledge(kbId,query));setBusy(false)};
  const threshold=kb?.similarityThreshold??0.6;
  const wouldAnswer=hits.some(h=>h.score>=threshold);
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Search console" description="Scored retrieval with the threshold line. Companies see only the plain version of this."/>
    <div className="card stack">
      <div className="field-grid">
        <div className="field"><label>{t('Knowledge base')}</label>
          <select className="select" value={kbId} onChange={e=>setKbId(e.target.value)}>
            {store.knowledgeBases.map(k=><option key={k.id} value={k.id}>{store.organizations.find(o=>o.id===k.orgId)?.name} — {k.name}</option>)}
          </select></div>
        <div className="field"><label>{t('Threshold')}</label><input className="input mono" readOnly value={threshold}/></div>
      </div>
      <div className="row"><input className="input" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')run()}} placeholder={t('Type a question')}/><Button onClick={run} disabled={busy}>{busy?'Searching…':'Search'}</Button></div>
      {hits.length>0&&<div className="notice">{wouldAnswer?t('The agent would answer this.'):t('The agent would decline and offer to find out.')}</div>}
      <div className="stack">{hits.map(h=>{
        const above=h.score>=threshold;
        return <div className={'result-card '+(above?'':'discarded')} key={h.id}>
          <div className="row between"><strong>{h.section}</strong><span className="mono">{h.score.toFixed(3)}</span></div>
          <div className="small muted">{h.title} · {t('page')} {h.page}</div>
          <p className="small">{h.preview}</p>
        </div>;
      })}</div>
      {hits.length>0&&<div className="threshold-line"><span>{t('threshold')} {threshold}</span></div>}
    </div>
  </div>;
}

export function PlatformAuditPage(){
  const {store,t}=useApp();
  const [query,setQuery]=useState('');
  const rows=useMemo(()=>store.platformAudit.filter(x=>!query||(x.action+x.detail+(x.actorEmail||'')).toLowerCase().includes(query.toLowerCase())),[store.platformAudit,query]);
  return <div className="stack">
    <PageHead eyebrow="Platform" title="Platform audit" description="Every action taken from this portal."/>
    <div className="card"><input className="input" value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('Search the audit log')}/></div>
    <div className="card table-wrap"><table className="table">
      <thead><tr><th>{t('When')}</th><th>{t('Who')}</th><th>{t('Action')}</th><th>{t('Company')}</th><th>{t('Detail')}</th></tr></thead>
      <tbody>{rows.map(r=><tr key={r.id}>
        <td className="small">{formatDate(r.createdAt)}</td>
        <td className="small mono">{r.actorEmail}</td>
        <td><strong>{r.action}</strong></td>
        <td className="small">{r.orgId?store.organizations.find(o=>o.id===r.orgId)?.name||r.orgId:'—'}</td>
        <td className="small muted">{r.detail}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
