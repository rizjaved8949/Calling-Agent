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
  ArrowLeft, Building2, CheckCircle2, CircleAlert, Copy, KeyRound, Loader2, Plus,
  RefreshCw, ServerCog, Trash2, XCircle,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {CopyField, ErrorNote, Loading, Modal, Section, TextInput, useConfirm} from './ui';
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

export function AdminCompanies() {
  const {toast} = useApp();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<{name: string; apiKey: string} | null>(null);
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
                {row.blocker && <div className="small" style={{color: 'var(--warning)', marginTop: 4}}>
                  <CircleAlert size={12}/> {row.blocker}
                </div>}
              </div>
              <div className="list-row-actions">
                <Badge tone={STAGE_TONE[row.stage]}>{row.stage}</Badge>
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
              </div>
            </div>)}
          </div>}

    {data?.store === 'local file' && <div className="notice warning">
      This server is keeping its rows in a local JSON file, not Supabase. That is fine on a
      laptop and wrong in production — the next deploy erases it.
    </div>}

    {adding && <RegisterCompany onClose={() => setAdding(false)}
      onRegistered={(name, apiKey) => {setIssued({name, apiKey}); setAdding(false); void load()}}/>}
  </div>;
}

function RegisterCompany({onClose, onRegistered}: {
  onClose: () => void; onRegistered: (name: string, apiKey: string) => void;
}) {
  const [name, setName] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [countryCode, setCountryCode] = useState('');
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
      {error && <div className="notice danger small">{error}</div>}
    </div>
  </Modal>;
}

// ---------------------------------------------------------------------------
// One company
// ---------------------------------------------------------------------------

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

      <Section title="What they have connected"
        help="Read from their own credentials. No secret is shown — only whether it is set.">
        {Object.entries(company.channels ?? {}).map(([key, value]) => {
          const channel = value as {label?: string; ready?: boolean; missing?: string[]};
          return <div className="list-row" key={key}>
            <div className="list-row-main">
              <strong>{channel.label ?? key}</strong>
              {!channel.ready && channel.missing?.length
                ? <div className="small muted">still needs: {channel.missing.join(', ')}</div>
                : null}
            </div>
            <Badge tone={channel.ready ? 'success' : 'warning'}>
              {channel.ready ? 'ready' : 'incomplete'}
            </Badge>
          </div>;
        })}
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
