/**
 * Settings, with every field connected to something.
 *
 * The screen this replaces offered a time zone, business hours, an accent
 * colour, a retention period, three "how calls are handled" preset groups and
 * a plan picker. Against the real backend none of those was stored: the save
 * went through, the toast said "Saved", and the value was gone on reload. What
 * is here is what `PATCH /api/companies/me` and the Google routes actually
 * keep, and nothing else.
 *
 * Credentials are deliberately not here any more. They belong to a *number*
 * now, each with its own, so they live on the Numbers page beside the number
 * they belong to.
 */
import {useCallback, useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {HardDrive, Loader2, RefreshCw, ShieldCheck, Trash2} from 'lucide-react';
import {Badge, Button, PageHead, Tabs} from './app';
import {useApp} from './app-context';
import {CopyField, ErrorNote, Loading, Section, Select, TextInput, useConfirm} from './ui';
import {request} from './lib/api/http';

type Company = {
  phoneNumberId: string;
  name: string;
  language: string | null;
  agentGreeting: string | null;
  persona: string | null;
  ttsVoice: string | null;
  recordCalls: boolean;
  autoReply: boolean;
  googleDrive: {
    connected: boolean; accountEmail: string | null; folderName: string | null;
    sheetLink: string | null;
  };
  webhookUrl?: string;
};

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

const TABS = ['Company', 'Default agent', 'Recordings', 'Your data'];

export function SettingsScreen() {
  const {t, toast, canManage} = useApp();
  const [tab, setTab] = useState(TABS[0]);
  const [company, setCompany] = useState<Company | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setCompany(await request<Company>('/api/companies/me'));
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not load your settings.'));
    }
  }, []);
  useEffect(() => {void load()}, [load]);

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true); setError('');
    try {
      setCompany(await request<Company>('/api/companies/me', {method: 'PATCH', body: patch}));
      toast(t('Saved.'));
    } catch (cause) {
      setError(errorText(cause, 'That could not be saved.'));
      await load();
    } finally {setSaving(false)}
  };

  if (!company) return <div className="stack">
    <PageHead eyebrow="Settings" title="Settings"/>
    {error ? <ErrorNote error={error} onRetry={() => void load()}/> : <Loading/>}
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Settings" title="Settings"
      description="Your company details, the fallback agent, and where recordings are kept."
      action={saving ? <Badge>{t('Saving…')}</Badge> : undefined}/>
    <Tabs items={TABS} active={tab} onChange={setTab}/>
    {error && <ErrorNote error={error}/>}

    {tab === 'Company' && <CompanyTab company={company} save={save} canManage={canManage}/>}
    {tab === 'Default agent' && <DefaultAgentTab company={company} save={save} canManage={canManage}/>}
    {tab === 'Recordings' && <RecordingsTab company={company} save={save} canManage={canManage} reload={load}/>}
    {tab === 'Your data' && <DataTab company={company}/>}
  </div>;
}

type TabProps = {
  company: Company;
  save: (patch: Record<string, unknown>) => Promise<void>;
  canManage: boolean;
};

function CompanyTab({company, save, canManage}: TabProps) {
  const [name, setName] = useState(company.name);
  const [language, setLanguage] = useState(company.language ?? '');
  const dirty = name !== company.name || language !== (company.language ?? '');

  return <div className="card">
    <Section title="Your company">
      <TextInput label="Company name" value={name} onChange={setName} disabled={!canManage}
        help="Shown in your workspace and on your exported call records."/>
      <TextInput label="Language your agent starts in" value={language} onChange={setLanguage}
        disabled={!canManage} placeholder="Urdu, English…"
        help="Only a default. The agent follows whatever language the caller speaks, and each agent can override this."/>
      {canManage && <div className="row">
        <Button disabled={!dirty} onClick={() => void save({name, language})}>Save</Button>
      </div>}
    </Section>
    <Section title="Numbers and credentials">
      <p className="small muted" style={{margin: 0}}>
        Each number carries its own Meta or Infobip credentials, so they are set on the
        number itself rather than here.
      </p>
      <div className="row"><Link className="button outline" to="/app/numbers">Go to Numbers</Link></div>
    </Section>
  </div>;
}

/**
 * The company's own persona — the bottom of the chain in `services/routing.py`.
 * A company whose numbers all have agents never reaches it; one that has not
 * built an agent yet answers from this on every number.
 */
function DefaultAgentTab({company, save, canManage}: TabProps) {
  const [persona, setPersona] = useState(company.persona ?? '');
  const [greeting, setGreeting] = useState(company.agentGreeting ?? '');
  const [voice, setVoice] = useState(company.ttsVoice ?? '');
  const dirty = persona !== (company.persona ?? '') || greeting !== (company.agentGreeting ?? '')
    || voice !== (company.ttsVoice ?? '');

  return <div className="card">
    <Section title="The fallback agent"
      help="Used on a number that has no agent of its own. Build proper agents under Agents — this is only the safety net.">
      <TextInput label="Opening line" value={greeting} onChange={setGreeting} disabled={!canManage}
        placeholder="Thank you for calling. How can I help?"/>
      <TextInput label="Who it answers as" rows={5} value={persona} onChange={setPersona}
        disabled={!canManage} placeholder="You are the assistant for Acme…"/>
      <TextInput label="Voice" value={voice} onChange={setVoice} disabled={!canManage}
        placeholder="leave empty for the default"/>
      {canManage && <div className="row">
        <Button disabled={!dirty}
          onClick={() => void save({persona, agentGreeting: greeting, ttsVoice: voice})}>Save</Button>
      </div>}
    </Section>
    <Section title="WhatsApp messages">
      <p className="small muted" style={{margin: 0}}>
        Whether the agent answers incoming WhatsApp messages by itself is set per number,
        since one of your numbers may be staffed by people and another by the agent.
      </p>
      <div className="row"><Link className="button outline" to="/app/numbers">Set it per number</Link></div>
    </Section>
  </div>;
}

