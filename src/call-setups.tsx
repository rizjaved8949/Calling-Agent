import {useMemo, useState} from 'react';
import {CheckCircle2, PhoneIncoming, PhoneOutgoing, Plus, Trash2, X} from 'lucide-react';
import {Badge,Button,Empty,Field,PageHead} from './app';
import {useApp} from './app-context';
import {api} from './lib/api';
import {presetsOfKind} from './lib/presets';
import type {CallSetup, ChannelType} from './lib/types';

/**
 * Call setups — the single answer to "how will a call on this number behave".
 *
 * A number holds at most one incoming setup, because a caller who dials it can
 * only be met one way. It can hold as many outgoing setups as you like, so a
 * fee chase and an open-day invitation can use different knowledge from the
 * same number.
 *
 * Staff can create and edit these, because choosing the knowledge and tone for
 * a call is the job. Everything else about the account stays with an admin.
 */

const CHANNEL_LABEL: Record<ChannelType, string> = {
  sim: 'Phone line', whatsapp_call: 'WhatsApp calling', whatsapp_message: 'WhatsApp messaging',
};

const blank = (orgId: string, channelId: string, direction: CallSetup['direction'], agentId: string, who: string): CallSetup => ({
  id: 'setup-' + Math.random().toString(36).slice(2, 9),
  orgId, channelId, direction,
  name: direction === 'INBOUND' ? 'Incoming calls' : '',
  agentId, knowledgeBaseIds: [], tonePreset: 'natural', strictnessPreset: 'balanced',
  enabled: true, createdBy: who, createdAt: new Date().toISOString(), usedLast30d: 0,
});

export function CallSetups() {
  const {store, org, session, run, t, role} = useApp();
  const channels = store.channels.filter(x => x.orgId === org.id);
  const setups = useMemo(() => store.callSetups.filter(x => x.orgId === org.id), [store.callSetups, org.id]);
  const agents = store.agents.filter(x => x.orgId === org.id);
  const [editing, setEditing] = useState<CallSetup | null>(null);

  const start = (channelId: string, direction: CallSetup['direction']) =>
    setEditing(blank(org.id, channelId, direction, agents[0]?.id || '', session?.name || 'Someone'));

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Call setups"
      description="How a call behaves on each number. One setup for incoming, as many as you need for outgoing."/>

    {role === 'staff' && <div className="notice">{t('You can create and change call setups. Account settings, numbers and billing stay with an admin.')}</div>}

    {channels.length === 0 && <Empty icon={PhoneIncoming} title="No numbers yet" body="Once a number is active it will appear here."/>}

    {channels.map(ch => {
      const inbound = setups.find(x => x.channelId === ch.id && x.direction === 'INBOUND');
      const outbound = setups.filter(x => x.channelId === ch.id && x.direction === 'OUTBOUND');
      const canIn = !!ch.config.inboundEnabled;
      const canOut = !!ch.config.outboundEnabled;
      return <div className="card stack" key={ch.id}>
        <div className="row between">
          <div><h2>{ch.label}</h2><p className="small muted">{t(CHANNEL_LABEL[ch.type])} · {ch.displayNumber || t('no number yet')}</p></div>
          <Badge tone={ch.status === 'connected' ? 'success' : 'warning'}>{t(ch.status === 'connected' ? 'Active' : 'Not active')}</Badge>
        </div>

        {/* Incoming — exactly one. */}
        <div className="setup-group">
          <div className="row between">
            <span className="row"><PhoneIncoming size={16}/><strong>{t('When someone calls in')}</strong></span>
            {!inbound && canIn && <Button small onClick={() => start(ch.id, 'INBOUND')}><Plus size={14}/> Set this up</Button>}
          </div>
          {!canIn
            ? <p className="small muted">{t('Answering incoming calls is switched off for this number.')}</p>
            : inbound
              ? <SetupRow setup={inbound} onEdit={() => setEditing(inbound)}/>
              : <p className="small muted">{t('Nothing set up yet, so incoming callers get your default agent and knowledge.')}</p>}
          {inbound && <p className="small muted">{t('A number can only be answered one way, so there is just this one incoming setup.')}</p>}
        </div>

        {/* Outgoing — as many as you like. */}
        <div className="setup-group">
          <div className="row between">
            <span className="row"><PhoneOutgoing size={16}/><strong>{t('When you call out')}</strong></span>
            {canOut && <Button small variant="outline" onClick={() => start(ch.id, 'OUTBOUND')}><Plus size={14}/> New outgoing setup</Button>}
          </div>
          {!canOut
            ? <p className="small muted">{t('Making outgoing calls is switched off for this number.')}</p>
            : outbound.length === 0
              ? <p className="small muted">{t('None yet. Add one for each purpose — a fee chase and an open-day invitation can use different knowledge.')}</p>
              : outbound.map(sp => <SetupRow key={sp.id} setup={sp} onEdit={() => setEditing(sp)}/>)}
        </div>
      </div>;
    })}

    {editing && <SetupEditor setup={editing} onClose={() => setEditing(null)}/>}
  </div>;
}

