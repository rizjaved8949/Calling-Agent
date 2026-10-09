/**
 * WhatsApp messages: one conversation at a time.
 *
 * Laid out the way WhatsApp Web is, because that is what everyone using this
 * already knows: contacts on the left, the thread on the right scrolling
 * inside itself, the compose box pinned to its bottom. The page itself never
 * grows, so the compose box cannot end up below the fold.
 *
 * Two WhatsApp rules shape this screen, and both are stated on it rather than
 * discovered by a message that silently never arrives:
 *
 * * A free-text message only reaches someone within 24 hours of their last
 *   message. Outside that window Meta accepts the request and delivers
 *   nothing, so the compose box switches itself to templates and says why.
 * * Deleting a message here removes our own record. WhatsApp has no API for
 *   unsending, so the confirmation says that plainly.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {
  Check, CheckCheck, Clock, Loader2, MessageCircle, Phone, Plus, RefreshCw, Send,
  Trash2, TriangleAlert,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {ErrorNote, Loading, Modal, Select, TextInput, useConfirm} from './ui';
import {fillTemplate, messagesApi, type WaTemplate, type WireMessage} from './lib/api/calls';
import {numberName, numbers as numbersApi, type PhoneNumber} from './lib/api/numbers';

const DAY_MS = 86_400_000;
const WINDOW_MS = 24 * 60 * 60 * 1000;

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function clockTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000)
    .toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
}

function dayLabel(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  const today = new Date();
  const days = Math.round((today.setHours(0, 0, 0, 0) - new Date(date).setHours(0, 0, 0, 0)) / DAY_MS);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return date.toLocaleDateString(undefined, {day: 'numeric', month: 'long', year: days > 300 ? 'numeric' : undefined});
}

/** Delivery, as WhatsApp itself shows it: one tick sent, two delivered. */
function Receipt({message}: {message: WireMessage}) {
  if (message.direction === 'INBOUND') return null;
  const status = message.status.toLowerCase();
  if (status === 'failed') return <span className="row" style={{gap: 4, color: 'var(--destructive)'}}>
    <TriangleAlert size={12}/> failed</span>;
  if (status === 'read') return <CheckCheck size={13} color="var(--accent)"/>;
  if (status === 'delivered') return <CheckCheck size={13}/>;
  if (status === 'sent') return <Check size={13}/>;
  return <Clock size={11}/>;
}

type Thread = {number: string; messages: WireMessage[]; last: WireMessage; unread: number};

/**
 * What to show when a message has no text of its own.
 *
 * A template's words live in Meta's catalogue, not in the message we sent, so
 * the body comes back empty — and "(template)" under the template's own name
 * told the reader nothing they could not already see. Media has the same
 * problem for a different reason.
 */
function describe(message: WireMessage): string {
  if (message.templateName) return 'Sent from your approved template.';
  const kinds: Record<string, string> = {
    image: 'Sent a photo', video: 'Sent a video', audio: 'Sent a voice note',
    document: 'Sent a document', sticker: 'Sent a sticker',
    location: 'Shared a location', contacts: 'Shared a contact',
  };
  return kinds[message.kind] ?? `Sent a ${message.kind} message`;
}

