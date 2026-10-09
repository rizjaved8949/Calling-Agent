import React,{useEffect,useMemo,useState} from 'react';
import {BrowserRouter,Link,NavLink,Navigate,Route,Routes,useLocation,useNavigate} from 'react-router-dom';
import {Activity,BookOpen,BrainCircuit,Building2,ChartNoAxesCombined,ChevronDown,Command,Headphones,History,LayoutDashboard,LifeBuoy,ListChecks,Menu,MessageSquare,Mic2,Moon,PhoneOutgoing,Radio,Route as RouteIcon,ServerCog,Settings,ShieldCheck,Sun,Users,X,Search,LogOut,HelpCircle} from 'lucide-react';
import {api,subscribe,subscribePending} from './lib/api';
import type {Store,Session,Organization,Role} from './lib/types';

const loadWorkspace=()=>import('./pages');
const lazyPage=(pick:(m:Awaited<ReturnType<typeof loadWorkspace>>)=>React.ComponentType<Record<string,never>>)=>React.lazy(()=>loadWorkspace().then(m=>({default:pick(m)})));
const AuthScreen=React.lazy(()=>loadWorkspace().then(m=>({default:m.AuthScreen as React.ComponentType<{mode:string}>})));

const Agents=lazyPage(m=>m.Agents);




const Live=lazyPage(m=>m.Live);
const HistoryPage=lazyPage(m=>m.HistoryPage);
const Messages=lazyPage(m=>m.Messages);
const Guides=lazyPage(m=>m.Guides);
const GuideDetail=lazyPage(m=>m.GuideDetail);
const Team=lazyPage(m=>m.Team);
const Usage=lazyPage(m=>m.Usage);


const loadCompany=()=>import('./pages-company');
const lazyCompany=(pick:(m:Awaited<ReturnType<typeof loadCompany>>)=>React.ComponentType<Record<string,never>>)=>React.lazy(()=>loadCompany().then(m=>({default:pick(m)})));
const Dashboard=lazyCompany(m=>m.Dashboard);
const Knowledge=lazyCompany(m=>m.Knowledge);
const AgentDetail=lazyCompany(m=>m.AgentDetail);
const Channels=lazyCompany(m=>m.Channels);
const ChannelDetail=lazyCompany(m=>m.ChannelDetail);
const SettingsPage=lazyCompany(m=>m.SettingsPage);

const loadSetup=()=>import('./pages-onboarding');
const lazySetup=(pick:(m:Awaited<ReturnType<typeof loadSetup>>)=>React.ComponentType<Record<string,never>>)=>React.lazy(()=>loadSetup().then(m=>({default:pick(m)})));
const Onboarding=lazySetup(m=>m.Onboarding);
const CallDetail=lazySetup(m=>m.CallDetail);

const loadExtra=()=>import('./pages-extra');
const lazyExtra=(pick:(m:Awaited<ReturnType<typeof loadExtra>>)=>React.ComponentType<Record<string,never>>)=>React.lazy(()=>loadExtra().then(m=>({default:pick(m)})));
const KnowledgeDetail=lazyExtra(m=>m.KnowledgeDetail);
const CallSetups=React.lazy(()=>import('./call-setups').then(m=>({default:m.CallSetups})));
const Campaigns=lazyExtra(m=>m.Campaigns);
const CampaignDetail=lazyExtra(m=>m.CampaignDetail);
const Unanswered=lazyExtra(m=>m.Unanswered);
const StaffQueue=lazyExtra(m=>m.StaffQueue);
const StaffLookup=lazyExtra(m=>m.StaffLookup);
const StaffAsk=lazyExtra(m=>m.StaffAsk);

const loadPlatform=()=>import('./pages-platform');
const lazyPlatform=(pick:(m:Awaited<ReturnType<typeof loadPlatform>>)=>React.ComponentType<Record<string,never>>)=>React.lazy(()=>loadPlatform().then(m=>({default:pick(m)})));
const PlatformCompanies=lazyPlatform(m=>m.PlatformCompanies);
const PlatformCompanyDetail=lazyPlatform(m=>m.PlatformCompanyDetail);
const PlatformProvisioning=lazyPlatform(m=>m.PlatformProvisioning);
const PlatformTechnical=lazyPlatform(m=>m.PlatformTechnical);
const PlatformPresets=lazyPlatform(m=>m.PlatformPresets);
const PlatformHealth=lazyPlatform(m=>m.PlatformHealth);
const PlatformSearch=lazyPlatform(m=>m.PlatformSearch);
const PlatformAuditPage=lazyPlatform(m=>m.PlatformAuditPage);

