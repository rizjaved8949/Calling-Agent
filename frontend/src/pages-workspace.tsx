/**
 * Agents, knowledge bases, call setups, and the team.
 *
 * Only reachable in a LIVE build — see the routing in app.tsx — because a
 * fixture store has no honest way to model "this number answers from this
 * base", which is the entire point of these screens. They talk straight to
 * `lib/api/workspace.ts` rather than through the big mock/live facade.
 */
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Link, useNavigate, useParams, useLocation} from 'react-router-dom';
import {
  Mic2, BrainCircuit, Route as RouteIcon, Users, Plus, Trash2, Upload, FileText,
  PhoneIncoming, PhoneOutgoing, Loader2, Copy, CheckCircle2, X, Star,
} from 'lucide-react';
import {Badge, Button, Empty, Field, PageHead} from './app';
import {useApp} from './app-context';
import {
  workspace, type Agent, type KnowledgeBase, type KbDocument, type CallSetup,
  type Channel, type TeamMember, type TeamInvite, type WireLookupCall,
} from './lib/api/workspace';
import {friendlyAuthError, loginWithEmail, acceptInvite, peekInvite} from './lib/api/auth';
import {setAuthToken} from './lib/api';
import {auth as firebaseAuth} from './lib/firebase';
import {createUserWithEmailAndPassword} from 'firebase/auth';
import {LiveCalls} from './pages-live';
import {ErrorNote, Loading, Section, Select, TextInput, useConfirm} from './ui';

/** The fields the agent form edits as a draft, saved together on Save. */
const FORM_FIELDS = [
  'name', 'greeting', 'roleDescription', 'language', 'ttsVoice', 'toneNotes',
  'escalationRules',
] as const satisfies readonly (keyof Agent)[];

const CHANNEL_LABEL: Record<Channel, string> = {
  PHONE: 'Phone line', WHATSAPP_CALL: 'WhatsApp calling', WHATSAPP_MESSAGE: 'WhatsApp messaging',
};

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function Modal({title, onClose, children}: {title: string; onClose: () => void; children: React.ReactNode}) {
  return <div className="modal-backdrop" onClick={onClose}>
    <div className="modal" onClick={e => e.stopPropagation()}>
      <div className="row between"><h2>{title}</h2>
        <button className="button outline small" onClick={onClose}><X size={14}/></button></div>
      {children}
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export function AgentsScreen() {
  const {t, toast, canManage} = useApp();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    workspace.agents().then(b => setAgents(b.agents)).catch(c => setError(errorText(c, 'Could not load agents.')));
  }, []);
  useEffect(() => { load() }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      const agent = await workspace.createAgent({name: name.trim()});
      toast(t('Agent created'));
      setCreating(false); setName('');
      navigate('/app/agents/' + agent.id);
    } catch (cause) {
      setError(errorText(cause, 'Could not create that agent.'));
    } finally { setBusy(false) }
  };

  return <div className="stack">
    <PageHead eyebrow="Build" title="Agents"
      description="Each agent has its own persona and knowledge base, and answers calls, makes them, or both. Put an agent on a number from the Numbers page."
      action={canManage ? <Button onClick={() => setCreating(true)}><Plus size={16}/>{t('New agent')}</Button> : undefined}/>
    {error && <div className="notice danger">{error}</div>}
    {agents === null ? <div className="small muted">{t('Loading…')}</div>
      : agents.length === 0
        ? <Empty icon={Mic2} title="No agents yet" body="Create an agent to start shaping its voice and what it knows."
            action={canManage ? <Button onClick={() => setCreating(true)}>{t('New agent')}</Button> : undefined}/>
        : <div className="grid cols-3">
            {agents.map(agent => <Link to={'/app/agents/' + agent.id} className="card lift" key={agent.id}>
              <div className="row between">
                <div className="brand-mark" style={{width: 45, height: 45, fontSize: '1rem'}}>{agent.name.slice(0, 1) || '?'}</div>
                <div className="row" style={{gap: 6}}>
                  <Badge>{agent.mode === 'inbound' ? t('inbound') : agent.mode === 'outbound' ? t('outbound') : t('in + out')}</Badge>
                  <Badge tone={agent.status === 'live' ? 'success' : agent.status === 'paused' ? 'warning' : ''}>{t(agent.status)}</Badge>
                </div>
              </div>
              <h2 style={{marginTop: 18, marginBottom: 5}}>{agent.name}</h2>
              <p className="small muted">{agent.greeting || t('No opening line yet')}</p>
            </Link>)}
          </div>}
    {creating && <Modal title={t('Create an agent')} onClose={() => setCreating(false)}>
      <Field label="Agent name" value={name} onChange={setName} placeholder="Support, Sales, Admissions…"/>
      {error && <div className="notice danger">{error}</div>}
      <div className="row" style={{justifyContent: 'flex-end', marginTop: 20}}>
        <Button variant="secondary" onClick={() => setCreating(false)}>{t('Cancel')}</Button>
        <Button disabled={!name.trim() || busy} onClick={create}>
          {busy ? <Loader2 size={15} className="spin"/> : t('Create agent')}
        </Button>
      </div>
    </Modal>}
  </div>;
}

