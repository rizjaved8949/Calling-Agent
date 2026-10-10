/**
 * The platform portal: your own screens, not a customer's.
 *
 * Everything here is read from the server. The pages this replaces —
 * provisioning, presets, a search console, an audit trail — were placeholders
 * for features that do not exist, and a portal of "coming soon" cards is
 * worse than a smaller portal that works. What is left answers the three
 * questions actually asked about a customer: have they got set up, what are
 * they using, and what is blocking them.
 */
import {useCallback, useEffect, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {
  ArrowLeft, Building2, CheckCircle2, CircleAlert, Copy, Cpu, KeyRound, Loader2, Plug, Plus,
  RefreshCw, ServerCog, Trash2, XCircle,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {CopyField, ErrorNote, Loading, Modal, Section, Select, TextInput, useConfirm} from './ui';
import {api, setViewingCompany, getViewingCompany} from './lib/api';
import {request} from './lib/api/http';
import {
  CHANNEL_LABEL, STATUS_LABEL, formatDuration, formatWhen, statusTone, type CallStatus,
} from './lib/api/calls';

type Stage = 'no numbers' | 'not verified' | 'no agent' | 'not assigned' | 'live';

type CompanyRow = {
  id: string;
  name: string;
  stage: Stage;
  blocker: string | null;
  numbers: number;
  verifiedNumbers: number;
  agents: number;
  calls: number;
  minutes: number;
  failedCalls: number;
  recordings: number;
  lastCallAt: number | null;
  driveConnected: boolean;
  allowPlatformCredentials: boolean;
  suspended?: boolean;
  suspendedReason?: string | null;
};

type Overview = {
  companies: CompanyRow[];
  totals: {companies: number; numbers: number; verified: number; calls: number;
           minutes: number; recordings: number; failed: number};
  store: string;
};

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

const STAGE_TONE: Record<Stage, string> = {
  'live': 'success', 'not assigned': 'warning', 'no agent': 'warning',
  'not verified': 'danger', 'no numbers': '',
};

function Stat({label, value, hint}: {label: string; value: React.ReactNode; hint?: string}) {
  return <div className="card">
    <div className="stat-label">{label}</div>
    <div className="stat-number mono">{value}</div>
    {hint && <div className="small muted">{hint}</div>}
  </div>;
}

// ---------------------------------------------------------------------------
// Companies
// ---------------------------------------------------------------------------

/**
 * The speech engine, said on the page the operator actually lands on.
 *
 * The settings have their own screen, but a key that is missing or wrong makes
 * every call on the platform connect and then sit in silence — and an operator
 * who does not know the screen exists has no way to tell that is what happened.
 * So the state is stated here, with the way to change it, rather than waiting
 * to be looked for.
 */
function EngineBanner() {
  const [state, setState] = useState<EngineState | null>(null);
  useEffect(() => {
    void request<EngineState>('/api/platform/engine')
      .then(setState)
      .catch(() => setState(null));
  }, []);
  if (!state) return null;

  const wrong = !state.keySet || !state.supported;
  return <div className={'card row between wrap' + (wrong ? ' notice danger' : '')} style={{gap: 14}}>
    <div className="row" style={{gap: 12}}>
      <Cpu size={20} color={wrong ? 'var(--destructive)' : 'var(--accent)'}/>
      <div>
        <strong>Speech engine — {state.engine}{state.model ? ` · ${state.model}` : ''}</strong>
        <div className="small muted">
          {state.keySet
            ? `API key ${state.keyHint} in place, from ${state.source}. Every company uses this unless it has its own.`
            : 'No API key is set. Calls will connect and then stay silent until one is.'}
        </div>
      </div>
    </div>
    <Button variant={wrong ? '' : 'outline'} to="/platform/engine">
      {state.keySet ? 'Change the key or model' : 'Set the API key'}
    </Button>
  </div>;
}

export function AdminCompanies() {
  const {toast} = useApp();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<{name: string; apiKey: string} | null>(null);
  const [suspending, setSuspending] = useState<CompanyRow | null>(null);
  const viewing = getViewingCompany();

  const load = useCallback(async () => {
    try {
      setData(await request<Overview>('/api/platform/overview'));
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not load the companies.'));
    }
  }, []);
  useEffect(() => {void load()}, [load]);

  const toggleCredentials = async (row: CompanyRow) => {
    setBusy(row.id);
    try {
      await request(`/api/companies/${encodeURIComponent(row.id)}`, {
        method: 'PATCH', body: {allowPlatformCredentials: !row.allowPlatformCredentials},
      });
      setData(old => old && {...old, companies: old.companies.map(c =>
        c.id === row.id ? {...c, allowPlatformCredentials: !c.allowPlatformCredentials} : c)});
      toast(row.allowPlatformCredentials
        ? 'They can no longer use your own provider credentials.'
        : 'They can now connect a number on your own provider credentials.');
    } catch (cause) {
      toast(errorText(cause, 'Could not change that.'));
    } finally {setBusy('')}
  };

  const resume = async (row: CompanyRow) => {
    try {
      await request(`/api/platform/companies/${encodeURIComponent(row.id)}/suspend`,
        {method: 'POST', body: {suspended: false}});
      setData(old => old && {...old, companies: old.companies.map(c =>
        c.id === row.id ? {...c, suspended: false, suspendedReason: null} : c)});
      toast(`${row.name || row.id} is switched back on.`);
    } catch (cause) {
      toast(errorText(cause, 'Could not switch them back on.'));
    }
  };

  const view = (companyId: string) => {
    setViewingCompany(companyId === viewing ? null : companyId);
    // Every screen reads the company from the client, so the honest refresh
    // is a reload rather than half the app showing the previous one.
    window.location.reload();
  };

  const totals = data?.totals;

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Companies"
      description="Everyone using the platform, how far they have got, and what is stopping them."
      action={<div className="row wrap">
        <Button variant="outline" onClick={() => void load()}><RefreshCw size={15}/> Refresh</Button>
        <Button onClick={() => setAdding(true)}><Plus size={15}/> Register a company</Button>
      </div>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    <EngineBanner/>

    {totals && <div className="stat-grid">
      <Stat label="Companies" value={totals.companies}/>
      <Stat label="Numbers connected" value={totals.numbers}
        hint={`${totals.verified} verified`}/>
      <Stat label="Calls" value={totals.calls.toLocaleString()}
        hint={`${totals.failed} failed`}/>
      <Stat label="Minutes" value={totals.minutes.toLocaleString()}/>
      <Stat label="Recordings" value={totals.recordings.toLocaleString()}/>
    </div>}

    {issued && <div className="card stack">
      <strong>API key for {issued.name}</strong>
      <p className="small muted" style={{margin: 0}}>
        Give this to the company. It is shown once and the server cannot return it again.
        They will not usually need it — signing in gives them their own.
      </p>
      <CopyField value={issued.apiKey}/>
      <div className="row"><Button variant="outline" onClick={() => setIssued(null)}>Done</Button></div>
    </div>}

    {data === null && !error ? <Loading label="Loading companies…"/>
      : (data?.companies.length ?? 0) === 0
        ? <Empty icon={Building2} title="No companies yet"
            body="A company appears here as soon as someone signs up. You can also register one yourself and hand over its key."
            action={<Button onClick={() => setAdding(true)}>Register a company</Button>}/>
        : <div className="card">
            {(data?.companies ?? []).map(row => <div className="list-row" key={row.id}>
              <div className="list-row-main">
                <Link to={'/platform/companies/' + encodeURIComponent(row.id)}>
                  <strong>{row.name || row.id}</strong>
                </Link>
                <div className="small muted mono">{row.id}</div>
                <div className="row wrap small muted" style={{gap: 12, marginTop: 6}}>
                  <span>{row.numbers} {row.numbers === 1 ? 'number' : 'numbers'}</span>
                  <span>{row.agents} {row.agents === 1 ? 'agent' : 'agents'}</span>
                  <span>{row.calls} calls · {row.minutes} min</span>
                  {row.lastCallAt
                    ? <span>last call {formatWhen(row.lastCallAt)}</span>
                    : <span>no calls yet</span>}
                </div>
                {row.suspended
                  ? <div className="small" style={{color: 'var(--destructive)', marginTop: 4}}>
                      <CircleAlert size={12}/> Switched off
                      {row.suspendedReason ? ` — ${row.suspendedReason}` : ''}
                    </div>
                  : row.blocker && <div className="small" style={{color: 'var(--warning)', marginTop: 4}}>
                      <CircleAlert size={12}/> {row.blocker}
                    </div>}
              </div>
              <div className="list-row-actions">
                {row.suspended
                  ? <Badge tone="danger">switched off</Badge>
                  : <Badge tone={STAGE_TONE[row.stage]}>{row.stage}</Badge>}
                {viewing === row.id && <Badge tone="live">viewing</Badge>}
                <Button small variant={row.allowPlatformCredentials ? '' : 'outline'}
                  disabled={busy === row.id} onClick={() => void toggleCredentials(row)}
                  title="Let this company connect a number using the platform's own Meta and Infobip credentials — how a demo account calls on your numbers">
                  {busy === row.id ? <Loader2 size={13} className="spin"/>
                    : row.allowPlatformCredentials ? 'Uses your credentials' : 'Allow your credentials'}
                </Button>
                <Button small variant="outline" onClick={() => view(row.id)}>
                  {viewing === row.id ? 'Stop viewing' : 'View as'}
                </Button>
                <Button small variant="outline"
                  onClick={() => row.suspended ? void resume(row) : setSuspending(row)}
                  title={row.suspended
                    ? 'Let them sign in and take calls again'
                    : 'Stop sign-in and calling without deleting anything'}>
                  {row.suspended ? 'Switch on' : 'Switch off'}
                </Button>
              </div>
            </div>)}
          </div>}

    {data?.store === 'local file' && <div className="notice warning">
      This server is keeping its rows in a local JSON file, not Supabase. That is fine on a
      laptop and wrong in production — the next deploy erases it.
    </div>}

    {suspending && <SuspendCompany row={suspending} onClose={() => setSuspending(null)}
      onDone={reason => {
        setData(old => old && {...old, companies: old.companies.map(c =>
          c.id === suspending.id ? {...c, suspended: true, suspendedReason: reason} : c)});
        setSuspending(null);
        toast(`${suspending.name || suspending.id} is switched off.`);
      }}/>}

    {adding && <RegisterCompany onClose={() => setAdding(false)}
      onRegistered={(name, apiKey) => {setIssued({name, apiKey}); setAdding(false); void load()}}/>}
  </div>;
}

/**
 * Switching a company off.
 *
 * The reason is asked for here rather than invented, because it is the exact
 * sentence they will meet when they try to sign in — "This account is switched
 * off" with no explanation reads as a fault, and they will raise a ticket
 * about it.
 */
function SuspendCompany({row, onClose, onDone}: {
  row: CompanyRow; onClose: () => void; onDone: (reason: string) => void;
}) {
  const [reason, setReason] = useState('This account is switched off. Please contact us.');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const go = async () => {
    setBusy(true); setError('');
    try {
      await request(`/api/platform/companies/${encodeURIComponent(row.id)}/suspend`,
        {method: 'POST', body: {suspended: true, reason: reason.trim()}});
      onDone(reason.trim());
    } catch (cause) {
      setError(errorText(cause, 'Could not switch them off.'));
    } finally {setBusy(false)}
  };

  return <Modal title={`Switch off ${row.name || row.id}?`} onClose={onClose}
    footer={<>
      <button className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="button danger" onClick={() => void go()} disabled={busy}>
        {busy ? <><Loader2 size={15} className="spin"/> Switching off…</> : 'Switch off'}
      </button>
    </>}>
    <div className="stack">
      <div className="row" style={{alignItems: 'flex-start', gap: 12}}>
        <CircleAlert size={20} color="var(--warning)" style={{flex: 'none', marginTop: 2}}/>
        <div className="small" style={{lineHeight: 1.6}}>
          <p style={{margin: '0 0 8px'}}>
            Nobody at this company can sign in, their numbers stop answering, and
            no call or message goes out.
          </p>
          <p style={{margin: 0}}>
            <b>Nothing is deleted.</b> Their {row.calls} calls, {row.recordings} recordings
            and {row.numbers} number{row.numbers === 1 ? '' : 's'} are kept, and one
            click switches them back on.
          </p>
        </div>
      </div>
      <TextInput label="What they are told" rows={2} value={reason} onChange={setReason}
        help="Shown when they try to sign in. A reason saves them raising a ticket about it."/>
      {error && <div className="notice danger small">{error}</div>}
    </div>
  </Modal>;
}

function RegisterCompany({onClose, onRegistered}: {
  onClose: () => void; onRegistered: (name: string, apiKey: string) => void;
}) {
  const [name, setName] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [countryCode, setCountryCode] = useState('');
  const [ownerEmail, setOwnerEmail] = useState('');
  const [ownerName, setOwnerName] = useState('');
  const [ownerPassword, setOwnerPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!name.trim()) {setError('Give the company a name.'); return}
    setBusy(true); setError('');
    try {
      const body = await api.registerCompany({
        name: name.trim(),
        // A company connects its real numbers itself, on the Numbers page, so
        // this id is only the row key until then — same as a self-signup.
        phoneNumberId: phoneNumberId.trim() || `pending-${Date.now().toString(36)}`,
        defaultCountryCode: countryCode.trim(),
        ownerEmail: ownerEmail.trim(),
        ownerPassword: ownerPassword,
        ownerName: ownerName.trim(),
      }) as {apiKey?: string} | undefined;
      onRegistered(name.trim(), body?.apiKey ?? '');
    } catch (cause) {
      setError(errorText(cause, 'That company could not be registered.'));
    } finally {setBusy(false)}
  };

  return <Modal title="Register a company" onClose={onClose}
    subtitle="Only needed if you are setting someone up for them. Normally a company signs up itself."
    footer={<>
      <button className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="button" onClick={() => void submit()} disabled={busy}>
        {busy ? <><Loader2 size={15} className="spin"/> Registering…</> : 'Register'}
      </button>
    </>}>
    <div className="stack">
      <TextInput label="Company name" value={name} onChange={setName} required
        placeholder="Northwind Services" autoFocus/>
      <TextInput label="Account id" value={phoneNumberId} onChange={setPhoneNumberId}
        placeholder="left empty, one is generated"
        help="Leave this empty. It is only the internal row key — their real numbers are connected on the Numbers page, each with its own credentials."/>
      <TextInput label="Country code" value={countryCode} onChange={setCountryCode}
        placeholder="92"
        help="Used when somebody types a local number without its country code."/>

      <Section title="Who signs in"
        help="Without this the company exists but nobody can reach it. Leave empty only if you plan to invite somebody yourself.">
        <TextInput label="Owner email" value={ownerEmail} onChange={setOwnerEmail}
          type="email" placeholder="owner@northwind.test"/>
        <div className="field-grid">
          <TextInput label="Owner name" value={ownerName} onChange={setOwnerName}
            placeholder="Sara Ahmed"/>
          <TextInput label="Owner password" value={ownerPassword} onChange={setOwnerPassword}
            type="password" help="At least 8 characters. Give it to them to change."/>
        </div>
      </Section>
      {error && <div className="notice danger small">{error}</div>}
    </div>
  </Modal>;
}

// ---------------------------------------------------------------------------
// One company
// ---------------------------------------------------------------------------

/** What `channel_readiness` returns: a list, and the key is `connected`. */
type ChannelReadiness = {
  id?: string; label: string; connected: boolean; missing?: string[]; required?: string[];
};

type Detail = {
  company: Record<string, any>;
  numbers: Record<string, any>[];
  agents: Record<string, any>[];
  knowledgeBases: Record<string, any>[];
  documentCount: number;
  recentCalls: Record<string, any>[];
  stats: Record<string, number>;
};

export function AdminCompanyDetail() {
  const {id} = useParams();
  const {toast} = useApp();
  const navigate = useNavigate();
  const [confirm, confirmDialog] = useConfirm();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [rotated, setRotated] = useState('');

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setData(await request<Detail>(`/api/platform/companies/${encodeURIComponent(id)}`));
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not load this company.'));
    }
  }, [id]);
  useEffect(() => {void load()}, [load]);

  if (!data) return <div className="stack">
    <PageHead eyebrow="Platform" title="Company"/>
    {error ? <ErrorNote error={error} onRetry={() => void load()}/> : <Loading/>}
  </div>;

  const {company} = data;
  const rotate = () => confirm({
    title: 'Issue a new API key?',
    body: <>The company's current key stops working immediately. Anything using
      it — their own integrations, a saved session — has to be given the new one.</>,
    confirmLabel: 'Issue a new key',
    onConfirm: async () => {
      const body = await request<{apiKey: string}>(
        `/api/companies/${encodeURIComponent(id!)}/rotate-key`, {method: 'POST'});
      setRotated(body.apiKey);
    },
  });

  const remove = () => confirm({
    title: 'Remove this company?',
    body: <>Their registration is deleted and nobody can sign in to it. Their calls
      and stored recordings are left alone — erasing a year of audio is not
      something this button should do as a side effect.</>,
    typeToConfirm: company.name || id,
    confirmLabel: 'Remove company',
    onConfirm: async () => {
      await request(`/api/companies/${encodeURIComponent(id!)}`, {method: 'DELETE'});
      toast('Company removed.');
      navigate('/platform/companies');
    },
  });

  return <div className="stack">
    <PageHead eyebrow="Platform" title={company.name || id || 'Company'}
      description={company.phoneNumberId}
      action={<div className="row wrap">
        <Button variant="outline" to="/platform/companies"><ArrowLeft size={15}/> All companies</Button>
        <Button variant="outline" onClick={rotate}><KeyRound size={15}/> New API key</Button>
        <Button variant="outline" onClick={remove}><Trash2 size={15}/> Remove</Button>
      </div>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}
    {rotated && <div className="card stack">
      <strong>New API key — shown once</strong>
      <CopyField value={rotated}/>
      <div className="row"><Button variant="outline" onClick={() => setRotated('')}>Done</Button></div>
    </div>}

    <div className="stat-grid">
      <Stat label="Calls" value={data.stats.total ?? data.recentCalls.length}/>
      <Stat label="Numbers" value={data.numbers.length}/>
      <Stat label="Agents" value={data.agents.length}/>
      <Stat label="Knowledge bases" value={data.knowledgeBases.length}
        hint={`${data.documentCount} documents`}/>
      <Stat label="Recordings in Drive" value={company.googleDrive?.connected ? 'yes' : 'no'}
        hint={company.googleDrive?.accountEmail || undefined}/>
    </div>

    <div className="card">
      <Section title="Numbers" help="Each one carries its own provider credentials.">
        {data.numbers.length === 0
          ? <p className="small muted" style={{margin: 0}}>They have not connected a number yet.</p>
          : data.numbers.map(n => <div className="list-row" key={n.id}>
              <div className="list-row-main">
                <strong className="mono">{n.phoneNumber}</strong>
                <div className="small muted">
                  {n.label} · {n.kind === 'whatsapp' ? 'WhatsApp (Meta)' : 'Phone line (Infobip)'} · {n.mode}
                  {n.usePlatformCredentials ? ' · on your credentials' : ''}
                </div>
                {n.statusDetail && <div className="small muted">{n.statusDetail}</div>}
              </div>
              <div className="list-row-actions">
                {n.inboundAgentId && <Badge>inbound agent set</Badge>}
                {n.outboundAgentId && <Badge>outbound agent set</Badge>}
                <Badge tone={n.status === 'verified' ? 'success' : n.status === 'failed' ? 'danger' : 'warning'}>
                  {n.status === 'verified' ? <CheckCircle2 size={12}/> : <XCircle size={12}/>} {n.status}
                </Badge>
              </div>
            </div>)}
      </Section>

      <Section title="Agents">
        {data.agents.length === 0
          ? <p className="small muted" style={{margin: 0}}>No agents created yet.</p>
          : data.agents.map(a => <div className="list-row" key={a.id}>
              <div className="list-row-main">
                <strong>{a.name}</strong>
                <div className="small muted">{a.greeting || 'no opening line'}</div>
              </div>
              <div className="list-row-actions">
                <Badge>{a.mode ?? 'both'}</Badge>
                <Badge tone={a.status === 'live' ? 'success' : ''}>{a.status}</Badge>
              </div>
            </div>)}
      </Section>

      <Section title="Recent calls" help="The last twenty-five, newest first.">
        {data.recentCalls.length === 0
          ? <p className="small muted" style={{margin: 0}}>No calls yet.</p>
          : <div className="table-wrap"><table className="table stack-on-phone">
              <thead><tr><th>When</th><th>Who</th><th>On</th><th>Length</th><th>Result</th></tr></thead>
              <tbody>{data.recentCalls.map(c => <tr key={c.id}>
                <td data-label="When">{formatWhen(c.startedAt)}</td>
                <td data-label="Who" className="mono">{c.counterparty}</td>
                <td data-label="On">{CHANNEL_LABEL[c.channel] ?? c.channel}</td>
                <td data-label="Length" className="mono">{formatDuration(c.durationSeconds)}</td>
                <td data-label="Result">
                  <Badge tone={statusTone(c.status as CallStatus)}>{STATUS_LABEL[c.status as CallStatus] ?? c.status}</Badge>
                </td>
              </tr>)}</tbody>
            </table></div>}
      </Section>

      <CompanyEngine id={id!} company={company} onSaved={load}/>

      <Section title="What they have connected"
        help="Read from their own credentials. No secret is shown — only whether it is set.">
        {(company.channels ?? []).map((channel: ChannelReadiness, index: number) =>
          <div className="list-row" key={channel.id ?? index}>
            <div className="list-row-main">
              <strong>{channel.label}</strong>
              {!channel.connected && channel.missing?.length
                ? <div className="small muted">still needs: {channel.missing.join(', ')}</div>
                : null}
            </div>
            <Badge tone={channel.connected ? 'success' : 'warning'}>
              {channel.connected ? 'connected' : 'incomplete'}
            </Badge>
          </div>)}
      </Section>
    </div>
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// The server itself
// ---------------------------------------------------------------------------