import {HomePage,ProductPage,SolutionsPage,PricingPage,AboutPage,ResourcesPage,ContactPage} from './marketing';
import {TermsPage,PrivacyPage} from './legal';
import {HistoryNavigationProvider} from './history-navigation';

import {AppCtx,useApp} from './app-context';
import {ComingSoon,hasBackendKey} from './connect-backend';
import {AgentsScreen,AgentDetailScreen,KnowledgeBasesScreen,KnowledgeBaseDetailScreen,CallSetupsScreen,TeamScreen,InviteAcceptScreen,StaffQueueScreen,StaffLookupScreen,StaffAskScreen} from './pages-workspace';
import {OperatorCompanies,PlatformLoginScreen} from './pages-operator';
import {LiveCalls} from './pages-live';
import {CampaignsScreen,GapsScreen,UsageScreen} from './pages-growth';
import {LIVE,hydrate} from './lib/api';

function Provider({children}:{children:React.ReactNode}){
  const [store,setStore]=useState<Store>(()=>api.snapshot());
  const [sessionState,setSessionState]=useState<Session|null>(()=>{
    // A shareable demo link: /app/dashboard?demo=admin opens a portal without
    // signing in, so the product can be shown to someone without an account.
    // Resolved here rather than in an effect, because the route guard reads the
    // session on the very first render.
    const seats:Record<string,Session>={
      admin:{email:'samira@northstar.edu',name:'Samira Khan',userId:'user-1',orgId:'org-northstar',portal:'company'},
      staff:{email:'omar@northstar.edu',name:'Omar Farooq',userId:'user-2',orgId:'org-northstar',portal:'company'},
      platform:{email:'ops@platform.internal',name:'Platform Operations',userId:'user-platform',orgId:'org-northstar',portal:'platform',platformRole:'superadmin'},
    };
    const want=new URLSearchParams(window.location.search).get('demo');
    const seat=want?seats[want]:undefined;
    if(seat){
      if(want==='platform')api.setProfile('user-platform');
      localStorage.setItem('ca-session',JSON.stringify(seat));
      return seat;
    }
    try{return JSON.parse(localStorage.getItem('ca-session')||'null')}catch{return null}
  });
  const [theme,setThemeState]=useState<'light'|'dark'>(()=>{
    // ?theme=dark pairs with the demo link, so a shared URL opens as intended.
    const want=new URLSearchParams(window.location.search).get('theme');
    if(want==='dark'||want==='light'){localStorage.setItem('ca-theme',want);return want}
    return localStorage.getItem('ca-theme')==='dark'?'dark':'light';
  });
  const [pending,setPending]=useState(0);
  const [connection,setConnection]=useState<'live'|'reconnecting'|'offline'>('live');
  const [toasts,setToasts]=useState<{id:number;text:string}[]>([]);
  useEffect(()=>subscribe(()=>setStore(api.snapshot())),[]);
  useEffect(()=>subscribePending(setPending),[]);
  // Against a real backend the fixtures are only a starting shape: the first
  // load replaces the company, its channels, calls and messages with what the
  // API actually holds. Failures are left to the screens, which each show
  // their own error — a blank app with one global message is worse.
  const [hydrated,setHydrated]=useState(!LIVE);
  useEffect(()=>{
    // No company key — a platform/admin session, or nobody signed in yet —
    // means there is nothing company-scoped to wait for, so "hydrated" is
    // immediately true rather than stuck false forever. It used to read
    // `!LIVE` here, which left PlatformOnly's "Loading…" gate permanently
    // stuck for an admin-key session, since that session never acquires a
    // company key to hydrate against.
    if(!LIVE||!hasBackendKey()){setHydrated(true);return}
    let cancelled=false;
    hydrate().finally(()=>{if(!cancelled)setHydrated(true)});
    return()=>{cancelled=true};
  },[]);
  // While impersonating, the workspace shown is the impersonated company's.
  const activeOrgId=sessionState?.impersonating?.orgId||sessionState?.orgId;
  const org=store.organizations.find(x=>x.id===activeOrgId)||store.organizations[0];
  const isPlatform=sessionState?.portal==='platform';
  const role:Role=useMemo(()=>{
    if(isPlatform)return 'owner';
    // The role sign-in returned (see lib/api/auth.ts's AuthSession and
    // pages.tsx's `finish`). The backend enforces owner-only actions itself
    // (see api/routes/team.py) — this is what hides the buttons for them.
    if(LIVE)return sessionState?.role||'staff';
    return store.memberships.find(m=>m.orgId===org.id&&m.userId===sessionState?.userId)?.role||'staff';
  },[store.memberships,org.id,sessionState?.userId,isPlatform,sessionState?.role]);
  const readOnly=!!sessionState?.impersonating;
  const canManage=!readOnly&&(role==='owner'||role==='admin');
  useEffect(()=>{
    // The interface copy is English, so the document stays left to right.
    // org.defaultLanguage is the language the agent SPEAKS on a call, which is
    // a different thing; Urdu strings carry their own font and direction.
    document.documentElement.lang='en';
    document.documentElement.dir='ltr';
    document.documentElement.setAttribute('data-theme',theme);
    document.documentElement.style.setProperty('--accent',org.accentColor);
  },[theme,org.accentColor]);
  const setSession=(s:Session|null)=>{setSessionState(s);if(s)localStorage.setItem('ca-session',JSON.stringify(s));else localStorage.removeItem('ca-session')};
  const setOrg=(id:string)=>setSession(sessionState?{...sessionState,orgId:id}:null);
  const setTheme=(v:'light'|'dark')=>{setThemeState(v);localStorage.setItem('ca-theme',v)};
  const t=(text:string)=>text;
  const toast=(text:string)=>{const id=Date.now()+Math.random();setToasts(old=>[...old,{id,text}]);setTimeout(()=>setToasts(old=>old.filter(x=>x.id!==id)),4200)};
  const run=async<T,>(action:()=>Promise<T>,success?:string):Promise<T|undefined>=>{
    if(readOnly){toast('This is a read-only support session. Nothing was changed.');return undefined}
    try{const result=await action();if(success)toast(t(success));return result}catch(e){toast(e instanceof Error?e.message:'Something did not work.');return undefined}
  };
  const readiness=useMemo(()=>{
    const bases=store.knowledgeBases.filter(x=>x.orgId===org.id);
    const channels=store.channels.filter(x=>x.orgId===org.id);
    const agents=store.agents.filter(x=>x.orgId===org.id);
    const wa=channels.find(x=>x.type==='whatsapp_call');
    return {
      knowledgeBaseReady:bases.some(x=>x.status==='ready'&&!!x.chunkCount),
      phoneChannelReady:channels.some(x=>x.type==='sim'&&x.status==='connected'),
      whatsappCallingReady:wa?.status==='connected',
      whatsappMessagingReady:channels.some(x=>x.type==='whatsapp_message'&&x.status==='connected')&&store.templates.some(x=>x.orgId===org.id&&x.status==='approved'),
      operatorCallingReady:wa?.status==='connected'&&!!wa.config.operatorCallingEnabled,
      personaReady:agents.some(x=>{const p=store.personas.find(p=>p.id===x.personaId);return !!p?.greeting&&!!p.roleDescription}),
      agentLive:agents.some(x=>x.status==='live'),
      routingReady:store.callSetups.some(x=>x.orgId===org.id&&x.enabled),
    };
  },[store,org.id]);
  return <AppCtx.Provider value={{store,session:sessionState,setSession,org,setOrg,theme,setTheme,t,toast,run,readiness,connection,setConnection,role,isPlatform,canManage,readOnly,hydrated}}>{pending>0&&<div className="progress-line"/>}{children}<div className="toast-wrap" aria-live="polite">{toasts.map(x=><div className="toast" key={x.id}>{x.text}<button aria-label="Dismiss" onClick={()=>setToasts(old=>old.filter(y=>y.id!==x.id))}><X size={16}/></button></div>)}</div></AppCtx.Provider>;
}