/**
 * One agent, edited as a form.
 *
 * It used to save on a timer while you typed, which meant a PATCH and a
 * "Saved." toast every few hundred milliseconds in the middle of a sentence,
 * and a half-written greeting stored if you walked away. Now the page holds a
 * draft, says when it differs from what is stored, and saves when asked.
 * Status and mode are the exception: those are single clicks with no partial
 * state, so they save immediately.
 */
export function AgentDetailScreen() {
  const {id} = useParams();
  const {t, toast, canManage} = useApp();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [draft, setDraft] = useState<Agent | null>(null);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [confirm, confirmDialog] = useConfirm();

  const load = useCallback(() => {
    if (!id) return;
    workspace.agent(id)
      .then(row => {setAgent(row); setDraft(row)})
      .catch(c => setError(errorText(c, 'Could not load this agent.')));
    workspace.knowledgeBases().then(b => setBases(b.knowledgeBases)).catch(() => {});
  }, [id]);
  useEffect(() => {load()}, [load]);

  // Leaving with unsaved words is the one thing worth interrupting for.
  const dirty = Boolean(agent && draft && FORM_FIELDS.some(k => agent[k] !== draft[k]));
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {e.preventDefault(); e.returnValue = ''};
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const set = (patch: Partial<Agent>) => setDraft(old => old && {...old, ...patch});

  const save = async (patch?: Partial<Agent>) => {
    if (!id || !draft) return;
    setSaving(true); setError('');
    try {
      const body = patch ?? Object.fromEntries(FORM_FIELDS.map(k => [k, draft[k]]));
      const updated = await workspace.updateAgent(id, body as Partial<Agent>);
      setAgent(updated);
      // A one-field save (status, mode, knowledge base) must not throw away
      // words still being typed, so only the keys it sent are taken back.
      setDraft(old => !old ? updated
        : patch ? {...old, ...Object.fromEntries(Object.keys(patch).map(k => [k, (updated as never)[k]]))}
        : updated);
      toast(t('Saved.'));
    } catch (cause) {
      setError(errorText(cause, 'That could not be saved.'));
    } finally {setSaving(false)}
  };

  /** Status and mode: one click, saved at once, and reverted if it is refused. */
  const setNow = async (patch: Partial<Agent>) => {
    set(patch);
    await save(patch);
  };

  const remove = () => confirm({
    title: 'Delete this agent?',
    body: <>Any number this agent answers on stops answering until you choose
      another one. Calls it has already handled keep their records.</>,
    confirmLabel: 'Delete agent',
    onConfirm: async () => {
      if (!id) return;
      await workspace.deleteAgent(id);
      toast(t('Agent deleted'));
      navigate('/app/agents');
    },
  });

  if (!agent || !draft) return <div className="stack">
    <PageHead eyebrow="Build" title="Agent"/>
    {error ? <ErrorNote error={error} onRetry={load}/> : <Loading/>}
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Build" title={agent.name}
      description="What this agent says, what it knows, and when it hands over to a person."
      action={canManage ? <Button variant="outline" onClick={remove}>
        <Trash2 size={14}/> {t('Delete')}
      </Button> : undefined}/>

    {error && <ErrorNote error={error}/>}

    <div className="card">
      <Section title="Where this agent is used"
        help="A number's inbound slot takes an inbound or both agent; its outbound slot takes an outbound or both.">
        <div className="field-grid">
          <Select label="What it does" value={draft.mode ?? 'both'} disabled={!canManage || saving}
            onChange={v => void setNow({mode: v as Agent['mode']})}
            help="Change this and the Numbers page offers it in that slot.">
            <option value="inbound">Answers calls that come in</option>
            <option value="outbound">Makes calls out</option>
            <option value="both">Both</option>
          </Select>
          <Select label="Status" value={draft.status} disabled={!canManage || saving}
            onChange={v => void setNow({status: v as Agent['status']})}
            help="Paused or draft agents can still be assigned, but you will see the status on the number.">
            <option value="draft">Draft — still being written</option>
            <option value="live">Live — ready to take calls</option>
            <option value="paused">Paused</option>
          </Select>
        </div>
        <div className="row wrap">
          <Link className="button outline small" to="/app/numbers">
            <RouteIcon size={14}/> Put this agent on a number
          </Link>
        </div>
      </Section>

      <Section title="What it says">
        <TextInput label="Name" value={draft.name} disabled={!canManage}
          onChange={v => set({name: v})} help="Only you see this. Callers never hear it."/>
        <TextInput label="Opening line" value={draft.greeting} disabled={!canManage}
          onChange={v => set({greeting: v})}
          placeholder="Thank you for calling Acme. How can I help?"
          help="The first thing a caller hears. Leave empty to use the company's own greeting."/>
        <TextInput label="Who this agent is" rows={5} value={draft.roleDescription} disabled={!canManage}
          onChange={v => set({roleDescription: v})}
          placeholder="You are Sara, the support assistant for Acme. You are warm, brief and practical…"
          help="Written in plain language, as if briefing a new colleague."/>
        <div className="field-grid">
          <TextInput label="Language" value={draft.language} disabled={!canManage}
            onChange={v => set({language: v})} placeholder="Urdu, English…"
            help="It still follows a caller who speaks another language."/>
          <TextInput label="Voice" value={draft.ttsVoice} disabled={!canManage}
            onChange={v => set({ttsVoice: v})} placeholder="leave empty for the default"/>
        </div>
        <TextInput label="Tone notes" value={draft.toneNotes} disabled={!canManage}
          onChange={v => set({toneNotes: v})}
          placeholder="Never promise a refund. Always confirm the spelling of a name."/>
        <TextInput label="When to hand off to a person" rows={3} value={draft.escalationRules}
          disabled={!canManage} onChange={v => set({escalationRules: v})}
          placeholder="If the caller asks for a manager, or sounds upset, offer a callback."/>
      </Section>

      <Section title="What it knows"
        help="Answers come only from the material you upload. Without any, it offers to take a message rather than invent an answer.">
        <Select label="Knowledge base" value={draft.knowledgeBaseId} disabled={!canManage}
          onChange={v => void setNow({knowledgeBaseId: v})}>
          <option value="">Everything the company has uploaded</option>
          {bases.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </Select>
        <div className="row wrap">
          <Link className="button outline small" to="/app/knowledge">
            <BrainCircuit size={14}/> Manage documents
          </Link>
        </div>
      </Section>
    </div>

    {/* The save bar stays put, so a long form never hides it. */}
    {canManage && <div className="save-bar">
      <span className="small muted">
        {saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'Everything is saved'}
      </span>
      <div className="row">
        <Button variant="outline" disabled={!dirty || saving} onClick={() => setDraft(agent)}>
          Discard changes
        </Button>
        <Button disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? <><Loader2 size={15} className="spin"/> Saving…</> : 'Save changes'}
        </Button>
      </div>
    </div>}
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// Knowledge bases
// ---------------------------------------------------------------------------

export function KnowledgeBasesScreen() {
  const {t, toast, canManage} = useApp();
  const navigate = useNavigate();
  const [bases, setBases] = useState<KnowledgeBase[] | null>(null);
  const [unfiled, setUnfiled] = useState(0);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    workspace.knowledgeBases().then(b => { setBases(b.knowledgeBases); setUnfiled(b.unfiledCount) })
      .catch(c => setError(errorText(c, 'Could not load your knowledge bases.')));
  }, []);
  useEffect(() => { load() }, [load]);

  const create = async () => {
    setBusy(true); setError('');
    try {
      const kb = await workspace.createKnowledgeBase(name.trim(), purpose.trim(), bases?.length === 0);
      toast(t('Knowledge base created'));
      setCreating(false); setName(''); setPurpose('');
      navigate('/app/knowledge/' + kb.id);
    } catch (cause) { setError(errorText(cause, 'Could not create that knowledge base.')) }
    finally { setBusy(false) }
  };

  return <div className="stack">
    <PageHead eyebrow="Build" title="Knowledge bases"
      description="Separate documents for different numbers or campaigns — a price list for sales, a handbook for support."
      action={canManage ? <Button onClick={() => setCreating(true)}><Plus size={16}/>{t('New knowledge base')}</Button> : undefined}/>
    {error && <div className="notice danger">{error}</div>}
    {bases === null ? <div className="small muted">{t('Loading…')}</div>
      : bases.length === 0
        ? <Empty icon={BrainCircuit} title="No knowledge bases yet"
            body="Without one, every agent answers from everything your company has uploaded. Create one to split that up."
            action={canManage ? <Button onClick={() => setCreating(true)}>{t('New knowledge base')}</Button> : undefined}/>
        : <div className="grid cols-3">
            {bases.map(kb => <Link to={'/app/knowledge/' + kb.id} className="card lift" key={kb.id}>
              <div className="row between">
                <div className="brand-mark" style={{width: 45, height: 45, fontSize: '1rem'}}><BrainCircuit size={20}/></div>
                {kb.isDefault && <Badge tone="success"><Star size={11}/> {t('Default')}</Badge>}
              </div>
              <h2 style={{marginTop: 18, marginBottom: 5}}>{kb.name}</h2>
              <p className="small muted">{kb.purpose || t('No description')}</p>
              <div className="small muted" style={{marginTop: 12}}>{kb.documentCount} {t('documents')}</div>
            </Link>)}
          </div>}
    {unfiled > 0 && <div className="notice">
      {unfiled} {t('document(s) are not filed under any knowledge base and are read by every agent with no base of its own.')}
    </div>}
    {creating && <Modal title={t('Create a knowledge base')} onClose={() => setCreating(false)}>
      <Field label="Name" value={name} onChange={setName} placeholder="Price list, Support handbook…"/>
      <Field label="What it's for" value={purpose} onChange={setPurpose} placeholder="Optional"/>
      {error && <div className="notice danger">{error}</div>}
      <div className="row" style={{justifyContent: 'flex-end', marginTop: 20}}>
        <Button variant="secondary" onClick={() => setCreating(false)}>{t('Cancel')}</Button>
        <Button disabled={!name.trim() || busy} onClick={create}>
          {busy ? <Loader2 size={15} className="spin"/> : t('Create')}
        </Button>
      </div>
    </Modal>}
  </div>;
}

