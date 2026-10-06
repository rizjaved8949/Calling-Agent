import React,{createContext,useContext,useEffect,useMemo,useState} from 'react';
import {BrowserRouter,Link,NavLink,Navigate,Route,Routes,useLocation,useNavigate} from 'react-router-dom';
import {Activity,BookOpen,BrainCircuit,ChartNoAxesCombined,ChevronDown,Command,Headphones,History,LayoutDashboard,Menu,MessageSquare,Mic2,Moon,Radio,Settings,Sun,Users,Wallet,X,Check,Search,LogOut} from 'lucide-react';
import {api,subscribe,subscribePending} from './lib/api';
import type {Store,Session,Organization} from './lib/types';
const loadWorkspace=()=>import('./pages');
const AuthScreen=React.lazy(()=>loadWorkspace().then(m=>({default:m.AuthScreen})));
const Dashboard=React.lazy(()=>loadWorkspace().then(m=>({default:m.Dashboard})));
const Onboarding=React.lazy(()=>loadWorkspace().then(m=>({default:m.Onboarding})));
const Agents=React.lazy(()=>loadWorkspace().then(m=>({default:m.Agents})));
const AgentDetail=React.lazy(()=>loadWorkspace().then(m=>({default:m.AgentDetail})));
const Knowledge=React.lazy(()=>loadWorkspace().then(m=>({default:m.Knowledge})));
const Channels=React.lazy(()=>loadWorkspace().then(m=>({default:m.Channels})));
const ChannelDetail=React.lazy(()=>loadWorkspace().then(m=>({default:m.ChannelDetail})));
const Live=React.lazy(()=>loadWorkspace().then(m=>({default:m.Live})));
const HistoryPage=React.lazy(()=>loadWorkspace().then(m=>({default:m.HistoryPage})));
const CallDetail=React.lazy(()=>loadWorkspace().then(m=>({default:m.CallDetail})));
const Messages=React.lazy(()=>loadWorkspace().then(m=>({default:m.Messages})));
const Guides=React.lazy(()=>loadWorkspace().then(m=>({default:m.Guides})));
const GuideDetail=React.lazy(()=>loadWorkspace().then(m=>({default:m.GuideDetail})));
const Team=React.lazy(()=>loadWorkspace().then(m=>({default:m.Team})));
const Usage=React.lazy(()=>loadWorkspace().then(m=>({default:m.Usage})));
const SettingsPage=React.lazy(()=>loadWorkspace().then(m=>({default:m.SettingsPage})));

import {HomePage,ProductPage,SolutionsPage,PricingPage,AboutPage,ResourcesPage,ContactPage} from './marketing';
import {HistoryControls,HistoryNavigationProvider} from './history-navigation';