function RecordingsTab({company, save, canManage, reload}: TabProps & {reload: () => Promise<void>}) {
  const {toast} = useApp();
  const [confirm, confirmDialog] = useConfirm();
  const [busy, setBusy] = useState('');
  const drive = company.googleDrive;

  const connect = async () => {
    setBusy('connect');
    try {
      const body = await request<{url: string}>('/api/google/connect', {
        method: 'POST', query: {returnTo: window.location.href},
      });
      // The company approves in their own Google account and comes back here.
      window.location.assign(body.url);
    } catch (cause) {
      toast(errorText(cause, 'Google Drive could not be connected.'));
      setBusy('');
    }
  };

  const disconnect = () => confirm({
    title: 'Disconnect Google Drive?',
    body: <>New recordings stay on this platform instead of being copied to your Drive.
      Recordings already in your Drive are untouched and stay yours.</>,
    confirmLabel: 'Disconnect',
    onConfirm: async () => {await request('/api/google/disconnect', {method: 'POST'}); await reload()},
  });

  return <div className="card">
    <Section title="Recording calls">
      <label className={'check' + (canManage ? '' : ' disabled')}>
        <input type="checkbox" checked={company.recordCalls} disabled={!canManage}
          onChange={e => void save({recordCalls: e.target.checked})}/>
        <span>
          <b>Record calls</b>
          <div className="help">
            Off from now on means new calls are not recorded. Recordings already stored stay
            until you delete them from the call.
          </div>
        </span>
      </label>
      <div className="notice small">
        Where you operate may require you to tell callers they are being recorded. If so,
        put it in the agent's opening line.
      </div>
    </Section>

    <Section title="Google Drive"
      help="Every finished recording is also copied into a folder in your own Google account, under your own storage, so you keep them whatever happens here."
      action={<Badge tone={drive.connected ? 'success' : ''}>
        {drive.connected ? 'Connected' : 'Not connected'}
      </Badge>}>
      {drive.connected
        ? <>
            <div className="row between"><span className="small muted">Account</span>
              <span className="small mono">{drive.accountEmail}</span></div>
            <div className="row between"><span className="small muted">Folder</span>
              <span className="small">{drive.folderName || 'Call recordings'}</span></div>
            {drive.sheetLink && <div className="row">
              <a className="button outline small" href={drive.sheetLink} target="_blank" rel="noreferrer">
                Open the call-record sheet
              </a>
            </div>}
            {canManage && <div className="row">
              <Button variant="outline" onClick={disconnect}>
                <Trash2 size={14}/> Disconnect Drive
              </Button>
            </div>}
          </>
        : <div className="row wrap">
            <Button disabled={!canManage || busy === 'connect'} onClick={() => void connect()}>
              {busy === 'connect' ? <Loader2 size={15} className="spin"/> : <HardDrive size={15}/>}
              Connect Google Drive
            </Button>
            <span className="small muted">You will be asked to sign in and approve one folder.</span>
          </div>}
    </Section>
    {confirmDialog}
  </div>;
}

function DataTab({company}: {company: Company}) {
  const [busy, setBusy] = useState(false);
  const {toast} = useApp();

  const exportCalls = async () => {
    setBusy(true);
    try {
      const {requestBlob, saveBlob} = await import('./lib/api/http');
      const {blob, filename} = await requestBlob('/api/exports/calls.xlsx');
      saveBlob(blob, filename);
    } catch (cause) {
      toast(errorText(cause, 'The export could not be produced.'));
    } finally {setBusy(false)}
  };

  return <div className="card">
    <Section title="Take your records with you"
      help="Every call, with its number, length, result and which knowledge base answered it.">
      <div className="row wrap">
        <Button variant="outline" disabled={busy} onClick={() => void exportCalls()}>
          {busy ? <Loader2 size={15} className="spin"/> : <RefreshCw size={15}/>} Download all calls (Excel)
        </Button>
      </div>
    </Section>

    <Section title="Deleting things"
      help="Each of these is done where the thing lives, so you can see exactly what goes.">
      <div className="stack small">
        <div>A <b>recording</b> — on the call, in History. The call record stays.</div>
        <div>A <b>call</b> and its recording — on the call, in History.</div>
        <div>A <b>document</b> — on its knowledge base.</div>
        <div>A <b>number</b> and its stored credentials — on the Numbers page.</div>
      </div>
      <div className="row wrap">
        <Link className="button outline" to="/app/recordings">Call history</Link>
        <Link className="button outline" to="/app/knowledge">Knowledge</Link>
        <Link className="button outline" to="/app/numbers">Numbers</Link>
      </div>
    </Section>

    <Section title="Closing the account"
      help="There is no self-service button for this, on purpose: it would delete every call and recording for everyone in the company at one click.">
      <p className="small muted" style={{margin: 0}}>
        Ask the platform operator to close this account. Quote your account id so the right
        one is removed.
      </p>
      <CopyField label="Account id" value={company.phoneNumberId}/>
    </Section>

    <Section title="Who can see what">
      <div className="row" style={{gap: 10, alignItems: 'flex-start'}}>
        <ShieldCheck size={18}/>
        <p className="small muted" style={{margin: 0}}>
          Your provider credentials are encrypted before they are stored and no screen or
          endpoint can read one back — only the last four characters, so you can tell which
          value you pasted. Staff you invite can place and answer calls but cannot see
          credentials or change the team.
        </p>
      </div>
      <div className="row"><Link className="button outline" to="/app/team">Manage the team</Link></div>
    </Section>
  </div>;
}
