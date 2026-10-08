import {createContext, useContext} from 'react';
import type {Organization, Role, Session, Store} from './lib/types';

/**
 * The app context lives apart from `app.tsx` so that file can export nothing
 * but components. React Fast Refresh refuses to hot-update a module with a
 * non-component export, and a hook counts as one — which is why every edit used
 * to trigger a full page reload and throw away the signed-in state.
 */
export type AppContextType = {
  store: Store;
  session: Session | null;
  setSession: (s: Session | null) => void;
  org: Organization;
  setOrg: (id: string) => void;
  theme: 'light' | 'dark';
  setTheme: (v: 'light' | 'dark') => void;
  t: (text: string) => string;
  toast: (text: string) => void;
  run: <T,>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
  readiness: Record<string, boolean>;
  connection: 'live' | 'reconnecting' | 'offline';
  setConnection: (v: 'live' | 'reconnecting' | 'offline') => void;
  role: Role;
  isPlatform: boolean;
  canManage: boolean;
  readOnly: boolean;
};

export const AppCtx = createContext<AppContextType | null>(null);

export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error('App context unavailable');
  return c;
};

export const useOrgReadiness = () => useApp().readiness;
