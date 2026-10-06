import React,{createContext,useContext,useEffect,useState} from 'react';
import {useLocation,useNavigate,useNavigationType} from 'react-router-dom';
import {ArrowLeft,ArrowRight} from 'lucide-react';

type HistoryNavigation={canGoBack:boolean;canGoForward:boolean;goBack:()=>void;goForward:()=>void};
const HistoryContext=createContext<HistoryNavigation|null>(null);

function fallbackFor(pathname:string){
  if(pathname==='/')return '/';
  if(pathname.startsWith('/agents/'))return '/agents';
  if(pathname.startsWith('/channels/'))return '/channels';
  if(pathname.startsWith('/history/'))return '/history';
  if(pathname.startsWith('/guides/'))return '/guides';
  if(pathname==='/forgot-password')return '/login';
  if(pathname==='/verify')return '/forgot-password';
  if(pathname==='/invite')return '/login';
  if(['/login','/signup','/product','/solutions','/pricing','/resources','/about','/contact','/dashboard'].includes(pathname))return '/';
  return '/dashboard';
}

export function HistoryNavigationProvider({children}:{children:React.ReactNode}){
  const location=useLocation();
  const navigate=useNavigate();
  const navigationType=useNavigationType();
  const rawIndex=Number(window.history.state?.idx);
  const currentIndex=Number.isFinite(rawIndex)?rawIndex:0;
  const [furthestIndex,setFurthestIndex]=useState(currentIndex);
  useEffect(()=>{
    setFurthestIndex(previous=>navigationType==='PUSH'?currentIndex:Math.max(previous,currentIndex));
  },[location.key,navigationType,currentIndex]);
  const fallback=fallbackFor(location.pathname);
  const canGoBack=currentIndex>0||fallback!==location.pathname;
  const canGoForward=currentIndex<furthestIndex;
  const goBack=()=>{if(currentIndex>0)navigate(-1);else if(fallback!==location.pathname)navigate(fallback)};
  const goForward=()=>{if(canGoForward)navigate(1)};
  return <HistoryContext.Provider value={{canGoBack,canGoForward,goBack,goForward}}>{children}</HistoryContext.Provider>;
}

export function HistoryControls(){
  const history=useContext(HistoryContext);
  if(!history)throw new Error('HistoryControls must be inside HistoryNavigationProvider');
  return <div className="history-controls" role="group" aria-label="Page navigation">
    <button type="button" onClick={history.goBack} disabled={!history.canGoBack} aria-label="Go back" title="Go back"><ArrowLeft size={16}/><span>Back</span></button>
    <span className="history-controls-divider" aria-hidden="true"/>
    <button type="button" onClick={history.goForward} disabled={!history.canGoForward} aria-label="Go forward" title="Go forward"><span>Forward</span><ArrowRight size={16}/></button>
  </div>;
}