function SetupRow({setup, onEdit}: {setup: CallSetup; onEdit: () => void}) {
  const {store, run, t} = useApp();
  const agent = store.agents.find(a => a.id === setup.agentId);
  const names = setup.knowledgeBaseIds.map(k => store.knowledgeBases.find(b => b.id === k)?.name).filter(Boolean);
  const tone = store.presets.find(p => p.id === setup.tonePreset);
  const strict = store.presets.find(p => p.id === setup.strictnessPreset);
  return <div className={'setup-row ' + (setup.enabled ? '' : 'off')}>
    <div className="setup-row-main">
      <div className="row"><strong>{setup.name || t('Untitled')}</strong>
        {!setup.enabled && <Badge tone="warning">{t('Off')}</Badge>}</div>
      <div className="small muted">
        {agent ? <>{t('Answered by')} <strong>{agent.name}</strong> · </> : null}
        {names.length ? <>{t('using')} <strong>{names.join(t(' first, then '))}</strong></> : t('using your default knowledge')}
      </div>
      <div className="small muted">{tone?.label} · {strict?.label} · {setup.usedLast30d} {t('calls in 30 days')}</div>
    </div>
    <div className="row">
      <Button small variant="outline" onClick={onEdit}>Edit</Button>
      <button className="icon-btn" title={t('Delete')} onClick={() => run(() => api.deleteCallSetup(setup.id), 'Setup deleted.')}><Trash2 size={15}/></button>
    </div>
  </div>;
}

function SetupEditor({setup, onClose}: {setup: CallSetup; onClose: () => void}) {
  const {store, org, run, t} = useApp();
  const [draft, setDraft] = useState<CallSetup>(setup);
  const bases = store.knowledgeBases.filter(x => x.orgId === org.id);
  const agents = store.agents.filter(x => x.orgId === org.id);
  const channel = store.channels.find(c => c.id === draft.channelId);
  const incoming = draft.direction === 'INBOUND';
  const toggleKb = (id: string) => setDraft(d => ({
    ...d, knowledgeBaseIds: d.knowledgeBaseIds.includes(id) ? d.knowledgeBaseIds.filter(x => x !== id) : [...d.knowledgeBaseIds, id],
  }));
  const save = async () => {
    const ok = await run(() => api.saveCallSetup(draft), 'Setup saved. New calls use it straight away.');
    if (ok) onClose();
  };
  return <div className="modal-backdrop" onClick={onClose}><div className="modal wide" onClick={e => e.stopPropagation()}>
    <div className="row between">
      <h2>{incoming ? t('When someone calls') : t('When you call out')} — {channel?.label}</h2>
      <button className="icon-btn" onClick={onClose}><X size={18}/></button>
    </div>

    {!incoming && <Field label="What is this for?" value={draft.name} onChange={v => setDraft(d => ({...d, name: v}))}
      placeholder="Merit list follow-up" help="A name your team will recognise when they pick it."/>}

    <div className="field"><label>{t('Who answers')}</label>
      <select className="select" value={draft.agentId} onChange={e => setDraft(d => ({...d, agentId: e.target.value}))}>
        {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select></div>

    <div className="field"><label>{t('Knowledge it should use')}</label>
      <div className="chip-row">{bases.map(b =>
        <button type="button" key={b.id} className={'chip ' + (draft.knowledgeBaseIds.includes(b.id) ? 'on' : '')} onClick={() => toggleKb(b.id)}>
          {draft.knowledgeBaseIds.includes(b.id) && <CheckCircle2 size={14}/>}{b.name}
        </button>)}</div>
      {draft.knowledgeBaseIds.length > 1 && <div className="help">{t('It looks in')} <strong>{draft.knowledgeBaseIds.map(id => bases.find(b => b.id === id)?.name).join(t(' first, then '))}</strong>.</div>}
      {draft.knowledgeBaseIds.length === 0 && <div className="help">{t('With none chosen, the call falls back to your default knowledge.')}</div>}
    </div>

    <div className="field-grid">
      <div className="field"><label>{t('Tone')}</label>
        <select className="select" value={draft.tonePreset} onChange={e => setDraft(d => ({...d, tonePreset: e.target.value}))}>
          {presetsOfKind(store.presets, 'pace').map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <div className="help">{presetsOfKind(store.presets, 'pace').find(p => p.id === draft.tonePreset)?.description}</div></div>
      <div className="field"><label>{t('How strictly it sticks to your documents')}</label>
        <select className="select" value={draft.strictnessPreset} onChange={e => setDraft(d => ({...d, strictnessPreset: e.target.value}))}>
          {presetsOfKind(store.presets, 'strictness').map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <div className="help">{presetsOfKind(store.presets, 'strictness').find(p => p.id === draft.strictnessPreset)?.description}</div></div>
    </div>

    <label className="row small"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft(d => ({...d, enabled: e.target.checked}))}/> {t('This setup is in use')}</label>

    <div className="row"><Button variant="outline" onClick={onClose}>Cancel</Button>
      <Button onClick={save} disabled={!incoming && !draft.name.trim()}>Save setup</Button></div>
  </div></div>;
}