function docSize(chars: number): string {
  if (chars >= 1000) return `${Math.round(chars / 1000).toLocaleString()}k characters`;
  return `${chars} characters`;
}

export function KnowledgeBaseDetailScreen() {
  const {kbId} = useParams();
  const {t, toast, canManage} = useApp();
  const navigate = useNavigate();
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [confirm, confirmDialog] = useConfirm();
  const picker = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    if (!kbId) return;
    workspace.knowledgeBases().then(b => setKb(b.knowledgeBases.find(x => x.id === kbId) || null));
    workspace.documents(kbId).then(b => setDocs(b.documents)).catch(c => setError(errorText(c, 'Could not load documents.')));
  }, [kbId]);
  useEffect(() => { load() }, [load]);

  const upload = async (file: File) => {
    if (!kbId) return;
    setBusy(file.name); setError('');
    try {
      await workspace.uploadDocument(file, kbId);
      toast(`${file.name} added.`);
      load();
    } catch (cause) { setError(errorText(cause, 'That file could not be read.')) }
    finally { setBusy(''); if (picker.current) picker.current.value = '' }
  };

  const removeBase = () => confirm({
    title: 'Delete this knowledge base?',
    body: <>Its documents are kept but become unfiled, so every agent can read
      them. Any agent pointed at this base falls back to everything you have
      uploaded. A number currently answering from it is refused — move it first.</>,
    confirmLabel: 'Delete knowledge base',
    onConfirm: async () => {
      if (!kbId) return;
      await workspace.deleteKnowledgeBase(kbId);
      toast(t('Knowledge base deleted'));
      navigate('/app/knowledge');
    },
  });

  const removeDocument = (doc: KbDocument) => confirm({
    title: 'Remove this document?',
    body: <>Your agent stops answering from <b>{doc.name}</b> on the next call.
      Nothing it already said changes.</>,
    confirmLabel: 'Remove document',
    onConfirm: async () => {await workspace.deleteDocument(doc.id); toast(`${doc.name} removed.`); load()},
  });

  if (!kb) return <div className="stack">
    <PageHead eyebrow="Build" title="Knowledge base"/>
    {error ? <div className="notice danger">{error}</div> : <div className="small muted">{t('Loading…')}</div>}
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Build" title={kb.name}
      action={canManage ? <div className="row">
        {!kb.isDefault && <Button variant="outline" small
          onClick={() => workspace.updateKnowledgeBase(kb.id, {isDefault: true}).then(load)}>
          <Star size={14}/> {t('Make default')}
        </Button>}
        <Button variant="outline" onClick={removeBase}><Trash2 size={14}/> {t('Delete')}</Button>
      </div> : undefined}/>
    {error && <div className="notice danger">{error}</div>}
    <div className="card stack">
      <Field label="Name" value={kb.name} disabled={!canManage}
        onChange={v => workspace.updateKnowledgeBase(kb.id, {name: v}).then(load)}/>
      <Field label="What it's for" value={kb.purpose} disabled={!canManage}
        onChange={v => workspace.updateKnowledgeBase(kb.id, {purpose: v}).then(load)}/>
    </div>
    <div className="card stack">
      <div className="row between">
        <strong>{t('Documents')}</strong>
        {canManage && <>
          <input ref={picker} type="file" hidden accept=".pdf,.txt,.md,.csv,text/*,application/pdf"
            onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f) }}/>
          <Button small onClick={() => picker.current?.click()} disabled={Boolean(busy)}>
            {busy ? <><Loader2 size={15} className="spin"/> {t('Reading…')}</> : <><Upload size={15}/> {t('Add a document')}</>}
          </Button>
        </>}
      </div>
      {docs.length === 0
        ? <Empty icon={FileText} title="No documents yet" body="Without one, agents pointed at this base answer from nothing."/>
        : <div className="stack">{docs.map(doc => <div className="setup-row" key={doc.id}>
            <div className="setup-row-main"><strong>{doc.name}</strong><span className="small muted">{docSize(doc.chars)}</span></div>
            {canManage && <Button small variant="outline" disabled={busy === doc.id} onClick={() => removeDocument(doc)}>
              <Trash2 size={14}/> {t('Remove')}</Button>}
          </div>)}</div>}
    </div>
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// Call setups
// ---------------------------------------------------------------------------

