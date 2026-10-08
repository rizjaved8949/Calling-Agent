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
import {Building2, KeyRound, Loader2, Plus} from 'lucide-react';
import {Badge, Button, Empty, Field, PageHead} from './app';
import {useApp} from './app-context';
import {api, hasAdminKey, setAdminKey, setViewingCompany, getViewingCompany} from './lib/api';

type Row = {org: {id: string; name: string; status: string}; health: string; openIssues: number};

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
                {viewing === row.org.id && <Badge tone="ok">{t('viewing')}</Badge>}
                <Button small variant="outline" onClick={() => view(row.org.id)}>
                  {viewing === row.org.id ? t('Stop viewing') : t('View as')}
                </Button>
              </div>
            </div>)}
          </div>}
  </div>;
}