type AppContextType={store:Store;session:Session|null;setSession:(s:Session|null)=>void;org:Organization;setOrg:(id:string)=>void;theme:'light'|'dark';setTheme:(v:'light'|'dark')=>void;t:(text:string)=>string;toast:(text:string)=>void;run:<T,>(action:()=>Promise<T>,success?:string)=>Promise<T|undefined>;readiness:Record<string,boolean>;connection:'live'|'reconnecting'|'offline';setConnection:(v:'live'|'reconnecting'|'offline')=>void};
const Ctx=createContext<AppContextType|null>(null);
export const useApp=()=>{const c=useContext(Ctx);if(!c)throw new Error('App context unavailable');return c};
export const useOrgReadiness=()=>useApp().readiness;
function Provider({children}:{children:React.ReactNode}){
  const [store,setStore]=useState<Store>(()=>api.snapshot());
  const [sessionState,setSessionState]=useState<Session|null>(()=>{try{return JSON.parse(localStorage.getItem('voxops-session')||'null')}catch{return null}});
  const [theme,setThemeState]=useState<'light'|'dark'>(()=>localStorage.getItem('voxops-theme')==='dark'?'dark':'light');
  const [pending,setPending]=useState(0);
  const [connection,setConnection]=useState<'live'|'reconnecting'|'offline'>('live');
  const [toasts,setToasts]=useState<{id:number;text:string}[]>([]);
  useEffect(()=>subscribe(()=>setStore(api.snapshot())),[]);
  useEffect(()=>subscribePending(setPending),[]);
  const org=store.organizations.find(x=>x.id===sessionState?.orgId)||store.organizations[0];
  useEffect(()=>{document.documentElement.lang='en';document.documentElement.dir='ltr';document.documentElement.setAttribute('data-theme',theme);document.documentElement.style.setProperty('--accent',org.accentColor)},[theme,org.accentColor]);
  const setSession=(s:Session|null)=>{setSessionState(s);if(s)localStorage.setItem('voxops-session',JSON.stringify(s));else localStorage.removeItem('voxops-session')};
  const setOrg=(id:string)=>setSession(sessionState?{...sessionState,orgId:id}:null);
  const setTheme=(v:'light'|'dark')=>{setThemeState(v);localStorage.setItem('voxops-theme',v)};
  const t=(text:string)=>text;
  const toast=(text:string)=>{const id=Date.now()+Math.random();setToasts(old=>[...old,{id,text}]);setTimeout(()=>setToasts(old=>old.filter(x=>x.id!==id)),4200)};
  const run=async<T,>(action:()=>Promise<T>,success?:string):Promise<T|undefined>=>{try{const result=await action();if(success)toast(t(success));return result}catch(e){toast(e instanceof Error?e.message:'Something went wrong');return undefined}};
  const readiness=useMemo(()=>{const kb=store.knowledgeBases.find(x=>x.orgId===org.id);const channels=store.channels.filter(x=>x.orgId===org.id);const agents=store.agents.filter(x=>x.orgId===org.id);const wa=channels.find(x=>x.type==='whatsapp_call');return {knowledgeBaseReady:kb?.status==='ready'&&!!kb.chunkCount,phoneChannelReady:channels.some(x=>x.type==='sim'&&x.status==='connected'),whatsappCallingReady:wa?.status==='connected'&&!!wa.config.callingEnabled,whatsappMessagingReady:channels.some(x=>x.type==='whatsapp_message'&&x.status==='connected')&&store.templates.some(x=>x.orgId===org.id&&x.status==='approved'),operatorCallingReady:!!wa?.config.operatorCallingEnabled,personaReady:agents.some(x=>{const p=store.personas.find(p=>p.id===x.personaId);return !!p?.greeting&&!!p.roleDescription}),agentLive:agents.some(x=>x.status==='live')};},[store,org.id]);
  return <Ctx.Provider value={{store,session:sessionState,setSession,org,setOrg,theme,setTheme,t,toast,run,readiness,connection,setConnection}}>{pending>0&&<div className="progress-line"/>}{children}<div className="toast-wrap" aria-live="polite">{toasts.map(x=><div className="toast" key={x.id}>{x.text}<button aria-label="Dismiss" onClick={()=>setToasts(old=>old.filter(y=>y.id!==x.id))}><X size={16}/></button></div>)}</div></Ctx.Provider>;
}
export function Button({children,onClick,to,variant='',small=false,disabled=false,title,type='button'}:{children:React.ReactNode;onClick?:()=>void;to?:string;variant?:string;small?:boolean;disabled?:boolean;title?:string;type?:'button'|'submit'}){
  const cls='button '+variant+(small?' small':'');
  return to?<Link className={cls} to={to} title={title}>{children}</Link>:<button className={cls} onClick={onClick} disabled={disabled} title={title} type={type}>{children}</button>;
}
export function Badge({children,tone=''}:{children:React.ReactNode;tone?:string}){return <span className={'badge '+tone}><span className="dot"/>{children}</span>}
export function PageHead({eyebrow,title,description,action}:{eyebrow?:string;title:string;description?:string;action?:React.ReactNode}){const {t}=useApp();return <div className="page-heading"><div>{eyebrow&&<div className="eyebrow">{t(eyebrow)}</div>}<h1>{t(title)}</h1>{description&&<p>{t(description)}</p>}</div>{action}</div>}
export function Field({label,value,onChange,help,type='text',placeholder,rows=0,required=false}:{label:string;value:string|number;onChange:(v:string)=>void;help?:string;type?:string;placeholder?:string;rows?:number;required?:boolean}){
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
  return <div className="field"><label>{t(label)}{required?' *':''}</label>{rows?<textarea className="textarea" value={draft} onFocus={()=>{focused.current=true}} onBlur={blur} onChange={e=>{setDraft(e.target.value);commit(e.target.value)}} rows={rows} placeholder={placeholder?t(placeholder):undefined}/>:<input className="input" type={type} value={draft} onFocus={()=>{focused.current=true}} onBlur={blur} onChange={e=>{setDraft(e.target.value);commit(e.target.value)}} placeholder={placeholder?t(placeholder):undefined} required={required}/ >}{help&&<div className="help">{t(help)}</div>}</div>
}export function Empty({icon:Icon=Search,title,body,action}:{icon?:React.ComponentType<{size?:number}>;title:string;body:string;action?:React.ReactNode}){const {t}=useApp();return <div className="empty"><Icon size={32}/><h3>{t(title)}</h3><p>{t(body)}</p>{action}</div>}
export function Tabs({items,active,onChange}:{items:string[];active:string;onChange:(s:string)=>void}){const {t}=useApp();return <div className="tabs" role="tablist">{items.map(item=><button role="tab" aria-selected={active===item} className={'tab '+(active===item?'active':'')} onClick={()=>onChange(item)} key={item}>{t(item)}</button>)}</div>}
export function formatDate(value?:string){return value?new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—'}
export function formatDuration(seconds:number){return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')}
function Shell({children}:{children:React.ReactNode}){
  const {session,org,setOrg,store,t,theme,setTheme,setSession,connection}=useApp();
  const [open,setOpen]=useState(false),[collapsed,setCollapsed]=useState(false),[palette,setPalette]=useState(false),[query,setQuery]=useState('');
  const location=useLocation(),navigate=useNavigate();
  useEffect(()=>{const handle=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setPalette(x=>!x)}if(e.key==='Escape')setPalette(false)};window.addEventListener('keydown',handle);return()=>window.removeEventListener('keydown',handle)},[]);
  const nav: [string, [React.ComponentType<{size?:number}>, string, string][]][]=[['Workspace',[[LayoutDashboard,'Dashboard','/dashboard'],[Mic2,'Agents','/agents'],[BrainCircuit,'Knowledge','/knowledge'],[Radio,'Channels','/channels']]],['Operations',[[Activity,'Live','/live'],[History,'History','/history'],[MessageSquare,'Messages','/messages']]],['Manage',[[BookOpen,'Guides','/guides'],[Users,'Team','/team'],[ChartNoAxesCombined,'Usage','/usage'],[Settings,'Settings','/settings']]]];
  const current=nav.flatMap(x=>x[1]).find(x=>location.pathname.startsWith(x[2]))?.[1]||'Overview';

  return <div className="app"><div className={'mobile-backdrop '+(open?'show':'')} onClick={()=>setOpen(false)}/><aside className={'sidebar '+(open?'open':'')} style={collapsed?{width:76}:undefined}><Link to="/dashboard" className="brand"><span className="brand-mark"><Activity size={21}/></span>{!collapsed&&'voxops'}</Link>{!collapsed&&<select className="workspace" aria-label={t('Workspace')} value={org.id} onChange={e=>setOrg(e.target.value)}>{store.organizations.filter(x=>store.memberships.some(m=>m.orgId===x.id&&m.userId===session?.userId)).map(x=><option value={x.id} key={x.id}>{x.name}</option>)}</select>}<nav>{nav.map(([group,links])=><div key={group}><div className="nav-group">{!collapsed&&t(group)}</div>{links.map(([Icon,label,path])=><NavLink className={({isActive})=>'nav-link '+(isActive?'active':'')} to={path} key={path} title={t(label)} onClick={()=>setOpen(false)}><Icon size={18}/>{!collapsed&&t(label)}</NavLink>)}</div>)}</nav><div className="sidebar-bottom"><button className="ghost-btn" onClick={()=>setCollapsed(x=>!x)} title={t('Collapse sidebar')}><ChevronDown size={16} style={{transform:'rotate(90deg)'}}/>{!collapsed&&t('Collapse sidebar')}</button></div></aside><div className="main"><header className="topbar"><div className="row"><button className="icon-btn mobile-menu" onClick={()=>{setCollapsed(false);setOpen(true)}} aria-label={t('Open menu')}><Menu size={21}/></button><span className="small muted">{t('Workspace')} /</span><strong>{t(current)}</strong></div><div className="top-actions"><span aria-live="polite"><Badge tone={connection==='offline'?'danger':connection==='reconnecting'?'warning':'live'}>{t(connection)}</Badge></span><button className="icon-btn" onClick={()=>setPalette(true)} title={t('Command palette')}><Command size={18}/></button><button className="icon-btn" onClick={()=>setTheme(theme==='light'?'dark':'light')} title={t(theme==='light'?'Dark mode':'Light mode')}>{theme==='light'?<Moon size={18}/>:<Sun size={18}/>}</button><button className="icon-btn" onClick={()=>{setSession(null);navigate('/login')}} title={t('Sign out')}><LogOut size={18}/></button></div></header><div className="app-page-history"><HistoryControls/></div><main className="content">{children}</main></div>{palette&&<div className="modal-backdrop" onClick={()=>setPalette(false)}><div className="modal" onClick={e=>e.stopPropagation()}><div className="row between"><h2>{t('Go to')}</h2><button className="icon-btn" onClick={()=>setPalette(false)}><X size={18}/></button></div><input autoFocus className="input" value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('Search pages and actions')}/><div className="command-list">{nav.flatMap(x=>x[1]).filter(x=>x[1].toLowerCase().includes(query.toLowerCase())).map(([Icon,label,path])=><button key={path} className="nav-link" style={{width:'100%',border:0,background:'transparent'}} onClick={()=>{navigate(path);setPalette(false);setQuery('')}}><Icon size={18}/>{t(label)}</button>)}</div></div></div>}</div>;
}
function Protected({children}:{children:React.ReactNode}){const {session}=useApp();return session?<Shell>{children}</Shell>:<Navigate to="/login" replace/>}
function AppRoutes(){const {session}=useApp();return <Routes><Route path="/" element={<HomePage/>}/><Route path="/product" element={<ProductPage/>}/><Route path="/solutions" element={<SolutionsPage/>}/><Route path="/pricing" element={<PricingPage/>}/><Route path="/about" element={<AboutPage/>}/><Route path="/resources" element={<ResourcesPage/>}/><Route path="/contact" element={<ContactPage/>}/><Route path="/login" element={<AuthScreen mode="login"/>}/><Route path="/signup" element={<AuthScreen mode="signup"/>}/><Route path="/forgot-password" element={<AuthScreen mode="forgot"/>}/><Route path="/verify" element={<AuthScreen mode="verify"/>}/><Route path="/invite" element={<AuthScreen mode="invite"/>}/><Route path="/dashboard" element={<Protected><Dashboard/></Protected>}/><Route path="/onboarding" element={<Protected><Onboarding/></Protected>}/><Route path="/agents" element={<Protected><Agents/></Protected>}/><Route path="/agents/:id" element={<Protected><AgentDetail/></Protected>}/><Route path="/knowledge" element={<Protected><Knowledge/></Protected>}/><Route path="/channels" element={<Protected><Channels/></Protected>}/><Route path="/channels/:type" element={<Protected><ChannelDetail/></Protected>}/><Route path="/live" element={<Protected><Live/></Protected>}/><Route path="/history" element={<Protected><HistoryPage/></Protected>}/><Route path="/history/:id" element={<Protected><CallDetail/></Protected>}/><Route path="/messages" element={<Protected><Messages/></Protected>}/><Route path="/guides" element={<Protected><Guides/></Protected>}/><Route path="/guides/:slug" element={<Protected><GuideDetail/></Protected>}/><Route path="/team" element={<Protected><Team/></Protected>}/><Route path="/usage" element={<Protected><Usage/></Protected>}/><Route path="/settings" element={<Protected><SettingsPage/></Protected>}/><Route path="*" element={<Navigate to="/"/>}/></Routes>}
export default function App(){return <BrowserRouter><Provider><HistoryNavigationProvider><React.Suspense fallback={<div className="route-loading">Loading workspace...</div>}><AppRoutes/></React.Suspense></HistoryNavigationProvider></Provider></BrowserRouter>}