export function Button({children,onClick,to,variant='',small=false,disabled=false,title,type='button'}:{children:React.ReactNode;onClick?:()=>void;to?:string;variant?:string;small?:boolean;disabled?:boolean;title?:string;type?:'button'|'submit'}){
  const cls='button '+variant+(small?' small':'');
  return to?<Link className={cls} to={to} title={title}>{children}</Link>:<button className={cls} onClick={onClick} disabled={disabled} title={title} type={type}>{children}</button>;
}
export function Badge({children,tone=''}:{children:React.ReactNode;tone?:string}){return <span className={'badge '+tone}><span className="dot"/>{children}</span>}
export function PageHead({eyebrow,title,description,action}:{eyebrow?:string;title:string;description?:string;action?:React.ReactNode}){const {t}=useApp();return <div className="page-heading"><div>{eyebrow&&<div className="eyebrow">{t(eyebrow)}</div>}<h1>{t(title)}</h1>{description&&<p>{t(description)}</p>}</div>{action}</div>}
export function Field({label,value,onChange,help,type='text',placeholder,rows=0,required=false,disabled=false}:{label:string;value:string|number;onChange:(v:string)=>void;help?:string;type?:string;placeholder?:string;rows?:number;required?:boolean;disabled?:boolean}){
  const {t}=useApp();
  const [draft,setDraft]=useState(String(value));
  const focused=React.useRef(false);
  const timer=React.useRef<ReturnType<typeof setTimeout>|null>(null);
  const latest=React.useRef(onChange);
  latest.current=onChange;
  useEffect(()=>{if(!focused.current)setDraft(String(value))},[value]);
  useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current)},[]);
  const commit=(next:string)=>{if(timer.current)clearTimeout(timer.current);timer.current=setTimeout(()=>latest.current(next),280)};
  const blur=()=>{focused.current=false;if(timer.current)clearTimeout(timer.current);latest.current(draft)};
  return <div className="field"><label>{t(label)}{required?' *':''}</label>{rows?<textarea className="textarea" value={draft} disabled={disabled} onFocus={()=>{focused.current=true}} onBlur={blur} onChange={e=>{setDraft(e.target.value);commit(e.target.value)}} rows={rows} placeholder={placeholder?t(placeholder):undefined}/>:<input className="input" type={type} value={draft} disabled={disabled} onFocus={()=>{focused.current=true}} onBlur={blur} onChange={e=>{setDraft(e.target.value);commit(e.target.value)}} placeholder={placeholder?t(placeholder):undefined} required={required}/>}{help&&<div className="help">{t(help)}</div>}</div>
}
export function Empty({icon:Icon=Search,title,body,action}:{icon?:React.ComponentType<{size?:number}>;title:string;body:string;action?:React.ReactNode}){const {t}=useApp();return <div className="empty"><Icon size={32}/><h3>{t(title)}</h3><p>{t(body)}</p>{action}</div>}
export function Tabs({items,active,onChange}:{items:string[];active:string;onChange:(s:string)=>void}){const {t}=useApp();return <div className="tabs" role="tablist">{items.map(item=><button role="tab" aria-selected={active===item} className={'tab '+(active===item?'active':'')} onClick={()=>onChange(item)} key={item}>{t(item)}</button>)}</div>}
/** A choice the company can make without ever seeing what is behind it. */
export function PresetPicker({options,value,onChange,disabled}:{options:{id:string;label:string;description:string;consequence:string}[];value:string;onChange:(id:string)=>void;disabled?:boolean}){
  return <div className="preset-grid">{options.map(opt=>
    <button key={opt.id} type="button" disabled={disabled} aria-pressed={value===opt.id} className={'preset-card '+(value===opt.id?'selected':'')} onClick={()=>onChange(opt.id)}>
      <strong>{opt.label}</strong>
      <span>{opt.description}</span>
      <em>{opt.consequence}</em>
    </button>)}</div>;
}


