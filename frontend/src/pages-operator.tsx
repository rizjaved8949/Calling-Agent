/**
 * The operator's own screens: the companies on the platform, and onboarding a
 * new one.
 *
 * These need the admin key rather than a company's key, because listing and
 * creating companies is deliberately not something a customer can do. The key
 * is entered here and kept the same way the company key is — which is to say,
 * temporarily, until there is real sign-in.
 */
import {useCallback, useEffect, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Building2, KeyRound, Loader2, Plus} from 'lucide-react';
import {Badge, Button, Empty, Field, PageHead} from './app';
import {useApp} from './app-context';
import {api, hasAdminKey, setAdminKey, setViewingCompany, getViewingCompany} from './lib/api';
import {request} from './lib/api/http';

type Row = {org: {id: string; name: string; status: string}; health: string; openIssues: number};

/**
 * Where the platform's super admin signs in — deliberately not linked from
 * anywhere a customer would see.
 *
 * On a fresh deployment there is no account yet, and this page creates it:
 * once only, after which the server refuses to create another. Creating it
 * (and every password reset) shows a recovery code exactly once; "Forgot
 * password" asks for it, since no email is sent from this deployment.
 */
type Mode = 'loading' | 'setup' | 'login' | 'forgot' | 'recovery' | 'key';

const TITLES: Record<Mode, string> = {
  loading: 'Platform access', setup: 'Create the super admin', login: 'Super admin sign in',
  forgot: 'Reset your password', recovery: 'Save your recovery code', key: 'Server key',
};

export function PlatformLoginScreen() {
  const {setSession} = useApp();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>('loading');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
  const [recovery, setRecovery] = useState('');
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState<{token: string; email: string; name?: string} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    request<{superAdminExists: boolean}>('/api/platform/status', {anonymous: true})
      .then(b => setMode(b.superAdminExists ? 'login' : 'setup'))
      .catch(() => setMode('login'));
  }, []);

  const enter = (token: string, who: string, display?: string) => {
    setAdminKey(token);
    setSession({
      email: who, name: display || 'Super admin', userId: 'platform-admin',
      orgId: '', portal: 'platform', platformRole: 'superadmin',
    });
    navigate('/platform/companies');
  };

  const go = (next: Mode) => {setMode(next); setError(''); setPassword(''); setConfirm(''); setCode('')};

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if ((mode === 'setup' || mode === 'forgot') && password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'setup') {
        const r = await request<{token: string; recoveryCode: string; email: string}>('/api/platform/setup',
          {method: 'POST', anonymous: true, body: {email, password, name}});
        setPending({token: r.token, email: r.email, name});
        setRecovery(r.recoveryCode);
        go('recovery');
      } else if (mode === 'login') {
        const r = await request<{token: string; email: string; name: string}>('/api/platform/login',
          {method: 'POST', anonymous: true, body: {email, password}});
        enter(r.token, r.email, r.name);
      } else if (mode === 'forgot') {
        const r = await request<{token: string; recoveryCode: string; email: string}>('/api/platform/forgot-password',
          {method: 'POST', anonymous: true, body: {email, recoveryCode: code, newPassword: password}});
        setPending({token: r.token, email: r.email});
        setRecovery(r.recoveryCode);
        go('recovery');
      } else if (mode === 'key') {
        setAdminKey(code.trim());
        try {await api.getPlatformCompanies()} catch (cause) {setAdminKey(null); throw cause}
        enter(code.trim(), 'operator', 'Platform Operator');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That did not work.');
    } finally {setBusy(false)}
  };

  const pw = (label: string, value: string, set: (v: string) => void, auto = 'new-password') =>
    <div className="field"><label>{label}</label>
      <input className="input" type="password" autoComplete={auto} value={value} onChange={e => set(e.target.value)}/></div>;

  return <div className="auth-page"><div className="card auth-card">
    <h1 style={{fontSize: '1.5rem'}}><KeyRound size={20}/> {TITLES[mode]}</h1>
    {mode === 'loading' && <p className="muted small">Checking…</p>}

    {mode === 'recovery' && <div className="stack">
      <p className="muted small">This is the only way to reset your password if you forget it. It is shown <b>once</b> — store it in a password manager. Each reset gives you a new one.</p>
      <code style={{fontSize: '1.15rem', padding: 14, background: 'var(--surface)', borderRadius: 10, textAlign: 'center', letterSpacing: '.06em'}}>{recovery}</code>
      <Button variant="outline" onClick={() => {void navigator.clipboard?.writeText(recovery)}}>Copy</Button>
      <label className="check"><input type="checkbox" checked={saved} onChange={e => setSaved(e.target.checked)}/>
        <span>I have saved this code somewhere safe</span></label>
      <Button disabled={!saved || !pending} onClick={() => pending && enter(pending.token, pending.email, pending.name)}>Continue to the portal</Button>
    </div>}

    {mode !== 'loading' && mode !== 'recovery' && <form className="stack" onSubmit={submit}>
      {mode === 'setup' && <p className="muted small">No super admin exists on this server yet. This account can be created <b>only once</b>.</p>}
      {mode === 'forgot' && <p className="muted small">Enter the recovery code you saved when the account was created (or the server’s ADMIN_API_KEY).</p>}
      {mode === 'key' && <p className="muted small">Paste the server’s ADMIN_API_KEY.</p>}
      {mode !== 'key' && <div className="field"><label>Email</label>
        <input className="input" type="email" autoComplete="username" autoFocus value={email} onChange={e => setEmail(e.target.value)}/></div>}
      {mode === 'setup' && <div className="field"><label>Your name</label>
        <input className="input" value={name} onChange={e => setName(e.target.value)}/></div>}
      {(mode === 'forgot' || mode === 'key') && <div className="field"><label>{mode === 'key' ? 'ADMIN_API_KEY' : 'Recovery code'}</label>
        <input className="input" type={mode === 'key' ? 'password' : 'text'} autoComplete="off" spellCheck={false}
          placeholder={mode === 'key' ? '' : 'XXXXX-XXXXX-XXXXX-XXXXX'} value={code} onChange={e => setCode(e.target.value)}/></div>}
      {mode === 'login' && pw('Password', password, setPassword, 'current-password')}
      {(mode === 'setup' || mode === 'forgot') && <>
        {pw(mode === 'forgot' ? 'New password' : 'Password', password, setPassword)}
        {pw('Confirm password', confirm, setConfirm)}
        <div className="help">At least 10 characters.</div>
      </>}
      {error && <div className="notice danger">{error}</div>}
      <Button type="submit" disabled={busy}>
        {busy ? <><Loader2 size={15} className="spin"/> Working…</>
          : mode === 'setup' ? 'Create super admin' : mode === 'forgot' ? 'Reset password' : 'Sign in'}
      </Button>
      <div className="row between small">
        {mode === 'login' && <button type="button" className="link-btn" onClick={() => go('forgot')}>Forgot password?</button>}
        {(mode === 'forgot' || mode === 'key') && <button type="button" className="link-btn" onClick={() => go('login')}>Back to sign in</button>}
        {mode === 'login' && <button type="button" className="link-btn" onClick={() => go('key')}>Use server key</button>}
      </div>
    </form>}
  </div></div>;
}