type Health = {status: string; env: string; uptimeSeconds: number;
               capabilities: Record<string, boolean | number | string>};

/** What each capability means, and what to do when it is off. */
const CAPABILITY_NOTES: Record<string, [string, string]> = {
  database: ['Database', 'Supabase is reachable. Without it the service answers webhooks and forgets them.'],
  localStore: ['Local JSON store', 'Rows are in a file beside the app. Fine on a laptop; in production the next deploy erases them.'],
  credentialEncryption: ['Credential encryption', 'CREDENTIALS_SECRET is set, so customers’ provider keys are sealed at rest.'],
  transcoding: ['ffmpeg', 'Recordings are converted so players can seek. Without it audio is stored as it arrived and files are much larger.'],
  objectStorage: ['Object storage', 'Recordings can be stored in Supabase Storage.'],
  googleOAuth: ['Google OAuth', 'Companies can connect their own Drive for recordings.'],
  adminApi: ['ADMIN_API_KEY', 'The server key is set. Super admin sign-in works without it, but it is the fallback for a lost recovery code.'],
  publicBaseUrl: ['PUBLIC_BASE_URL', 'Needed for webhook URLs and carrier media. Without it inbound calls have nowhere to send audio.'],
  agentKey: ['Voice engine key', 'The agent can speak. Without it a call connects to silence.'],
  agentMediaBridge: ['WhatsApp media bridge', 'aiortc is installed, so WhatsApp calls can be answered rather than only logged.'],
  carrierMedia: ['Carrier media', 'Phone-line audio over a websocket. Always available.'],
};

