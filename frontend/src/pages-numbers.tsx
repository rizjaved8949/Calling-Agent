/**
 * Numbers: connect a phone or WhatsApp number with its own credentials,
 * verify it with the provider, then choose which agent answers and which one
 * calls out.
 *
 * The page is laid out in the order a company actually does this, with the
 * provider console open in another tab: pick the provider, copy each value
 * across (every field says where to find it), and get an immediate yes or no.
 */
import React, {useCallback, useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {
  CheckCircle2, Copy, Loader2, MessageCircle, Phone, Plus, RefreshCw, Trash2, X, XCircle,
  PhoneIncoming, PhoneOutgoing, Pencil, CircleDashed,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {CopyField, Loading, Modal, Section, TextInput, useConfirm} from './ui';
import {useApp} from './app-context';
import {workspace, type Agent} from './lib/api/workspace';
import {
  numbers as api, numberName, type NumberCreate, type NumberKind, type NumberMode, type PhoneNumber,
} from './lib/api/numbers';

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

const MODE_LABEL: Record<NumberMode, string> = {
  inbound: 'Inbound only', outbound: 'Outbound only', both: 'Inbound and outbound',
};

function StatusBadge({n}: {n: PhoneNumber}) {
  if (n.status === 'verified') return <Badge tone="success">Verified</Badge>;
  if (n.status === 'failed') return <Badge tone="danger">Not verified</Badge>;
  return <Badge tone="warning">Pending</Badge>;
}

// ---------------------------------------------------------------------------
// Connecting (and editing) a number
// ---------------------------------------------------------------------------

type Draft = Required<Omit<NumberCreate, 'label' | 'usePlatformCredentials'>> & {
  label: string; usePlatformCredentials: boolean;
};

const EMPTY: Draft = {
  kind: 'sim', label: '', phoneNumber: '', mode: 'both', usePlatformCredentials: false,
  metaPhoneNumberId: '', wabaId: '', accessToken: '', appSecret: '', verifyToken: '',
  infobipApiKey: '', infobipBaseUrl: '', infobipCallsConfigurationId: '',
};

function ConnectNumber({existing, platformAllowed, onDone, onClose}: {
  existing?: PhoneNumber; platformAllowed: boolean;
  onDone: (n: PhoneNumber) => void; onClose: () => void;
}) {
  const editing = Boolean(existing);
  const [step, setStep] = useState<'kind' | 'form'>(editing ? 'form' : 'kind');
  const [d, setD] = useState<Draft>(() => existing ? {
    ...EMPTY, kind: existing.kind, label: existing.label, phoneNumber: existing.phoneNumber,
    mode: existing.mode, usePlatformCredentials: existing.usePlatformCredentials,
    metaPhoneNumberId: existing.credentials.metaPhoneNumberId ?? '',
    wabaId: existing.credentials.wabaId ?? '',
    infobipBaseUrl: existing.credentials.infobipBaseUrl ?? '',
    infobipCallsConfigurationId: existing.credentials.infobipCallsConfigurationId ?? '',
  } : EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<PhoneNumber | null>(null);
  const set = (patch: Partial<Draft>) => setD(old => ({...old, ...patch}));
  const wa = d.kind === 'whatsapp';
  const platform = d.usePlatformCredentials;
  const secretHint = (v: string | null | undefined) => editing && v ? `Saved (${v}). Leave blank to keep it.` : undefined;

  const missing = (): string[] => {
    const need: [string, string][] = [['Phone number', d.phoneNumber]];
    if (!platform && !editing) {
      if (wa) need.push(['Phone number ID', d.metaPhoneNumberId], ['WhatsApp Business Account ID', d.wabaId],
        ['Access token', d.accessToken], ['App secret', d.appSecret]);
      else need.push(['API key', d.infobipApiKey], ['Base URL', d.infobipBaseUrl]);
    }
    return need.filter(([, v]) => !v.trim()).map(([k]) => k);
  };

  const submit = async () => {
    const gaps = missing();
    if (gaps.length) {setError('Still needed: ' + gaps.join(', ') + '.'); return}
    setBusy(true); setError('');
    try {
      const shared = wa
        ? {metaPhoneNumberId: d.metaPhoneNumberId, wabaId: d.wabaId, accessToken: d.accessToken,
           appSecret: d.appSecret, verifyToken: d.verifyToken}
        : {infobipApiKey: d.infobipApiKey, infobipBaseUrl: d.infobipBaseUrl,
           infobipCallsConfigurationId: d.infobipCallsConfigurationId};
      const saved = existing
        ? await api.update(existing.id, {
            label: d.label, phoneNumber: d.phoneNumber, mode: d.mode,
            usePlatformCredentials: d.usePlatformCredentials, ...shared,
          })
        : await api.create({
            kind: d.kind, label: d.label, phoneNumber: d.phoneNumber, mode: d.mode,
            usePlatformCredentials: d.usePlatformCredentials, ...shared,
          });
      setResult(saved);
      onDone(saved);
    } catch (cause) {
      setError(errorText(cause, 'That number could not be saved.'));
    } finally {setBusy(false)}
  };

  if (result) {
    const ok = result.status === 'verified';
    return <Modal width={660} title={ok ? 'Number connected' : 'Saved, but not verified'} onClose={onClose}>
      <div className="stack">
        <div className="row" style={{gap: 12, alignItems: 'flex-start'}}>
          {ok ? <CheckCircle2 size={28} color="var(--success)"/> : <XCircle size={28} color="var(--destructive)"/>}
          <div>
            <strong>{numberName(result)}</strong>
            <p className="small muted" style={{margin: '4px 0 0'}}>{result.statusDetail}</p>
          </div>
        </div>
        {result.webhookUrl && <>
          <CopyField label={wa ? 'Callback URL — paste into your Meta app (WhatsApp → Configuration)' : 'Call events URL — paste into your Infobip Calls application'} value={result.webhookUrl}/>
          {wa && <p className="help">Subscribe the webhook to <b>messages</b> and <b>calls</b>, and use the same verify token you entered here.</p>}
        </>}
        {ok
          ? <div className="notice">Next: create an agent and give it a knowledge base, then come back and pick it as this number’s {result.mode === 'outbound' ? 'outbound' : 'inbound'} agent.</div>
          : <div className="notice danger">Fix the credentials and press “Edit” on the number, or “Verify again” once you have fixed it at the provider.</div>}
        <div className="row" style={{justifyContent: 'flex-end'}}>
          {ok && <Button variant="outline" to="/app/agents">Create an agent</Button>}
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </Modal>;
  }

  if (step === 'kind') {
    const choice = (kind: NumberKind, Icon: typeof Phone, title: string, body: string) =>
      <button type="button" className={'preset-card ' + (d.kind === kind ? 'selected' : '')}
        aria-pressed={d.kind === kind} onClick={() => set({kind})}>
        <Icon size={22}/><strong>{title}</strong><span>{body}</span>
      </button>;
    return <Modal width={660} title="Connect a number" onClose={onClose}>
      <div className="stack">
        <p className="muted small" style={{margin: 0}}>Where did you get the number?</p>
        <div className="preset-grid">
          {choice('sim', Phone, 'Phone line (Infobip)', 'A SIM / voice number bought in your Infobip account. Customers call it like any phone number.')}
          {choice('whatsapp', MessageCircle, 'WhatsApp (Meta)', 'A WhatsApp Business number from Meta, for WhatsApp calls and messages.')}
        </div>
        <div className="row" style={{justifyContent: 'flex-end'}}>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => setStep('form')}>Continue</Button>
        </div>
      </div>
    </Modal>;
  }

  return <Modal width={660} title={editing ? 'Edit number' : wa ? 'WhatsApp number' : 'Phone line'} onClose={onClose}>
    <div className="stack">
      <div className="field-grid">
        <TextInput label="Phone number" required value={d.phoneNumber} onChange={v => set({phoneNumber: v})}
          placeholder="+92 300 1112222" help="With the country code, as callers dial it."/>
        <TextInput label="Name" value={d.label} onChange={v => set({label: v})} placeholder="Support line"/>
      </div>

      <div className="field">
        <label>What is this number for?</label>
        <div className="row wrap" style={{gap: 8, minHeight: 44, alignItems: 'center'}}>
          {(['inbound', 'outbound', 'both'] as NumberMode[]).map(m =>
            <button key={m} type="button" className={'button small ' + (d.mode === m ? '' : 'outline')}
              onClick={() => set({mode: m})}>
              {m === 'inbound' ? <PhoneIncoming size={14}/> : m === 'outbound' ? <PhoneOutgoing size={14}/> : null}
              {MODE_LABEL[m]}
            </button>)}
        </div>
        <div className="help">Inbound: the agent answers anyone who calls. Outbound: the agent (or an employee from the dialer) calls people from this number.</div>
      </div>

      {platformAllowed && <label className="check">
        <input type="checkbox" checked={d.usePlatformCredentials}
          onChange={e => set({usePlatformCredentials: e.target.checked})}/>
        <span><b>Use the platform’s own {wa ? 'Meta' : 'Infobip'} credentials</b>
          <div className="help">Allowed for this account by the platform operator. Leave
            the credential fields below empty and the server's own are used.</div></span>
      </label>}

      {!platform && (wa ? <>
        <div className="notice small">In <b>Meta for Developers → your app → WhatsApp → API Setup</b> you will find the first two values. Use a <b>System User</b> token (Business Settings → System users) so it does not expire in 24 hours.</div>
        <div className="field-grid">
          <TextInput label="Phone number ID" required={!editing} value={d.metaPhoneNumberId} onChange={v => set({metaPhoneNumberId: v})}
            placeholder="1234567890" help="API Setup → “Phone number ID” (not the phone number itself)."/>
          <TextInput label="WhatsApp Business Account ID" required={!editing} value={d.wabaId} onChange={v => set({wabaId: v})}
            placeholder="1029384756" help="API Setup → “WhatsApp Business Account ID”."/>
        </div>
        <TextInput label="Access token" required={!editing} type="password" value={d.accessToken} onChange={v => set({accessToken: v})}
          placeholder="EAAG…" help={secretHint(existing?.credentials.accessToken) ?? 'Permanent System User token with whatsapp_business_messaging and whatsapp_business_management.'}/>
        <div className="field-grid">
          <TextInput label="App secret" required={!editing} type="password" value={d.appSecret} onChange={v => set({appSecret: v})}
            help={secretHint(existing?.credentials.appSecret) ?? 'App settings → Basic → App secret. Used to check every webhook really came from Meta.'}/>
          <TextInput label="Webhook verify token" type="password" value={d.verifyToken} onChange={v => set({verifyToken: v})}
            help={secretHint(existing?.credentials.verifyToken) ?? 'Any phrase you choose. Type the same one into Meta when you add the callback URL.'}/>
        </div>
      </> : <>
        <div className="notice small">In the <b>Infobip portal</b>, your base URL and API keys are on the home page (Developers → API keys). The key needs the <b>Voice / Calls</b> scope.</div>
        <div className="field-grid">
          <TextInput label="Base URL" required={!editing} value={d.infobipBaseUrl} onChange={v => set({infobipBaseUrl: v})}
            placeholder="xxxxx.api.infobip.com" help="Shown at the top of the Infobip portal home page."/>
          <TextInput label="API key" required={!editing} type="password" value={d.infobipApiKey} onChange={v => set({infobipApiKey: v})}
            help={secretHint(existing?.credentials.infobipApiKey)}/>
        </div>
        <TextInput label="Calls configuration ID" value={d.infobipCallsConfigurationId}
          onChange={v => set({infobipCallsConfigurationId: v})}
          help="Optional. Channels → Voice → Calls configurations. Leave empty to use your account default."/>
      </>)}

      {error && <div className="notice danger">{error}</div>}
      <div className="row" style={{justifyContent: 'flex-end'}}>
        {!editing && <Button variant="secondary" onClick={() => setStep('kind')}>Back</Button>}
        <Button onClick={() => void submit()} disabled={busy}>
          {busy ? <><Loader2 size={15} className="spin"/> Checking with {wa ? 'Meta' : 'Infobip'}…</> : editing ? 'Save and verify' : 'Connect and verify'}
        </Button>
      </div>
    </div>
  </Modal>;
}

// ---------------------------------------------------------------------------
// One number
// ---------------------------------------------------------------------------

function NumberCard({n, agents, canManage, onChange, onEdit, onRemove}: {
  n: PhoneNumber; agents: Agent[]; canManage: boolean;
  onChange: (n: PhoneNumber | null) => void; onEdit: () => void; onRemove: () => void;
}) {
  const {toast} = useApp();
  const [busy, setBusy] = useState('');
  const wa = n.kind === 'whatsapp';
  const inbound = n.mode !== 'outbound';
  const outbound = n.mode !== 'inbound';
  const verified = n.status === 'verified';

  const patch = async (what: string, body: Parameters<typeof api.update>[1]) => {
    setBusy(what);
    try {onChange(await api.update(n.id, body)); toast('Saved')}
    catch (cause) {toast(errorText(cause, 'That could not be saved.'))}
    finally {setBusy('')}
  };
  const verify = async () => {
    setBusy('verify');
    try {
      const v = await api.verify(n.id);
      onChange(v);
      toast(v.status === 'verified' ? 'Verified' : 'Still not verified');
    } catch (cause) {toast(errorText(cause, 'Could not verify.'))}
    finally {setBusy('')}
  };


  const agentPicker = (slot: 'inbound' | 'outbound') => {
    const value = (slot === 'inbound' ? n.inboundAgentId : n.outboundAgentId) ?? '';
    const fits = agents.filter(a => a.mode === 'both' || a.mode === slot);
    return <div className="field">
      <label>{slot === 'inbound' ? <><PhoneIncoming size={13}/> Answers incoming calls</> : <><PhoneOutgoing size={13}/> Makes outgoing calls</>}</label>
      <select className="select" value={value} disabled={!canManage || !verified || Boolean(busy)}
        onChange={e => void patch(slot, slot === 'inbound' ? {inboundAgentId: e.target.value} : {outboundAgentId: e.target.value})}>
        <option value="">{slot === 'inbound' ? 'Nobody — calls are declined' : 'No agent (dialer only)'}</option>
        {fits.map(a => <option key={a.id} value={a.id}>{a.name}{a.status !== 'live' ? ` (${a.status})` : ''}</option>)}
      </select>
      {verified && fits.length === 0 && <div className="help">No {slot} agent yet. <Link to="/app/agents">Create one</Link>.</div>}
    </div>;
  };

  return <div className="card stack">
    <div className="row between" style={{alignItems: 'flex-start', flexWrap: 'wrap'}}>
      <div className="row" style={{alignItems: 'flex-start'}}>
        <div className="brand-mark" style={{width: 42, height: 42}}>{wa ? <MessageCircle size={20}/> : <Phone size={20}/>}</div>
        <div>
          <strong style={{fontSize: '1.05rem'}}>{n.phoneNumber}</strong>
          <div className="small muted">{n.label} · {wa ? 'WhatsApp (Meta)' : 'Phone line (Infobip)'} · {MODE_LABEL[n.mode]}{n.usePlatformCredentials ? ' · platform credentials' : ''}</div>
        </div>
      </div>
      <div className="row wrap">
        <StatusBadge n={n}/>
        {canManage && <>
          <Button small variant="outline" disabled={Boolean(busy)} onClick={() => void verify()}>
            {busy === 'verify' ? <Loader2 size={14} className="spin"/> : <RefreshCw size={14}/>} Verify again
          </Button>
          <Button small variant="outline" disabled={Boolean(busy)} onClick={onEdit}><Pencil size={14}/> Edit</Button>
          <Button small variant="outline" disabled={Boolean(busy)} onClick={onRemove}
            title="Disconnect this number"><Trash2 size={14}/></Button>
        </>}
      </div>
    </div>

    {n.statusDetail && <div className={'notice small ' + (n.status === 'failed' ? 'danger' : n.status === 'pending' ? 'warning' : '')}>{n.statusDetail}</div>}

    {!verified
      ? <p className="small muted" style={{margin: 0}}><CircleDashed size={13}/> Agents can be assigned once the provider has accepted this number’s credentials.</p>
      : <div className="field-grid">
          {inbound && agentPicker('inbound')}
          {outbound && agentPicker('outbound')}
        </div>}

    {wa && verified && inbound && <label className={'check' + (canManage ? '' : ' disabled')}>
      <input type="checkbox" checked={n.autoReply} disabled={!canManage || Boolean(busy)}
        onChange={e => void patch('auto', {autoReply: e.target.checked})}/>
      <span><b>Answer WhatsApp messages too</b>
        <div className="help">The inbound agent replies to text messages on this number,
          not just calls. Needs a knowledge base, or it has nothing to answer from.</div></span>
    </label>}

    {n.webhookUrl && <details>
      <summary className="small muted" style={{cursor: 'pointer'}}>Provider setup ({wa ? 'Meta webhook' : 'Infobip events URL'})</summary>
      <div style={{marginTop: 10}}><CopyField label={wa ? 'Callback URL' : 'Call events URL'} value={n.webhookUrl}/></div>
    </details>}
  </div>;
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export function NumbersScreen() {
  const {canManage} = useApp();
  const [list, setList] = useState<PhoneNumber[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [platformAllowed, setPlatformAllowed] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<PhoneNumber | 'new' | null>(null);
  const [confirm, confirmDialog] = useConfirm();

  const load = useCallback(() => {
    api.list().then(b => {setList(b.numbers); setPlatformAllowed(b.platformCredentialsAllowed)})
      .catch(c => setError(errorText(c, 'Could not load your numbers.')));
    workspace.agents().then(b => setAgents(b.agents)).catch(() => {});
  }, []);
  useEffect(() => {load()}, [load]);

  const replace = (id: string, next: PhoneNumber | null) =>
    setList(old => (old ?? []).flatMap(x => x.id !== id ? [x] : next ? [next] : []));

  const verifiedCount = (list ?? []).filter(n => n.status === 'verified').length;
  const assigned = (list ?? []).some(n => n.inboundAgentId || n.outboundAgentId);

  return <div className="stack">
    <PageHead eyebrow="Setup" title="Numbers"
      description="Connect each number with its own Infobip or Meta credentials. Once verified, choose which agent answers it and which one calls out."
      action={canManage ? <Button onClick={() => setEditing('new')}><Plus size={16}/> Connect a number</Button> : undefined}/>

    <div className="card">
      <div className="row wrap" style={{gap: 22}}>
        {[
          ['Connect a number', (list ?? []).length > 0],
          ['Verified by provider', verifiedCount > 0],
          ['Agent with knowledge', agents.length > 0],
          ['Agent assigned', assigned],
        ].map(([label, done], i) => <div className="row small" key={String(label)} style={{gap: 8}}>
          {done ? <CheckCircle2 size={17} color="var(--success)"/> : <span className="badge">{i + 1}</span>}
          <span className={done ? '' : 'muted'}>{label as string}</span>
        </div>)}
      </div>
    </div>

    {error && <div className="notice danger">{error}</div>}
    {list === null && !error ? <div className="small muted">Loading…</div>
      : list && list.length === 0
        ? <Empty icon={Phone} title="No numbers yet"
            body="Buy a number in Infobip or get a WhatsApp Business number from Meta, then connect it here with its credentials."
            action={canManage ? <Button onClick={() => setEditing('new')}>Connect a number</Button> : undefined}/>
        : <div className="stack">{(list ?? []).map(n =>
            <NumberCard key={n.id} n={n} agents={agents} canManage={canManage}
              onChange={next => replace(n.id, next)} onEdit={() => setEditing(n)}
              onRemove={() => confirm({
                title: 'Disconnect this number?',
                body: <>Calls to <b>{n.phoneNumber}</b> stop being answered and it can no
                  longer place calls. Its stored credentials are deleted — you would have to
                  paste them again. Calls already made keep their records.</>,
                confirmLabel: 'Disconnect number',
                onConfirm: async () => {await api.remove(n.id); replace(n.id, null)},
              })}/>)}</div>}

    {editing && <ConnectNumber
      existing={editing === 'new' ? undefined : editing}
      platformAllowed={platformAllowed}
      onClose={() => setEditing(null)}
      onDone={saved => setList(old => {
        const rest = (old ?? []).filter(x => x.id !== saved.id);
        return editing === 'new' ? [...rest, saved] : (old ?? []).map(x => x.id === saved.id ? saved : x);
      })}/>}
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// The dashboard's setup checklist, read from the real records
// ---------------------------------------------------------------------------

export function SetupChecklist() {
  const [state, setState] = useState<{numbers: PhoneNumber[]; agents: Agent[]; docs: number} | null>(null);
  useEffect(() => {
    Promise.all([
      api.list().then(b => b.numbers).catch(() => [] as PhoneNumber[]),
      workspace.agents().then(b => b.agents).catch(() => [] as Agent[]),
      workspace.documents().then(b => b.documents.length).catch(() => 0),
    ]).then(([numbers, agents, docs]) => setState({numbers, agents, docs}));
  }, []);
  if (!state) return null;
  const steps: [string, string, boolean, string][] = [
    ['Connect a number', 'Enter the Infobip or Meta credentials for a number you own.', state.numbers.length > 0, '/app/numbers'],
    ['Get it verified', 'The provider confirms the credentials work.', state.numbers.some(n => n.status === 'verified'), '/app/numbers'],
    ['Create an agent', 'Inbound, outbound or both, with its greeting and persona.', state.agents.length > 0, '/app/agents'],
    ['Give it knowledge', 'Upload the documents it should answer from.', state.docs > 0, '/app/knowledge'],
    ['Put the agent on the number', 'Choose who answers and who calls out.', state.numbers.some(n => n.inboundAgentId || n.outboundAgentId), '/app/numbers'],
  ];
  const done = steps.filter(s => s[2]).length;
  if (done === steps.length) return null;
  return <div className="card stack">
    <div className="row between">
      <h2 style={{margin: 0}}>Get your calling agent live</h2>
      <Badge tone="warning">{done} of {steps.length} done</Badge>
    </div>
    <div className="meter"><span style={{width: `${(done / steps.length) * 100}%`}}/></div>
    {steps.map(([title, body, ok, to], i) => <div className="row between" key={title} style={{gap: 12}}>
      <div className="row" style={{alignItems: 'flex-start'}}>
        {ok ? <CheckCircle2 size={19} color="var(--success)"/> : <span className="badge">{i + 1}</span>}
        <div><strong className={ok ? 'muted' : ''}>{title}</strong><div className="small muted">{body}</div></div>
      </div>
      {!ok && <Button small to={to}>Do this</Button>}
    </div>)}
  </div>;
}
