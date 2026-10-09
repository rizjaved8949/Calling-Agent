import React,{createContext,useContext,useEffect,useState} from 'react';
import {useLocation,useNavigate,useNavigationType} from 'react-router-dom';
import {ArrowLeft,ArrowRight} from 'lucide-react';

type HistoryNavigation={canGoBack:boolean;canGoForward:boolean;goBack:()=>void;goForward:()=>void;backLabel:string};
const HistoryContext=createContext<HistoryNavigation|null>(null);

/**
 * Where "back" should land when there is nothing behind us in this tab — a
 * typed URL, a bookmark, or a link opened in a new tab. The workspace lives
 * under /app and the internal portal under /platform.
 */
function fallbackFor(pathname:string):string{
  if(pathname==='/')return '/';
  if(/^\/app\/agents\/.+/.test(pathname))return '/app/agents';
  if(/^\/app\/knowledge\/.+/.test(pathname))return '/app/knowledge';
  if(/^\/app\/channels\/.+/.test(pathname))return '/app/channels';
  if(/^\/app\/campaigns\/.+/.test(pathname))return '/app/campaigns';
  if(/^\/app\/recordings\/.+/.test(pathname))return '/app/recordings';
  if(/^\/app\/guides\/.+/.test(pathname))return '/app/guides';
  if(/^\/platform\/companies\/.+/.test(pathname))return '/platform/companies';
  if(pathname==='/forgot-password')return '/login';
  if(pathname==='/verify')return '/forgot-password';
  if(pathname==='/invite')return '/login';
  if(pathname.startsWith('/app/'))return '/app/dashboard';
  if(pathname.startsWith('/platform/'))return '/platform/companies';
  // Marketing and auth pages fall back to the home page.
  return '/';
}

/** Human wording for where Back will actually take you. */
function labelFor(target:string):string{
  if(target==='/')return 'Home';
  const last=target.split('/').filter(Boolean).pop()||'Back';
  return last.charAt(0).toUpperCase()+last.slice(1);
}

/**
 * The browser does not expose whether a forward entry exists, so we remember
 * the furthest index this tab has reached. It is kept in sessionStorage because
 * component state is lost on a full page load, which used to leave the Forward
 * button dead for the rest of the session.
 */
const FURTHEST_KEY='ca-history-furthest';
const readFurthest=()=>{const n=Number(sessionStorage.getItem(FURTHEST_KEY));return Number.isFinite(n)?n:0};
const writeFurthest=(n:number)=>{try{sessionStorage.setItem(FURTHEST_KEY,String(n))}catch{/* private mode */}};

export function HistoryNavigationProvider({children}:{children:React.ReactNode}){
  const location=useLocation();
  const navigate=useNavigate();
  const navigationType=useNavigationType();
  const rawIndex=Number(window.history.state?.idx);
  const currentIndex=Number.isFinite(rawIndex)?rawIndex:0;
  const [furthestIndex,setFurthestIndex]=useState(()=>Math.max(readFurthest(),currentIndex));

  useEffect(()=>{
    // A push discards anything that was ahead; a pop or replace keeps it.
    const next=navigationType==='PUSH'?currentIndex:Math.max(furthestIndex,currentIndex);
    if(next!==furthestIndex)setFurthestIndex(next);
    writeFurthest(next);
  },[location.key,navigationType,currentIndex,furthestIndex]);

  const fallback=fallbackFor(location.pathname);
  const hasRealHistory=currentIndex>0;
  const canGoBack=hasRealHistory||fallback!==location.pathname;
  const canGoForward=currentIndex<furthestIndex;
  const goBack=()=>{
    if(hasRealHistory)navigate(-1);
    else if(fallback!==location.pathname)navigate(fallback);
  };
  const goForward=()=>{if(canGoForward)navigate(1)};
  const backLabel=hasRealHistory?'Back':labelFor(fallback);

  return <HistoryContext.Provider value={{canGoBack,canGoForward,goBack,goForward,backLabel}}>{children}</HistoryContext.Provider>;
}

export function HistoryControls(){
  const history=useContext(HistoryContext);
  if(!history)throw new Error('HistoryControls must be inside HistoryNavigationProvider');
  return <div className="history-controls" role="group" aria-label="Page navigation">
    <button type="button" onClick={history.goBack} disabled={!history.canGoBack}
      aria-label={history.canGoBack?'Go back':'Nothing to go back to'}
      title={history.canGoBack?'Go back':'Nothing to go back to'}>
      <ArrowLeft size={16}/><span>{history.backLabel}</span>
    </button>
    <span className="history-controls-divider" aria-hidden="true"/>
    <button type="button" onClick={history.goForward} disabled={!history.canGoForward}
      aria-label={history.canGoForward?'Go forward':'Nothing to go forward to'}
      title={history.canGoForward?'Go forward':'Nothing to go forward to'}>
      <span>Forward</span><ArrowRight size={16}/>
    </button>
  </div>;
}