export function AdminHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setHealth(await request<Health>('/api/health', {anonymous: true}));
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not reach the server.'));
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const uptime = (seconds: number) => {
    const h = Math.floor(seconds / 3600);
    return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h >= 1 ? `${h}h` : `${Math.round(seconds / 60)}m`;
  };

  const capabilities = Object.entries(health?.capabilities ?? {});
  // `localStore: true` is a warning, not a success — it is the only inverted
  // one, so it is called out rather than painted green with everything else.
  const tone = (key: string, value: boolean) =>
    key === 'localStore' ? (value ? 'warning' : 'success') : (value ? 'success' : 'warning');

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Server health"
      description="What this deployment can actually do. Everything optional fails quietly, so this is where to look first."
      action={<Button variant="outline" onClick={() => void load()}><RefreshCw size={15}/> Refresh</Button>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}
    {!health && !error ? <Loading label="Checking the server…"/> : null}

    {health && <>
      <div className="stat-grid">
        <Stat label="Status" value={health.status}/>
        <Stat label="Environment" value={health.env}/>
        <Stat label="Uptime" value={uptime(health.uptimeSeconds)}/>
        <Stat label="Calls in progress" value={String(health.capabilities.callsInProgress ?? 0)}/>
        <Stat label="Voice engine" value={String(health.capabilities.agentEngine ?? '—')}/>
      </div>

      <div className="card">
        <Section title="Capabilities" help="Each of these is set by the server's environment.">
          {capabilities
            .filter(([, value]) => typeof value === 'boolean')
            .map(([key, value]) => {
              const [label, note] = CAPABILITY_NOTES[key] ?? [key, ''];
              return <div className="list-row" key={key}>
                <div className="list-row-main">
                  <strong>{label}</strong>
                  {note && <div className="small muted">{note}</div>}
                </div>
                <Badge tone={tone(key, value as boolean)}>
                  {value ? <CheckCircle2 size={12}/> : <XCircle size={12}/>} {value ? 'on' : 'off'}
                </Badge>
              </div>;
            })}
        </Section>
      </div>
    </>}
  </div>;
}