type NavGroup=[string,[React.ComponentType<{size?:number}>,string,string][]][];
const ADMIN_NAV:NavGroup=[
  ['Workspace',[[LayoutDashboard,'Dashboard','/app/dashboard'],[Mic2,'Agents','/app/agents'],[BrainCircuit,'Knowledge','/app/knowledge'],[RouteIcon,'Call setups','/app/setups'],[Radio,'Numbers','/app/channels']]],
  ['Operations',[[Activity,'Live','/app/live'],[PhoneOutgoing,'Campaigns','/app/campaigns'],[History,'History','/app/history'],[MessageSquare,'Messages','/app/messages'],[ListChecks,'Unanswered','/app/unanswered']]],
  ['Manage',[[BookOpen,'Guides','/app/guides'],[Users,'Team','/app/team'],[ChartNoAxesCombined,'Usage','/app/usage'],[Settings,'Settings','/app/settings']]],
];
const STAFF_NAV:NavGroup=[
  ['My work',[[Headphones,'My queue','/app/queue'],[Activity,'Live','/app/live'],[RouteIcon,'Call setups','/app/setups'],[History,'My calls','/app/history']]],
  ['Help me',[[Search,'Customer lookup','/app/lookup'],[HelpCircle,'Ask the documents','/app/ask'],[BookOpen,'Guides','/app/guides']]],
];
const PLATFORM_NAV:NavGroup=[
  ['Customers',[[Building2,'Companies','/platform/companies'],[ServerCog,'Setup queue','/platform/provisioning'],[LifeBuoy,'Health','/platform/health']]],
  ['Controls',[[ShieldCheck,'Technical','/platform/technical'],[Settings,'Presets','/platform/presets'],[BrainCircuit,'Search console','/platform/search']]],
  ['Record',[[History,'Platform audit','/platform/audit']]],
];