export function MessagesScreen() {
  const {t, toast, canManage} = useApp();
  const [confirm, confirmDialog] = useConfirm();
  const [messages, setMessages] = useState<WireMessage[] | null>(null);
  const [lines, setLines] = useState<PhoneNumber[]>([]);
  const [lineId, setLineId] = useState('');
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [newNumber, setNewNumber] = useState('');
  const [templates, setTemplates] = useState<WaTemplate[]>([]);
  const [templateOpen, setTemplateOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(async (quiet = false) => {
    try {
      const body = await messagesApi.list();
      setMessages(body.messages);
      if (!quiet) setError('');
    } catch (cause) {
      if (!quiet) setError(errorText(cause, 'Could not load your messages.'));
    }
  }, []);

  useEffect(() => {
    void load();
    // Polled, not streamed: a thread gains a message every few minutes at
    // most, and a websocket purely to watch a list is more to go wrong.
    const timer = setInterval(() => void load(true), 10_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    numbersApi.list().then(b => {
      const whatsapp = b.numbers.filter(n => n.kind === 'whatsapp');
      setLines(whatsapp);
      const usable = whatsapp.find(n => n.status === 'verified');
      if (usable) setLineId(id => id || usable.id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!lineId) return;
    messagesApi.templates(lineId)
      .then(b => setTemplates(b.templates.filter(x => x.status?.toUpperCase() === 'APPROVED')))
      .catch(() => setTemplates([]));
  }, [lineId]);

  const threads = useMemo<Thread[]>(() => {
    const byNumber = new Map<string, WireMessage[]>();
    for (const message of messages ?? []) {
      const list = byNumber.get(message.counterparty);
      if (list) list.push(message); else byNumber.set(message.counterparty, [message]);
    }
    return Array.from(byNumber.entries())
      .map(([number, rows]) => {
        const ordered = rows.slice().sort((a, b) => a.createdAt - b.createdAt);
        return {
          number,
          messages: ordered,
          last: ordered[ordered.length - 1],
          unread: 0,
        };
      })
      .sort((a, b) => b.last.createdAt - a.last.createdAt);
  }, [messages]);

  useEffect(() => {
    if (!selected && threads.length && !starting) setSelected(threads[0].number);
  }, [threads, selected, starting]);

  const thread = threads.find(x => x.number === selected);
  const active = starting ? newNumber.trim() : selected;

  useEffect(() => {
    bottom.current?.scrollIntoView({block: 'end'});
  }, [thread?.messages.length, selected]);

  const line = lines.find(l => l.id === lineId);
  const ready = Boolean(line && line.status === 'verified');

  /** Whether a free-text reply can still reach them. */
  const lastInbound = thread?.messages.filter(m => m.direction === 'INBOUND').at(-1);
  const windowOpen = Boolean(lastInbound && Date.now() - lastInbound.createdAt * 1000 < WINDOW_MS);
  const canFreeText = windowOpen || !thread; // a brand-new number is told after it fails

  const send = async () => {
    const body = draft.trim();
    if (!active || !body || sending) return;
    setSending(true);
    try {
      const sent = await messagesApi.sendText(active, body, lineId);
      setMessages(old => [...(old ?? []), sent]);
      setDraft('');
      if (starting) {setStarting(false); setSelected(sent.counterparty || active); setNewNumber('')}
      void load(true);
    } catch (cause) {
      toast(errorText(cause, 'That message could not be sent.'));
    } finally {setSending(false)}
  };

  const remove = (message: WireMessage) => confirm({
    title: 'Delete this message?',
    body: <>
      <p style={{margin: '0 0 10px'}}>
        It disappears from this workspace — for you and for everyone on your team.
      </p>
      <p style={{margin: 0}}>
        <b>It stays on {message.counterparty}&rsquo;s phone.</b> WhatsApp gives
        businesses no way to unsend, so &ldquo;delete for everyone&rdquo; is not
        something this or any other business tool can actually do. Anyone telling
        you otherwise is deleting their own copy too.
      </p>
    </>,
    confirmLabel: 'Delete for my team',
    onConfirm: async () => {
      await messagesApi.remove(message.id);
      setMessages(old => (old ?? []).filter(m => m.id !== message.id));
    },
  });

  const removeThread = (which: Thread) => confirm({
    title: 'Delete this conversation?',
    body: <>
      <p style={{margin: '0 0 10px'}}>
        All {which.messages.length} messages with <b>{which.number}</b> are removed
        from this workspace, for everyone on your team.
      </p>
      <p style={{margin: 0}}>
        Their own copy of the conversation is untouched — WhatsApp gives
        businesses no way to delete a message from someone else&rsquo;s phone.
      </p>
    </>,
    confirmLabel: 'Delete for my team',
    onConfirm: async () => {
      for (const message of which.messages) await messagesApi.remove(message.id);
      setMessages(old => (old ?? []).filter(m => m.counterparty !== which.number));
      setSelected('');
    },
  });

  if (messages === null && !error) return <div className="stack">
    <PageHead eyebrow="Messages" title="WhatsApp"/>
    <Loading label="Loading your conversations…"/>
  </div>;

  if (lines.length === 0) return <div className="stack">
    <PageHead eyebrow="Messages" title="WhatsApp"/>
    <Empty icon={MessageCircle} title="No WhatsApp number connected"
      body="Messaging needs a WhatsApp Business number from Meta, connected with its own credentials."
      action={<Button to="/app/numbers"><Phone size={15}/> Connect a number</Button>}/>
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Messages" title="WhatsApp"
      description="Conversations on your WhatsApp numbers. Your agent can answer these on its own — turn that on per number."
      action={<div className="row wrap">
        {lines.length > 1 && <select className="select" style={{width: 'auto'}} value={lineId}
          aria-label="Send from" onChange={e => setLineId(e.target.value)}>
          {lines.map(l => <option key={l.id} value={l.id}>{numberName(l)}</option>)}
        </select>}
        <Button variant="outline" onClick={() => void load()}><RefreshCw size={15}/> {t('Refresh')}</Button>
      </div>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}
    {!ready && <div className="notice warning">
      That WhatsApp number is not verified yet, so messages cannot be sent from it.{' '}
      <Link to="/app/numbers">Check its credentials →</Link>
    </div>}

    <div className="chat-shell">
      <div className="chat-contacts">
        <div className="chat-contacts-head">
          <Button small variant="outline" disabled={!canManage}
            onClick={() => {setStarting(true); setSelected(''); setNewNumber('')}}>
            <Plus size={14}/> {t('New conversation')}
          </Button>
        </div>
        <div className="chat-contacts-list">
          {threads.length === 0 && !starting && <p className="small muted" style={{padding: 18}}>
            No conversations yet. Start one, or wait for someone to message your number.
          </p>}
          {threads.map(item => <button key={item.number}
            className={'chat-contact' + (selected === item.number && !starting ? ' active' : '')}
            onClick={() => {setSelected(item.number); setStarting(false)}}>
            <div className="chat-contact-top">
              <span className="mono">{item.number}</span>
              <span className="chat-contact-time">{clockTime(item.last.createdAt)}</span>
            </div>
            <div className="chat-contact-preview">
              {item.last.direction === 'OUTBOUND' ? 'You: ' : ''}{item.last.body || describe(item.last)}
            </div>
          </button>)}
        </div>
      </div>

      <div className="chat-thread">
        {starting
          ? <div className="chat-thread-head">
              <div className="form-row">
                <TextInput label="Their WhatsApp number" value={newNumber} onChange={setNewNumber}
                  placeholder="+923001112222" autoFocus/>
                <Button variant="outline" onClick={() => setStarting(false)}>Cancel</Button>
              </div>
            </div>
          : thread && <div className="chat-thread-head">
              <div className="row between" style={{gap: 12}}>
                <span className="mono">{thread.number}</span>
                <div className="row" style={{gap: 8}}>
                  {windowOpen
                    ? <Badge tone="success">can reply freely</Badge>
                    : <Badge tone="warning">template only</Badge>}
                  <button className="icon-btn" title="Delete conversation"
                    onClick={() => removeThread(thread)}><Trash2 size={15}/></button>
                </div>
              </div>
            </div>}

        {!thread && !starting
          ? <div className="chat-empty">Choose a conversation, or start a new one.</div>
          : <>
              <div className="chat-thread-body">
                {(thread?.messages ?? []).map((message, index, all) => {
                  const previous = all[index - 1];
                  const newDay = !previous
                    || new Date(previous.createdAt * 1000).toDateString()
                       !== new Date(message.createdAt * 1000).toDateString();
                  return <div key={message.id} style={{display: 'contents'}}>
                    {newDay && <span className="chat-day">{dayLabel(message.createdAt)}</span>}
                    <div className={'chat-bubble ' + (message.direction === 'OUTBOUND' ? 'out' : 'in')}>
                      <div className="chat-bubble-tools">
                        <button className="icon-btn" aria-label="Delete this message"
                          title="Delete from this workspace (it stays on their phone)"
                          onClick={() => remove(message)}><Trash2 size={13}/></button>
                      </div>
                      {message.templateName && <div className="small muted">
                        Template · {message.templateName}
                      </div>}
                      <div style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', paddingInlineEnd: 22}}>
                        {message.body || <span className="muted">{describe(message)}</span>}
                      </div>
                      <div className="chat-bubble-meta">
                        <span>{clockTime(message.createdAt)}</span>
                        <Receipt message={message}/>
                      </div>
                      {message.error && <div className="small" style={{color: 'var(--destructive)'}}>
                        {message.error}
                      </div>}
                    </div>
                  </div>;
                })}
                <div ref={bottom}/>
              </div>

              {!canFreeText && <div className="notice warning small" style={{margin: '0 16px 10px'}}>
                They last messaged more than 24 hours ago, so WhatsApp will not deliver a
                plain reply. Send an approved template instead.
              </div>}

              <div className="chat-compose">
                <textarea className="textarea" rows={1} value={draft} disabled={!ready}
                  style={{minHeight: 44}}
                  placeholder={canFreeText ? 'Type a message' : 'Outside the 24-hour window — use a template'}
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault(); void send()}
                  }}/>
                {templates.length > 0 && <Button variant="outline" disabled={!ready || !active}
                  onClick={() => setTemplateOpen(true)}>Template</Button>}
                <Button onClick={() => void send()}
                  disabled={!ready || !active || !draft.trim() || sending}
                  title={!ready ? 'This number is not verified yet' : undefined}>
                  {sending ? <Loader2 size={16} className="spin"/> : <Send size={16}/>}
                </Button>
              </div>
            </>}
      </div>
    </div>

    {templateOpen && <TemplateSender to={active} lineId={lineId} templates={templates}
      onClose={() => setTemplateOpen(false)}
      onSent={sent => {
        setMessages(old => [...(old ?? []), sent]);
        setTemplateOpen(false);
        if (starting) {setStarting(false); setSelected(sent.counterparty || active)}
        toast('Template sent.');
      }}/>}
    {confirmDialog}
  </div>;
}

/**
 * Sending an approved template.
 *
 * How many values a template takes is not something anyone should have to
 * know: it is however many distinct {{n}} appear in a body that lives in
 * Meta's catalogue, not here. Guessing gets "(#132000) Number of parameters
 * does not match the expected number of params", which names neither the
 * template nor the count — an error nobody can act on.
 *
 * So the form asks Meta, renders exactly that many boxes with the template's
 * own examples as hints, and previews the finished message. What you see is
 * what the person receives.
 */
function TemplateSender({to, lineId, templates, onClose, onSent}: {
  to: string;
  lineId: string;
  templates: WaTemplate[];
  onClose: () => void;
  onSent: (message: WireMessage) => void;
}) {
  const [name, setName] = useState(templates[0]?.name ?? '');
  const [values, setValues] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const chosen = templates.find(x => x.name === name);
  const needed = chosen?.placeholders ?? 0;

  // Each template wants its own number of values, so the boxes are rebuilt
  // when the choice changes rather than carrying the last one's answers over.
  useEffect(() => {
    setValues(Array.from({length: needed}, () => ''));
    setError('');
  }, [name, needed]);

  const missing = values.slice(0, needed).filter(v => !v.trim()).length;
  const preview = chosen ? fillTemplate(chosen.body, values) : '';

  const send = async () => {
    setBusy(true); setError('');
    try {
      onSent(await messagesApi.sendTemplate(
        to, name, values.slice(0, needed), chosen?.language || 'en_US', lineId,
      ));
    } catch (cause) {
      setError(errorText(cause, 'That template could not be sent.'));
    } finally {setBusy(false)}
  };

  return <Modal title="Send a template" onClose={onClose} width={620}
    subtitle={`To ${to || 'nobody yet'} — approved templates from your WhatsApp account.`}
    footer={<>
      <button className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
      <button className="button" onClick={() => void send()}
        disabled={busy || !name || !to || missing > 0}>
        {busy ? <><Loader2 size={15} className="spin"/> Sending…</> : 'Send template'}
      </button>
    </>}>
    <div className="stack">
      <Select label="Template" value={name} onChange={setName}
        help="Only templates Meta has approved appear here.">
        {templates.map(x => <option key={x.name + x.language} value={x.name}>
          {x.name} ({x.language}){x.placeholders ? ` — ${x.placeholders} value${x.placeholders > 1 ? 's' : ''}` : ''}
        </option>)}
      </Select>

      {needed === 0 && chosen && <p className="help" style={{margin: 0}}>
        This template takes no values. It sends exactly as written below.
      </p>}

      {Array.from({length: needed}, (_, index) => <TextInput key={index}
        label={`Value ${index + 1}`}
        value={values[index] ?? ''}
        onChange={v => setValues(old => {
          const next = [...old];
          next[index] = v;
          return next;
        })}
        placeholder={chosen?.examples?.[index] ?? ''}
        help={chosen?.examples?.[index]
          ? `Goes where {{${index + 1}}} appears. Example: ${chosen.examples[index]}`
          : `Goes where {{${index + 1}}} appears.`}/>)}

      {chosen && <div className="field">
        <label>What they will receive</label>
        <div className="template-preview">
          {chosen.header && <div className="template-head">{chosen.header}</div>}
          <div style={{whiteSpace: 'pre-wrap'}}>{preview}</div>
          {chosen.footer && <div className="template-foot">{chosen.footer}</div>}
        </div>
        {missing > 0 && <div className="help">
          {missing} value{missing > 1 ? 's' : ''} still needed — the placeholders
          above are shown as they are until you fill them.
        </div>}
      </div>}

      {error && <div className="notice danger small">{error}</div>}
    </div>
  </Modal>;
}