// ---------------------------------------------------------------------------
// The speech engine
// ---------------------------------------------------------------------------

type Engine = {
  id: string; label: string; supported: boolean;
  defaultModel: string; keyHint: string; note: string;
  /** Suggestions, not a limit — the model field takes anything typed. */
  models?: string[];
  /**
   * Implemented and key-testable, but no call has been placed on it from
   * this deployment. Not the same as unsupported, and the difference matters
   * to whoever is about to move their customers onto it.
   */
  unproven?: boolean;
};

type EngineState = {
  engine: string; model: string; keySet: boolean; keyHint: string;
  source: string; engines: Engine[]; supported: boolean;
};

/**
 * Which engine carries the conversations, and the key it runs on.
 *
 * These lived in the server's environment, so rotating a leaked key or trying
 * a newer model meant someone with access to the host — and until they got to
 * it, every call on the platform was failing. The key is write-only here: it
 * is sealed before storage and only its last four characters come back.
 */
export function AdminEngine() {
  const {toast} = useApp();
  const [state, setState] = useState<EngineState | null>(null);
  const [engine, setEngine] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [tested, setTested] = useState<{ok: boolean; detail: string} | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await request<EngineState>('/api/platform/engine');
      setState(body);
      setEngine(body.engine);
      setModel(body.model);
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not read the engine settings.'));
    }
  }, []);
  useEffect(() => {void load()}, [load]);

  if (!state) return <div className="stack">
    <PageHead eyebrow="Platform" title="Speech engine"/>
    {error ? <ErrorNote error={error} onRetry={() => void load()}/> : <Loading/>}
  </div>;

  const chosen = state.engines.find(e => e.id === engine);
  const dirty = engine !== state.engine || model !== state.model || apiKey !== '';

  const save = async () => {
    setBusy(true); setError('');
    try {
      const body = await request<EngineState>('/api/platform/engine', {
        method: 'PUT',
        // An untouched key field means "leave it alone", never "clear it" —
        // the field starts empty because the key is never shown back.
        body: {engine, model, ...(apiKey ? {apiKey} : {})},
      });
      setState(body); setApiKey('');
      toast('Engine settings saved. New calls use them straight away.');
    } catch (cause) {
      setError(errorText(cause, 'That could not be saved.'));
    } finally {setBusy(false)}
  };

  const test = async () => {
    setTesting(true); setTested(null);
    try {
      // The key being typed if there is one, otherwise whatever is stored —
      // so "is the key I saved last month still good?" is answerable.
      setTested(await request<{ok: boolean; detail: string}>('/api/platform/engine/test', {
        method: 'POST', body: {engine, model, apiKey},
      }));
    } catch (cause) {
      setTested({ok: false, detail: errorText(cause, 'The provider could not be reached.')});
    } finally {setTesting(false)}
  };

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Speech engine"
      description="What carries every conversation on the platform, for every company."/>

    {error && <ErrorNote error={error}/>}

    <div className="stat-grid">
      <Stat label="In use" value={state.engine} hint={state.supported ? undefined : 'not supported'}/>
      <Stat label="Model" value={state.model || '—'}/>
      <Stat label="API key" value={state.keySet ? state.keyHint : 'not set'}
        hint={`from ${state.source}`}/>
    </div>

    {!state.keySet && <div className="notice danger">
      No key is set anywhere. Calls will connect and then sit in silence until one is.
    </div>}

    <div className="card">
      <Section title="Engine" help="Only an engine this build can speak to may be selected.">
        <Select label="Provider" value={engine} onChange={setEngine}>
          {state.engines.map(e => <option key={e.id} value={e.id} disabled={!e.supported}>
            {e.label}
            {!e.supported ? ' — not available in this build'
              : e.unproven ? ' — untried on a live call' : ''}
          </option>)}
        </Select>
        {chosen && <div className={'notice small '
          + (!chosen.supported ? 'warning' : chosen.unproven ? 'warning' : '')}>
          {chosen.unproven && <><strong>Not yet proven on a real call.</strong> </>}
          {chosen.note}
        </div>}
      </Section>

      <Section title="Model and key"
        help="Whatever you put here is what every call runs on. The server's own environment variable is only used for a field you leave empty.">
        <TextInput label="Model" value={model} onChange={setModel}
          placeholder={chosen?.defaultModel ?? ''}
          help="Type any model name the provider accepts. A name it does not know fails every call, so use Test below before you rely on it."/>
        {(chosen?.models ?? []).length > 0 && <div className="row wrap" style={{gap: 7}}>
          <span className="small muted">Known to work:</span>
          {(chosen?.models ?? []).map(name => <button key={name} type="button"
            className={'button small ' + (model === name ? '' : 'outline')}
            onClick={() => setModel(name)}>{name}</button>)}
        </div>}

        <TextInput label="API key" value={apiKey} onChange={setApiKey} type="password"
          saved={state.keySet ? state.keyHint.replace(/^…/, '') : undefined}
          placeholder={chosen?.keyHint}
          help={state.keySet
            ? `In use, from ${state.source}. Type a new one to replace it.`
            : chosen?.keyHint}/>

        <div className="row wrap">
          <Button disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? <><Loader2 size={15} className="spin"/> Saving…</> : 'Save'}
          </Button>
          {/* Saving proves nothing: a typo, a revoked key or a retired model
              name all store perfectly and are found out by the first caller,
              who hears silence. */}
          <Button variant="outline" disabled={testing} onClick={() => void test()}>
            {testing ? <><Loader2 size={15} className="spin"/> Asking the provider…</>
              : <><Plug size={15}/> Test this key</>}
          </Button>
          {dirty && <span className="small muted">Takes effect on the next call.</span>}
        </div>

        {tested && <div className={'notice small ' + (tested.ok ? '' : 'danger')}>
          {tested.ok ? <CheckCircle2 size={14}/> : <XCircle size={14}/>} {tested.detail}
        </div>}
      </Section>

      <Section title="Where this is stored"
        help="Beside the super admin's own record, with the same protection. The key is sealed before it is written and no screen or endpoint can read it back.">
        <p className="small muted" style={{margin: 0}}>
          A field left empty falls back to the server's environment variable,
          which is how this platform ran before this screen existed. Anything
          you set here wins over it, for every company that has no key of its
          own.
        </p>
      </Section>
    </div>
  </div>;
}