function Shell({children,platform=false}:{children:React.ReactNode;platform?:boolean}){
  const {session,org,setOrg,store,t,theme,setTheme,setSession,connection,role,readOnly}=useApp();
  const [open,setOpen]=useState(false),[collapsed,setCollapsed]=useState(false),[palette,setPalette]=useState(false),[query,setQuery]=useState('');
  const location=useLocation(),navigate=useNavigate();
  useEffect(()=>{const handle=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setPalette(x=>!x)}if(e.key==='Escape')setPalette(false)};window.addEventListener('keydown',handle);return()=>window.removeEventListener('keydown',handle)},[]);
  const nav=platform?PLATFORM_NAV:role==='staff'?STAFF_NAV:ADMIN_NAV;
  const current=nav.flatMap(x=>x[1]).filter(x=>location.pathname.startsWith(x[2])).sort((a,b)=>b[2].length-a[2].length)[0]?.[1]||(platform?'Platform':'Overview');
  const home=platform?'/platform/companies':role==='staff'?'/app/queue':'/app/dashboard';
  const endImpersonation=()=>{if(session?.impersonating){api.endImpersonation(session.impersonating.orgId);setSession({...session,impersonating:undefined});navigate('/platform/companies')}};
  return <div className={'app'+(platform?' platform':'')}>
    <div className={'mobile-backdrop '+(open?'show':'')} onClick={()=>setOpen(false)}/>
    <aside className={'sidebar '+(open?'open':'')} style={collapsed?{width:76}:undefined}>
      <Link to={home} className="brand"><span className="brand-mark"><Activity size={21}/></span>{!collapsed&&(platform?'platform':'calling agent')}</Link>
      {!collapsed&&!platform&&role!=='staff'&&<select className="workspace" aria-label={t('Workspace')} value={org.id} onChange={e=>setOrg(e.target.value)} disabled={readOnly}>{store.organizations.filter(x=>store.memberships.some(m=>m.orgId===x.id&&m.userId===session?.userId)).map(x=><option value={x.id} key={x.id}>{x.name}</option>)}</select>}
      {!collapsed&&!platform&&role==='staff'&&<div className="workspace-label">{org.name}</div>}
      <nav>{nav.map(([group,links])=><div key={group}><div className="nav-group">{!collapsed&&t(group)}</div>{links.map(([Icon,label,path])=><NavLink className={({isActive})=>'nav-link '+(isActive?'active':'')} to={path} key={path} title={t(label)} onClick={()=>setOpen(false)}><Icon size={18}/>{!collapsed&&t(label)}</NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><button className="ghost-btn" onClick={()=>setCollapsed(x=>!x)} title={t('Collapse sidebar')}><ChevronDown size={16} style={{transform:'rotate(90deg)'}}/>{!collapsed&&t('Collapse sidebar')}</button></div>
    </aside>
    <div className="main">
      {platform&&<div className="portal-banner platform-banner"><ShieldCheck size={15}/>{t('Platform portal — internal. Everything here is hidden from customers.')}</div>}
      {readOnly&&<div className="portal-banner impersonation-banner"><LifeBuoy size={15}/>{t('Read-only support session for')} <strong>{org.name}</strong><button className="link-btn" onClick={endImpersonation}>{t('Exit')}</button></div>}
      <header className="topbar">
        <div className="row"><button className="icon-btn mobile-menu" onClick={()=>{setCollapsed(false);setOpen(true)}} aria-label={t('Open menu')}><Menu size={21}/></button><span className="small muted">{platform?t('Platform'):t('Workspace')} /</span><strong>{t(current)}</strong></div>
        <div className="top-actions">
          <span aria-live="polite"><Badge tone={connection==='offline'?'danger':connection==='reconnecting'?'warning':'live'}>{t(connection)}</Badge></span>
          <button className="icon-btn" onClick={()=>setPalette(true)} title={t('Command palette')}><Command size={18}/></button>
          <button className="icon-btn" onClick={()=>setTheme(theme==='light'?'dark':'light')} title={t(theme==='light'?'Dark mode':'Light mode')}>{theme==='light'?<Moon size={18}/>:<Sun size={18}/>}</button>
          <button className="icon-btn" onClick={()=>{setSession(null);navigate('/login')}} title={t('Sign out')}><LogOut size={18}/></button>
        </div>
      </header>
      <main className="content">{children}</main>
    </div>
    {palette&&<div className="modal-backdrop" onClick={()=>setPalette(false)}><div className="modal" onClick={e=>e.stopPropagation()}><div className="row between"><h2>{t('Go to')}</h2><button className="icon-btn" onClick={()=>setPalette(false)}><X size={18}/></button></div><input autoFocus className="input" value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('Search pages and actions')}/><div className="command-list">{nav.flatMap(x=>x[1]).filter(x=>x[1].toLowerCase().includes(query.toLowerCase())).map(([Icon,label,path])=><button key={path} className="nav-link" style={{width:'100%',border:0,background:'transparent'}} onClick={()=>{navigate(path);setPalette(false);setQuery('')}}><Icon size={18}/>{t(label)}</button>)}</div></div></div>}
  </div>;
}

function NotFound(){return <div className="route-loading"><h2>Page not found</h2><p className="muted">That page does not exist. <Link to="/">Go back</Link>.</p></div>}

/**
 * Signing in is what produces the company API key now, so a workspace route
 * with no key means nobody has signed in — send them to do that.
 *
 * This used to render `ApiKeyGate`, which asked for the key to be pasted in
 * by hand. That was the stand-in for sign-in not existing; leaving it in
 * front of `Protected` meant a real visitor met a box asking for a key only
 * the backend had ever seen, and never reached the sign-in screen at all.
 */
function Protected({children,manage=false}:{children:React.ReactNode;manage?:boolean}){
  const {session,role,hydrated}=useApp();
  if(!session||(LIVE&&!hasBackendKey()))return <Navigate to="/login" replace/>;
  if(!hydrated)return <div className="route-loading">Loading your workspace…</div>;
  if(session.portal==='platform'&&!session.impersonating)return <Navigate to="/platform/companies" replace/>;
  if(manage&&role==='staff')return <Navigate to="/app/queue" replace/>;
  return <Shell>{children}</Shell>;
}
/**
 * The platform portal. A company account is given a plain not-found rather than a
 * permission error, so the portal's existence is never advertised.
 */
function PlatformOnly({children}:{children:React.ReactNode}){
  const {session,hydrated}=useApp();
  if(!session)return <Navigate to="/login" replace/>;
  if(!hydrated)return <div className="route-loading">Loading…</div>;
  if(session.portal!=='platform')return <NotFound/>;
  return <Shell platform>{children}</Shell>;
}

/**
 * A screen whose data the backend does not serve yet.
 *
 * In a fixture build it renders as before, because there the fixtures are the
 * honest answer. Against a real backend it says so instead of dressing sample
 * records up as the company's own.
 */
function NotYet({title,detail,children}:{title:string;detail?:string;children:React.ReactNode}){
  return LIVE?<ComingSoon title={title} detail={detail}/>:<>{children}</>;
}

/**
 * A screen with two implementations: the fixture one the design was built
 * against, and the one that talks to the API. Which you get depends on whether
 * there is an API to talk to.
 */
function WhenLive({real,children}:{real:React.ReactNode;children:React.ReactNode}){
  return LIVE?<>{real}</>:<>{children}</>;
}

function AppRoutes(){
  return <Routes>
    <Route path="/" element={<HomePage/>}/>
    <Route path="/product" element={<ProductPage/>}/>
    <Route path="/solutions" element={<SolutionsPage/>}/>
    <Route path="/pricing" element={<PricingPage/>}/>
    <Route path="/about" element={<AboutPage/>}/>
    <Route path="/resources" element={<ResourcesPage/>}/>
    <Route path="/contact" element={<ContactPage/>}/>
    {/* Reachable without an account, because somebody deciding whether to
        sign up is exactly who needs to read them. */}
    <Route path="/terms" element={<TermsPage/>}/>
    <Route path="/privacy" element={<PrivacyPage/>}/>
    <Route path="/login" element={<AuthScreen mode="login"/>}/>
    <Route path="/signup" element={<AuthScreen mode="signup"/>}/>
    <Route path="/forgot-password" element={<AuthScreen mode="forgot"/>}/>
    <Route path="/verify" element={<AuthScreen mode="verify"/>}/>
    <Route path="/invite" element={LIVE?<InviteAcceptScreen/>:<AuthScreen mode="invite"/>}/>
    {/* Not linked from any nav or the login screen on purpose — see
        PlatformLoginScreen's own docstring for what actually keeps a
        stranger out (the admin key itself, not this URL being unlisted). */}
    <Route path="/ops-login" element={<PlatformLoginScreen/>}/>

    <Route path="/app/dashboard" element={<Protected manage><Dashboard/></Protected>}/>
    <Route path="/app/onboarding" element={<Protected manage><Onboarding/></Protected>}/>
    <Route path="/app/agents" element={<Protected manage><WhenLive real={<AgentsScreen/>}><Agents/></WhenLive></Protected>}/>
    <Route path="/app/agents/:id" element={<Protected manage><WhenLive real={<AgentDetailScreen/>}><AgentDetail/></WhenLive></Protected>}/>
    <Route path="/app/knowledge" element={<Protected manage><WhenLive real={<KnowledgeBasesScreen/>}><Knowledge/></WhenLive></Protected>}/>
    <Route path="/app/knowledge/:kbId" element={<Protected manage><WhenLive real={<KnowledgeBaseDetailScreen/>}><KnowledgeDetail/></WhenLive></Protected>}/>
    <Route path="/app/setups" element={<Protected><WhenLive real={<CallSetupsScreen/>}><CallSetups/></WhenLive></Protected>}/>
    <Route path="/app/campaigns" element={<Protected manage><WhenLive real={<CampaignsScreen/>}><Campaigns/></WhenLive></Protected>}/>
    <Route path="/app/campaigns/:id" element={<Protected manage><WhenLive real={<CampaignsScreen/>}><CampaignDetail/></WhenLive></Protected>}/>
    <Route path="/app/unanswered" element={<Protected manage><WhenLive real={<GapsScreen/>}><Unanswered/></WhenLive></Protected>}/>
    <Route path="/app/channels" element={<Protected manage><Channels/></Protected>}/>
    <Route path="/app/channels/:id" element={<Protected manage><ChannelDetail/></Protected>}/>
    <Route path="/app/messages" element={<Protected manage><Messages/></Protected>}/>
    <Route path="/app/team" element={<Protected manage><WhenLive real={<TeamScreen/>}><Team/></WhenLive></Protected>}/>
    <Route path="/app/usage" element={<Protected manage><WhenLive real={<UsageScreen/>}><Usage/></WhenLive></Protected>}/>
    <Route path="/app/settings" element={<Protected manage><SettingsPage/></Protected>}/>
    <Route path="/app/live" element={<Protected><WhenLive real={<LiveCalls/>}><Live/></WhenLive></Protected>}/>
    <Route path="/app/history" element={<Protected><HistoryPage/></Protected>}/>
    <Route path="/app/history/:id" element={<Protected><CallDetail/></Protected>}/>
    <Route path="/app/guides" element={<Protected><Guides/></Protected>}/>
    <Route path="/app/guides/:slug" element={<Protected><GuideDetail/></Protected>}/>
    <Route path="/app/queue" element={<Protected><WhenLive real={<StaffQueueScreen/>}><StaffQueue/></WhenLive></Protected>}/>
    <Route path="/app/lookup" element={<Protected><WhenLive real={<StaffLookupScreen/>}><StaffLookup/></WhenLive></Protected>}/>
    <Route path="/app/ask" element={<Protected><WhenLive real={<StaffAskScreen/>}><StaffAsk/></WhenLive></Protected>}/>

    <Route path="/platform/companies" element={<PlatformOnly><WhenLive real={<OperatorCompanies/>}><PlatformCompanies/></WhenLive></PlatformOnly>}/>
    <Route path="/platform/companies/:id" element={<PlatformOnly><PlatformCompanyDetail/></PlatformOnly>}/>
    <Route path="/platform/provisioning" element={<PlatformOnly><NotYet title="Provisioning" detail="Number provisioning is not connected yet."><PlatformProvisioning/></NotYet></PlatformOnly>}/>
    <Route path="/platform/technical" element={<PlatformOnly><NotYet title="Technical settings" detail="Platform-wide tuning arrives with the voice engine."><PlatformTechnical/></NotYet></PlatformOnly>}/>
    <Route path="/platform/presets" element={<PlatformOnly><NotYet title="Presets" detail="Shared agent presets arrive with the voice engine."><PlatformPresets/></NotYet></PlatformOnly>}/>
    <Route path="/platform/health" element={<PlatformOnly><NotYet title="Platform health"><PlatformHealth/></NotYet></PlatformOnly>}/>
    <Route path="/platform/search" element={<PlatformOnly><NotYet title="Search"><PlatformSearch/></NotYet></PlatformOnly>}/>
    <Route path="/platform/audit" element={<PlatformOnly><NotYet title="Audit log" detail="The audit trail arrives with sign-in, so every entry can name a person."><PlatformAuditPage/></NotYet></PlatformOnly>}/>

    {/* Routes from the single-workspace layout, kept so old links still work. */}
    <Route path="/dashboard" element={<Navigate to="/app/dashboard" replace/>}/>
    <Route path="/agents" element={<Navigate to="/app/agents" replace/>}/>
    <Route path="/knowledge" element={<Navigate to="/app/knowledge" replace/>}/>
    <Route path="/channels" element={<Navigate to="/app/channels" replace/>}/>
    <Route path="/live" element={<Navigate to="/app/live" replace/>}/>
    <Route path="/history" element={<Navigate to="/app/history" replace/>}/>
    <Route path="/messages" element={<Navigate to="/app/messages" replace/>}/>
    <Route path="/guides" element={<Navigate to="/app/guides" replace/>}/>
    <Route path="/team" element={<Navigate to="/app/team" replace/>}/>
    <Route path="/usage" element={<Navigate to="/app/usage" replace/>}/>
    <Route path="/settings" element={<Navigate to="/app/settings" replace/>}/>
    <Route path="/onboarding" element={<Navigate to="/app/onboarding" replace/>}/>
    <Route path="*" element={<NotFound/>}/>
  </Routes>;
}
export default function App(){return <BrowserRouter><Provider><HistoryNavigationProvider><React.Suspense fallback={<div className="route-loading">Loading…</div>}><AppRoutes/></React.Suspense></HistoryNavigationProvider></Provider></BrowserRouter>}