export function AdminKeyGate({onReady}: {onReady: () => void}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const candidate = key.trim();
    if (!candidate) return;
    setBusy(true);
    setError('');
    setAdminKey(candidate);
    try {
      await api.getPlatformCompanies();
      onReady();
    } catch (cause) {
      setAdminKey(null);
      setError(cause instanceof Error ? cause.message : 'That key was not accepted.');
    } finally {
      setBusy(false);
    }
  };

  return <form className="card stack" onSubmit={submit} style={{maxWidth: 520}}>
    <h2><KeyRound size={18}/> Operator key</h2>
    <p className="muted small">
      The portal lists and registers companies, which a customer's key cannot do.
      Paste the admin key from your server configuration.
    </p>
    <input className="input" type="password" autoFocus autoComplete="off" spellCheck={false}
      placeholder="ADMIN_API_KEY" value={key} onChange={e => setKey(e.target.value)}/>
    {error && <p className="small danger">{error}</p>}
    <Button type="submit" disabled={busy || !key.trim()}>
      {busy ? <><Loader2 size={15} className="spin"/> Checking…</> : 'Continue'}
    </Button>
    <p className="small muted">
      Temporary, like the company key: sign-in replaces both.
    </p>
  </form>;
}

export function OperatorCompanies() {
  const {t, toast} = useApp();
  const [ready, setReady] = useState(() => hasAdminKey());
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({phoneNumberId: '', name: '', wabaId: '', defaultCountryCode: ''});
  const [issued, setIssued] = useState<{name: string; apiKey: string} | null>(null);
  const viewing = getViewingCompany();

  const load = useCallback(async () => {
    if (!hasAdminKey()) {setLoading(false); return}
    try {
      setRows(((await api.getPlatformCompanies()) ?? []) as Row[]);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load companies.');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {if (ready) void load()}, [ready, load]);

  if (!ready) {
    return <div className="stack">
      <PageHead eyebrow="Platform" title="Companies"/>
      <AdminKeyGate onReady={() => {setReady(true); setLoading(true)}}/>
    </div>;
  }

  const register = async () => {
    if (!draft.phoneNumberId.trim() || !draft.name.trim()) {
      toast(t('A name and a WhatsApp phone number id are both needed.'));
      return;
    }
    try {
      const created = await api.registerCompany({...draft}) as {name: string; apiKey: string};
      // Shown once, because the API never returns it again.
      setIssued({name: created.name, apiKey: created.apiKey});
      setAdding(false);
      setDraft({phoneNumberId: '', name: '', wabaId: '', defaultCountryCode: ''});
      await load();
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : 'That company could not be registered.');
    }
  };

  // Which companies may run their numbers on the platform's own Meta and
  // Infobip credentials (the server's environment) — how a demo account
  // places real calls without credentials of its own.
  const [demo, setDemo] = useState<Record<string, boolean>>({});
  useEffect(() => {
    request<{companies: {phoneNumberId: string; allowPlatformCredentials?: boolean}[]}>('/api/companies')
      .then(b => setDemo(Object.fromEntries(b.companies.map(c => [c.phoneNumberId, Boolean(c.allowPlatformCredentials)]))))
      .catch(() => {});
  }, [rows.length]);
  const toggleDemo = async (companyId: string) => {
    const next = !demo[companyId];
    try {
      await request(`/api/companies/${encodeURIComponent(companyId)}`, {
        method: 'PATCH', body: {allowPlatformCredentials: next},
      });
      setDemo(d => ({...d, [companyId]: next}));
      toast(next ? 'This company can now use your platform credentials.' : 'Platform credentials turned off for this company.');
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : 'Could not change that.');
    }
  };

  const view = (companyId: string) => {
    setViewingCompany(companyId === viewing ? null : companyId);
    // Everything downstream reads the company from the client, so the simplest
    // honest refresh is a reload.
    window.location.reload();
  };

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Companies"
      description="Every business using the platform, and the one you are currently looking at."
      action={<Button onClick={() => setAdding(x => !x)}>
        <Plus size={15}/> {t('Register a company')}
      </Button>}/>

    {error && <div className="notice danger">{error}</div>}

    {issued && <div className="card stack">
      <strong>{t('API key for')} {issued.name}</strong>
      <p className="small muted">
        {t('Give this to the company. It is shown once and the server cannot return it again.')}
      </p>
      <code className="small" style={{overflowWrap: 'anywhere'}}>{issued.apiKey}</code>
      <div className="row">
        <Button small variant="outline"
          onClick={() => {navigator.clipboard?.writeText(issued.apiKey); toast(t('Copied.'))}}>
          {t('Copy')}
        </Button>
        <Button small variant="outline" onClick={() => setIssued(null)}>{t('Done')}</Button>
      </div>
    </div>}

    {adding && <div className="card stack">
      <Field label="Company name" value={draft.name}
        onChange={v => setDraft(d => ({...d, name: v}))} placeholder="Northwind Services"/>
      <Field label="WhatsApp phone number id" value={draft.phoneNumberId}
        onChange={v => setDraft(d => ({...d, phoneNumberId: v}))}
        help="The numeric id Meta shows beside the number, not the number itself. It is how every webhook identifies them."/>
      <Field label="WhatsApp business account id" value={draft.wabaId}
        onChange={v => setDraft(d => ({...d, wabaId: v}))} help="Optional now; needed before they can send templates."/>
      <Field label="Country code" value={draft.defaultCountryCode}
        onChange={v => setDraft(d => ({...d, defaultCountryCode: v}))} placeholder="92"
        help="Used to expand numbers typed in national form. There is no shared default."/>
      <div className="row">
        <Button onClick={() => void register()}>{t('Register')}</Button>
        <Button variant="outline" onClick={() => setAdding(false)}>{t('Cancel')}</Button>
      </div>
    </div>}

    {loading
      ? <div className="small muted">{t('Loading…')}</div>
      : rows.length === 0
        ? <Empty icon={Building2} title="No companies yet"
            body="Register one to get started. You will be given an API key to hand over, once."/>
        : <div className="stack">
            {rows.map(row => <div className="setup-row" key={row.org.id}>
              <div className="setup-row-main">
                <strong>{row.org.name || row.org.id}</strong>
                <span className="small muted">
                  {row.org.id}
                  {row.openIssues > 0 && ` — ${t('not fully connected')}`}
                </span>
              </div>
              <div className="row">
                {viewing === row.org.id && <Badge tone="success">{t('viewing')}</Badge>}
                <Button small variant={demo[row.org.id] ? '' : 'outline'} onClick={() => void toggleDemo(row.org.id)}
                  title={t('Let this company connect numbers using your own Meta/Infobip credentials from the server settings')}>
                  {demo[row.org.id] ? t('Uses your credentials') : t('Allow your credentials')}
                </Button>
                <Button small variant="outline" onClick={() => view(row.org.id)}>
                  {viewing === row.org.id ? t('Stop viewing') : t('View as')}
                </Button>
              </div>
            </div>)}
          </div>}
  </div>;
}