/**
 * One company's own speech engine.
 *
 * Every company runs on the platform's key until there is a reason to
 * separate one — a customer large enough to want its own billing, or one
 * being moved to a different model ahead of everybody else. Left empty this
 * section does nothing, which is the normal state.
 */
function CompanyEngine({id, company, onSaved}: {
  id: string; company: Record<string, any>; onSaved: () => Promise<void> | void;
}) {
  const {toast} = useApp();
  const [model, setModel] = useState(String(company.engineModel ?? ''));
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const keySet = Boolean(company.engineKeySet);
  const dirty = model !== String(company.engineModel ?? '') || apiKey !== '';

  const save = async (clear = false) => {
    setBusy(true); setError('');
    try {
      await request(`/api/companies/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        body: clear
          ? {engineModel: '', engineApiKey: ''}
          : {engineModel: model, ...(apiKey ? {engineApiKey: apiKey} : {})},
      });
      setApiKey('');
      if (clear) setModel('');
      toast(clear
        ? 'They are back on the platform engine.'
        : 'Saved. Their next call uses it.');
      await onSaved();
    } catch (cause) {
      setError(errorText(cause, 'That could not be saved.'));
    } finally {setBusy(false)}
  };

  return <Section title="Their own speech engine"
    help="Leave empty and they run on the platform's engine, which is how every company runs unless you separate one."
    action={keySet ? <Badge tone="warning">own key</Badge> : <Badge>platform engine</Badge>}>
    <div className="field-grid">
      <TextInput label="Model" value={model} onChange={setModel}
        placeholder="platform default"
        help="A model the provider does not know fails every one of their calls."/>
      <TextInput label="API key" value={apiKey} onChange={setApiKey} type="password"
        placeholder={keySet ? 'leave empty to keep theirs' : 'platform key'}
        help={keySet
          ? 'A key is in place. Type a new one to replace it.'
          : 'Billed to whoever owns this key.'}/>
    </div>
    <div className="row wrap">
      <Button disabled={!dirty || busy} onClick={() => void save()}>
        {busy ? <><Loader2 size={15} className="spin"/> Saving…</> : 'Save'}
      </Button>
      {(keySet || company.engineModel) && <Button variant="outline" disabled={busy}
        onClick={() => void save(true)}>Use the platform engine</Button>}
    </div>
    {error && <div className="notice danger small">{error}</div>}
  </Section>;
}