export function CallSetupsScreen() {
  const {t, toast, canManage} = useApp();
  const [setups, setSetups] = useState<CallSetup[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [form, setForm] = useState({
    name: '', channel: 'PHONE' as Channel, direction: 'INBOUND' as 'INBOUND' | 'OUTBOUND',
    agentId: '', knowledgeBaseId: '',
  });
  const [busy, setBusy] = useState(false);
  const [confirm, confirmDialog] = useConfirm();

  const load = useCallback(() => {
    workspace.callSetups().then(b => setSetups(b.callSetups)).catch(c => setError(errorText(c, 'Could not load call setups.')));
    workspace.agents().then(b => setAgents(b.agents)).catch(() => {});
    workspace.knowledgeBases().then(b => setBases(b.knowledgeBases)).catch(() => {});
  }, []);
  useEffect(() => { load() }, [load]);

  const create = async () => {
    if (!form.agentId) { setError(t('Choose which agent answers.')); return }
    setBusy(true); setError('');
    try {
      await workspace.createCallSetup({
        name: form.name.trim() || (form.direction === 'INBOUND' ? 'Incoming calls' : 'Outbound'),
        channel: form.channel, direction: form.direction,
        agentId: form.agentId, knowledgeBaseId: form.knowledgeBaseId, enabled: true,
      });
      toast(t('Call setup created'));
      setCreating(false);
      setForm({name: '', channel: 'PHONE', direction: 'INBOUND', agentId: '', knowledgeBaseId: ''});
      load();
    } catch (cause) { setError(errorText(cause, 'Could not create that call setup.')) }
    finally { setBusy(false) }
  };

  // An outbound setup can be toggled on its own — several can run at once.
  // Owner and staff both get this: switching which preset is active for a
  // number is day-to-day operation, not configuration.
  const toggle = async (setup: CallSetup) => {
    try { await workspace.updateCallSetup(setup.id, {enabled: !setup.enabled}); load() }
    catch (cause) { setError(errorText(cause, 'Could not change that.')) }
  };

  // Inbound is exclusive — the backend refuses a second enabled setup on the
  // same channel (one call, one agent to answer it). So "switch" here means
  // disable whichever inbound setup currently owns this channel, then enable
  // the one chosen, in that order: enabling first would hit that same
  // refusal while the old one is still on.
  const switchInbound = async (target: CallSetup) => {
    if (!setups) return;
    const sibling = setups.find(
      s => s.id !== target.id && s.channel === target.channel && s.direction === 'INBOUND' && s.enabled,
    );
    setBusy(true); setError('');
    try {
      if (sibling) await workspace.updateCallSetup(sibling.id, {enabled: false});
      await workspace.updateCallSetup(target.id, {enabled: true});
      load();
    } catch (cause) { setError(errorText(cause, 'Could not switch to that one.')) }
    finally { setBusy(false) }
  };

  const remove = (setup: CallSetup) => confirm({
    title: 'Delete this call setup?',
    body: <>Calls on this line fall back to whatever the number itself says, or
      to the company's own settings if it says nothing.</>,
    confirmLabel: 'Delete setup',
    onConfirm: async () => {await workspace.deleteCallSetup(setup.id); load()},
  });

  const agentName = (id: string) => agents.find(a => a.id === id)?.name || t('Unknown agent');
  const baseName = (id: string) => id ? (bases.find(b => b.id === id)?.name || t('Unknown')) : t("the agent's own base");

  return <div className="stack">
    <PageHead eyebrow="Build" title="Call setups"
      description="Which agent answers each number, incoming or outgoing. One incoming setup per number; as many outgoing as you like."
      action={canManage ? <Button onClick={() => setCreating(true)} disabled={agents.length === 0}>
        <Plus size={16}/>{t('New call setup')}</Button> : undefined}/>
    {agents.length === 0 && <div className="notice">{t('Create an agent first — a call setup needs one to answer with.')}</div>}
    {error && <div className="notice danger">{error}</div>}
    {setups === null ? <div className="small muted">{t('Loading…')}</div>
      : setups.length === 0
        ? <Empty icon={RouteIcon} title="No call setups yet"
            body="Without one, every number falls back to the company's own agent and everything it has uploaded."/>
        : <div className="stack">{setups.map(s => <div className="setup-row" key={s.id}>
            <div className="setup-row-main">
              {s.direction === 'INBOUND' ? <PhoneIncoming size={16}/> : <PhoneOutgoing size={16}/>}
              <div>
                <strong>{s.name}</strong>
                <div className="small muted">
                  {t(CHANNEL_LABEL[s.channel])} · {t(s.direction === 'INBOUND' ? 'Incoming' : 'Outgoing')} ·{' '}
                  {agentName(s.agentId)} · {baseName(s.knowledgeBaseId)}
                </div>
              </div>
            </div>
            <div className="row">
              {s.direction === 'INBOUND'
                // Exclusive, so "on" means this is the one answering —
                // turning it off would just leave nothing on this channel,
                // which is worse than not offering the control at all.
                // Switching is picking a different one, not flipping this
                // one's own switch.
                ? (s.enabled
                  ? <Badge tone="success">{t('Answering now')}</Badge>
                  : <Button small variant="outline" disabled={busy} onClick={() => void switchInbound(s)}>
                      {t('Switch to this one')}</Button>)
                : <input className="switch" type="checkbox" checked={s.enabled}
                    onChange={() => void toggle(s)}/>}
              {canManage && <Button small variant="outline" onClick={() => void remove(s)}><Trash2 size={14}/></Button>}
            </div>
          </div>)}</div>}
    {creating && <Modal title={t('Create a call setup')} onClose={() => setCreating(false)}>
      <Field label="Name" value={form.name} onChange={v => setForm({...form, name: v})} placeholder="Incoming calls"/>
      <div className="field"><label>{t('Number')}</label>
        <select className="select" value={form.channel} onChange={e => setForm({...form, channel: e.target.value as Channel})}>
          {(Object.keys(CHANNEL_LABEL) as Channel[]).map(c => <option key={c} value={c}>{t(CHANNEL_LABEL[c])}</option>)}
        </select></div>
      <div className="field"><label>{t('Direction')}</label>
        <select className="select" value={form.direction} onChange={e => setForm({...form, direction: e.target.value as 'INBOUND' | 'OUTBOUND'})}>
          <option value="INBOUND">{t('Incoming')}</option>
          <option value="OUTBOUND">{t('Outgoing')}</option>
        </select></div>
      <div className="field"><label>{t('Agent')}</label>
        <select className="select" value={form.agentId} onChange={e => setForm({...form, agentId: e.target.value})}>
          <option value="">{t('Choose an agent')}</option>
          {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select></div>
      <div className="field"><label>{t('Knowledge base')}</label>
        <select className="select" value={form.knowledgeBaseId} onChange={e => setForm({...form, knowledgeBaseId: e.target.value})}>
          <option value="">{t("Use the agent's own")}</option>
          {bases.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select></div>
      {error && <div className="notice danger">{error}</div>}
      <div className="row" style={{justifyContent: 'flex-end', marginTop: 20}}>
        <Button variant="secondary" onClick={() => setCreating(false)}>{t('Cancel')}</Button>
        <Button disabled={busy} onClick={create}>{busy ? <Loader2 size={15} className="spin"/> : t('Create')}</Button>
      </div>
    </Modal>}
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// Team
// ---------------------------------------------------------------------------

export function TeamScreen() {
  const {t, toast, role} = useApp();
  const isOwner = role === 'owner';
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [inviting, setInviting] = useState(false);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [linkFor, setLinkFor] = useState<string>('');
  const [confirm, confirmDialog] = useConfirm();

  const load = useCallback(() => {
    workspace.team().then(b => { setMembers(b.members); setInvites(b.invites) })
      .catch(c => setError(errorText(c, 'Could not load your team.')));
  }, []);
  useEffect(() => { load() }, [load]);

  const invite = async () => {
    setBusy(true); setError('');
    try {
      const result = await workspace.invite(email.trim().toLowerCase(), 'staff');
      const link = `${window.location.origin}/invite?token=${result.token}`;
      setLinkFor(link);
      setInviting(false); setEmail('');
      load();
    } catch (cause) { setError(errorText(cause, 'Could not create that invitation.')) }
    finally { setBusy(false) }
  };

  const remove = (m: TeamMember) => confirm({
    title: 'Remove them from the team?',
    body: <><b>{m.name}</b> can no longer sign in to this workspace. Their own
      calls stay in the history. This does not change the company's API key —
      do that from Settings if you need access cut off immediately.</>,
    confirmLabel: 'Remove them',
    onConfirm: async () => {await workspace.removeMember(m.userId); toast(t('Removed.')); load()},
  });

  const revokeInvite = (token: string, email: string) => confirm({
    title: 'Cancel this invitation?',
    body: <>The link sent to <b>{email}</b> stops working. You can invite them again later.</>,
    confirmLabel: 'Cancel invitation',
    onConfirm: async () => {await workspace.revokeInvite(token); load()},
  });

  return <div className="stack">
    <PageHead eyebrow="Manage" title="Team"
      description="Everyone who can sign in to this workspace."
      action={isOwner ? <Button onClick={() => setInviting(true)}><Plus size={16}/>{t('Invite someone')}</Button> : undefined}/>
    {error && <div className="notice danger">{error}</div>}
    {!isOwner && <div className="notice">{t('Only the owner can invite or remove people.')}</div>}
    {members === null ? <div className="small muted">{t('Loading…')}</div> : <div className="card stack">
      {members.map(m => <div className="setup-row" key={m.userId}>
        <div className="setup-row-main">
          <strong>{m.name}{m.isYou && <span className="small muted"> ({t('you')})</span>}</strong>
          <span className="small muted">{m.email}</span>
        </div>
        <div className="row">
          <Badge tone={m.role === 'owner' ? 'success' : ''}>{t(m.role === 'owner' ? 'Owner' : 'Staff')}</Badge>
          {isOwner && !m.isYou && <Button small variant="outline" onClick={() => void remove(m)}>
            <Trash2 size={14}/></Button>}
        </div>
      </div>)}
    </div>}
    {invites.length > 0 && <div className="card stack">
      <strong>{t('Pending invitations')}</strong>
      {invites.map(i => <div className="setup-row" key={i.token}>
        <div className="setup-row-main"><strong>{i.email}</strong><span className="small muted">{t(i.role)}</span></div>
        {isOwner && <Button small variant="outline" onClick={() => revokeInvite(i.token, i.email)}><X size={14}/> {t('Revoke')}</Button>}
      </div>)}
    </div>}
    {inviting && <Modal title={t('Invite someone')} onClose={() => setInviting(false)}>
      <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="colleague@yourcompany.com"/>
      <p className="small muted">{t('They join as staff: they can place and receive calls and messages, but cannot change settings, credentials or the team.')}</p>
      {error && <div className="notice danger">{error}</div>}
      <div className="row" style={{justifyContent: 'flex-end', marginTop: 20}}>
        <Button variant="secondary" onClick={() => setInviting(false)}>{t('Cancel')}</Button>
        <Button disabled={!email.includes('@') || busy} onClick={invite}>
          {busy ? <Loader2 size={15} className="spin"/> : t('Create invitation')}
        </Button>
      </div>
    </Modal>}
    {linkFor && <Modal title={t('Share this link')} onClose={() => setLinkFor('')}>
      <p className="small muted">{t('There is no email delivery set up yet, so send this to them yourself — WhatsApp, Slack, however you usually reach them.')}</p>
      <div className="row" style={{gap: 8}}>
        <code className="small" style={{flex: 1, overflowWrap: 'anywhere', padding: '10px 12px', background: 'var(--surface-2, #f3f3f3)', borderRadius: 8}}>{linkFor}</code>
        <Button small onClick={() => { navigator.clipboard?.writeText(linkFor); toast(t('Copied')) }}><Copy size={14}/></Button>
      </div>
    </Modal>}
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// Accepting an invite
// ---------------------------------------------------------------------------

export function InviteAcceptScreen() {
  const {t} = useApp();
  const location = useLocation();
  const token = new URLSearchParams(location.search).get('token') || '';
  const [info, setInfo] = useState<{email: string; role: string; companyName: string} | null>(null);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<'new' | 'existing'>('new');

  useEffect(() => {
    if (!token) { setError('This invitation link is missing its token.'); return }
    peekInvite(token).then(setInfo).catch(() => setError('This invitation link is no longer valid.'));
  }, [token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!info) return;
    setBusy(true); setError('');
    try {
      if (mode === 'new') await registerWithEmailForInvite(info.email, password);
      else await loginWithEmail(info.email, password);
      const session = await acceptInvite(token);
      setAuthToken(session.apiKey);
      localStorage.setItem('ca-session', JSON.stringify({
        email: session.email, name: session.companyName, userId: session.phoneNumberId,
        orgId: session.phoneNumberId, portal: 'company', role: session.role,
      }));
      window.location.href = '/app/dialer';
    } catch (cause) {
      setError(friendlyAuthError(cause));
    } finally { setBusy(false) }
  };

  if (error && !info) return <div className="auth-page"><div className="card auth-card">
    <h1>{t('Invitation not found')}</h1>
    <p className="muted">{t(error)}</p>
    <Link to="/login">{t('Back to sign in')}</Link>
  </div></div>;

  if (!info) return <div className="auth-page"><div className="card auth-card"><p className="muted">{t('Loading…')}</p></div></div>;

  return <div className="auth-page"><div className="card auth-card">
    <h1 style={{fontSize: '1.8rem'}}>{t('Join')} {info.companyName}</h1>
    <p className="muted">{t('You were invited as staff, using')} {info.email}.</p>
    <form className="stack" onSubmit={submit}>
      <div className="row" style={{gap: 8}}>
        <Button small variant={mode === 'new' ? '' : 'outline'} type="button" onClick={() => setMode('new')}>{t('I am new here')}</Button>
        <Button small variant={mode === 'existing' ? '' : 'outline'} type="button" onClick={() => setMode('existing')}>{t('I already have a password')}</Button>
      </div>
      <Field label="Password" type="password" value={password} onChange={setPassword}
        help={mode === 'new' ? t('Use at least six characters — this creates your sign-in.') : t('Your existing password for this email.')} required/>
      {error && <div className="notice danger">{t(error)}</div>}
      <Button type="submit" disabled={busy || password.length < 6}>
        {busy ? <Loader2 size={15} className="spin"/> : t('Join the team')}
      </Button>
    </form>
  </div></div>;
}

async function registerWithEmailForInvite(email: string, password: string) {
  // Reuses the Firebase account-creation half of registerWithEmail without
  // its backend signup call — accept-invite is this flow's equivalent of
  // that step, and calling both would create a company nobody asked for.
  if (!firebaseAuth) throw new Error('Sign-in is not configured in this build.');
  await createUserWithEmailAndPassword(firebaseAuth, email, password);
}

// ---------------------------------------------------------------------------
// Staff console: the live queue, looking someone up, and asking the docs
// ---------------------------------------------------------------------------

function when(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}
function length(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.max(0, Math.round(seconds) - m * 60)).padStart(2, '0')}`;
}

/** The live-calls screen is already the real thing a staff member needs —
 * see calls happening now, take one over, place one. "My queue" adds recent
 * history below it, since there is no per-person call routing to filter by
 * (every signed-in person at a company shares the same line). */
export function StaffQueueScreen() {
  const {t} = useApp();
  const [recent, setRecent] = useState<WireLookupCall[] | null>(null);
  useEffect(() => {
    workspace.recentCalls(12).then(b => setRecent(b.calls)).catch(() => setRecent([]));
  }, []);
  return <div className="stack">
    <LiveCalls/>
    <div className="card stack">
      <h2>{t('Recent calls')}</h2>
      {recent === null ? <div className="small muted">{t('Loading…')}</div>
        : recent.length === 0 ? <Empty icon={Users} title="No calls yet" body="Calls will be listed here once there are some."/>
        : <div className="table-wrap"><table className="table">
            <thead><tr><th>{t('When')}</th><th>{t('Number')}</th><th>{t('Length')}</th><th>{t('Knowledge used')}</th></tr></thead>
            <tbody>{recent.map(c => <tr key={c.id}>
              <td><Link to={'/app/history/' + c.id}>{when(c.startedAt)}</Link></td>
              <td className="mono">{c.counterparty}</td>
              <td className="mono">{length(c.durationSeconds)}</td>
              <td className="small">{c.knowledgeBaseName || t('Everything uploaded')}</td>
            </tr>)}</tbody>
          </table></div>}
    </div>
  </div>;
}

export function StaffLookupScreen() {
  const {t} = useApp();
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<WireLookupCall[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const digits = query.replace(/\D/g, '');
    if (digits.length < 3) { setMatches(null); return }
    const handle = setTimeout(() => {
      workspace.lookupCalls(digits).then(b => setMatches(b.calls))
        .catch(c => setError(errorText(c, 'Could not search right now.')));
    }, 300);
    return () => clearTimeout(handle);
  }, [query]);

  return <div className="stack">
    <PageHead eyebrow="Help me" title="Customer lookup"
      description="Search a number to see everything that has happened with that person."/>
    <div className="card">
      <input className="input" value={query} onChange={e => setQuery(e.target.value)}
        placeholder={t('Type at least three digits of a number')} autoFocus/>
    </div>
    {error && <div className="notice danger">{error}</div>}
    {matches !== null && (matches.length === 0
      ? <Empty icon={Users} title="No calls with that number" body="Check the digits, or try fewer of them."/>
      : <div className="card stack">
          <div className="small muted">{matches.length} {t('calls with')} <span className="mono">{matches[0].counterparty}</span></div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{t('When')}</th><th>{t('Direction')}</th><th>{t('Length')}</th><th>{t('Knowledge used')}</th></tr></thead>
            <tbody>{matches.map(c => <tr key={c.id}>
              <td><Link to={'/app/history/' + c.id}>{when(c.startedAt)}</Link></td>
              <td>{c.direction === 'INBOUND' ? t('They called') : t('We called')}</td>
              <td className="mono">{length(c.durationSeconds)}</td>
              <td className="small">{c.knowledgeBaseName || t('Everything uploaded')}</td>
            </tr>)}</tbody>
          </table></div>
        </div>)}
  </div>;
}

export function StaffAskScreen() {
  const {t} = useApp();
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState('');
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<{answered: boolean; text: string} | null>(null);
  const [error, setError] = useState('');

  useEffect(() => { workspace.knowledgeBases().then(b => setBases(b.knowledgeBases)).catch(() => {}) }, []);

  const ask = async () => {
    if (!question.trim()) return;
    setBusy(true); setError(''); setAnswer(null);
    try { setAnswer(await workspace.ask(question.trim(), kbId)) }
    catch (cause) { setError(errorText(cause, 'Could not check that right now.')) }
    finally { setBusy(false) }
  };

  return <div className="stack">
    <PageHead eyebrow="Help me" title="Ask the documents"
      description="Check an answer mid-call without putting the caller on hold."/>
    <div className="card stack">
      <div className="field"><label>{t('Look in')}</label>
        <select className="select" value={kbId} onChange={e => setKbId(e.target.value)}>
          <option value="">{t('Everything uploaded')}</option>
          {bases.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select></div>
      <div className="row">
        <input className="input" autoFocus value={question} onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void ask() }}
          placeholder={t('What is the fee for BS Computer Science?')}/>
        <Button onClick={() => void ask()} disabled={busy || !question.trim()}>
          {busy ? t('Checking…') : t('Ask')}
        </Button>
      </div>
      {error && <div className="notice danger">{error}</div>}
      {answer && <div className={'result-card big' + (answer.answered ? '' : ' discarded')}>
        <p>{answer.text}</p>
      </div>}
    </div>
  </div>;
}
