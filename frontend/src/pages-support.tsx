/**
 * Live chat between a company and the platform operator.
 *
 * One conversation component serves both sides. The company's screen is that
 * component with no company id — meaning "my own thread" — and the operator's
 * is a list of companies beside the same component with one chosen. Writing it
 * twice would mean two places to fix whenever a message grows a field.
 *
 * Attachments are the point as much as the text: "the number says Not
 * verified" is a screenshot, and "the agent sounds wrong" is a recording of
 * it. A photo, a video and a voice note each render as the thing they are,
 * playable in place, rather than as a filename you have to download to see.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {
  ArrowLeft, Building2, Check, CheckCheck, Circle, Download, FileText, Loader2, Mic,
  Paperclip, Play, Send, Square, Trash2, X,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {ErrorNote, Loading, useConfirm} from './ui';
import {
  ACCEPT, KIND_LABEL, SupportSocket, dayLabel, formatBytes, formatClock, formatWhen,
  kindOf, supportApi, tooLarge,
  type Author, type SocketEvent, type SupportMessage, type ThreadRow,
} from './lib/api/support';
import {markThreadOpen} from './lib/support-unread';

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

// ---------------------------------------------------------------------------
// One attachment
// ---------------------------------------------------------------------------

/**
 * The file, as the thing it is.
 *
 * The URL is fetched when the bubble renders rather than stored on the
 * message: it is signed and short-lived, so one minted at page load would
 * have expired by the time somebody scrolled back to it.
 */
function AttachmentView({message, tenantId}: {message: SupportMessage; tenantId: string}) {
  const attachment = message.attachment!;
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  const [full, setFull] = useState(false);

  useEffect(() => {
    let alive = true;
    supportApi.attachmentUrl(tenantId, message.id)
      .then(found => {if (alive) setUrl(found)})
      .catch(() => {if (alive) setFailed(true)});
    return () => {alive = false};
  }, [tenantId, message.id]);

  if (failed) return <div className="small muted">
    {KIND_LABEL[attachment.kind]} unavailable — it may have been removed.
  </div>;
  if (!url) return <div className="attachment-loading">
    <Loader2 size={14} className="spin"/> Loading the {KIND_LABEL[attachment.kind].toLowerCase()}…
  </div>;

  if (attachment.kind === 'image') return <>
    <button type="button" className="attachment-image" onClick={() => setFull(true)}
      title="See it full size">
      <img src={url} alt={attachment.name} loading="lazy"/>
    </button>
    {full && <div className="lightbox" onClick={() => setFull(false)} role="dialog" aria-modal="true">
      <button className="icon-btn lightbox-close" aria-label="Close"><X size={22}/></button>
      <img src={url} alt={attachment.name} onClick={e => e.stopPropagation()}/>
    </div>}
  </>;

  if (attachment.kind === 'video') return <video className="attachment-video" src={url}
    controls preload="metadata" playsInline/>;

  if (attachment.kind === 'voice') return <div className="attachment-voice">
    <audio src={url} controls preload="metadata"/>
    {attachment.durationSeconds > 0
      && <span className="small muted">{formatClock(attachment.durationSeconds)}</span>}
  </div>;

  return <a className="attachment-file" href={url} target="_blank" rel="noreferrer">
    <FileText size={18}/>
    <span>
      <strong>{attachment.name}</strong>
      <div className="small muted">{formatBytes(attachment.bytes)}</div>
    </span>
    <Download size={16}/>
  </a>;
}

// ---------------------------------------------------------------------------
// One message
// ---------------------------------------------------------------------------

function Bubble({message, tenantId, me, onDelete}: {
  message: SupportMessage; tenantId: string; me: Author;
  onDelete: (message: SupportMessage) => void;
}) {
  const mine = message.author === me;
  // Either side may remove their own; the operator may remove anything,
  // because the operator is who has to deal with what was uploaded.
  const canDelete = mine || me === 'operator';

  return <div className={'chat-line ' + (mine ? 'mine' : 'theirs')}>
    <div className="chat-bubble">
      {!mine && message.authorName
        && <div className="chat-who">{message.authorName}</div>}
      {message.attachment && <AttachmentView message={message} tenantId={tenantId}/>}
      {message.body && <p className="chat-text">{message.body}</p>}
      <div className="chat-meta">
        <span>{formatWhen(message.createdAt)}</span>
        {mine && (message.readAt
          ? <CheckCheck size={13} className="tick read" aria-label="Read"/>
          : <Check size={13} className="tick" aria-label="Sent"/>)}
        {canDelete && <button type="button" className="chat-delete"
          onClick={() => onDelete(message)} aria-label="Delete this message">
          <Trash2 size={12}/>
        </button>}
      </div>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// Recording a voice note
// ---------------------------------------------------------------------------

/**
 * A voice note, recorded in the browser.
 *
 * `audio/webm` is what Chrome and Firefox produce and Safari now accepts; the
 * server takes ogg, mp4 and wav too, so whatever the platform hands back is
 * sendable. The length is measured here because a webm from MediaRecorder
 * carries no duration in its header, and an audio element reports `Infinity`
 * for it — so without this the player shows no length at all.
 */
function useVoiceRecorder(onReady: (file: File, seconds: number) => void) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const started = useRef(0);
  const ticker = useRef<number | null>(null);
  const keep = useRef(true);

  const stopTicking = () => {
    if (ticker.current !== null) window.clearInterval(ticker.current);
    ticker.current = null;
  };

  useEffect(() => () => {
    stopTicking();
    recorder.current?.stream.getTracks().forEach(track => track.stop());
  }, []);

  const start = async () => {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('This browser cannot record audio. Attach a file instead.');
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
    } catch {
      setError('No access to the microphone. Allow it in the browser and try again.');
      return;
    }

    const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
    const made = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
    chunks.current = [];
    keep.current = true;
    made.ondataavailable = event => {
      if (event.data.size) chunks.current.push(event.data);
    };
    made.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      stopTicking();
      setRecording(false);
      const length = (Date.now() - started.current) / 1000;
      if (!keep.current || chunks.current.length === 0 || length < 0.4) return;
      const type = made.mimeType || 'audio/webm';
      const blob = new Blob(chunks.current, {type});
      const extension = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'weba';
      onReady(new File([blob], `voice-note.${extension}`, {type}), length);
    };

    recorder.current = made;
    started.current = Date.now();
    setSeconds(0);
    setRecording(true);
    made.start();
    ticker.current = window.setInterval(
      () => setSeconds((Date.now() - started.current) / 1000), 200);
  };

  const finish = (save: boolean) => {
    keep.current = save;
    if (recorder.current?.state === 'recording') recorder.current.stop();
    else {
      stopTicking();
      setRecording(false);
    }
  };

  return {recording, seconds, error, start, stop: () => finish(true), cancel: () => finish(false)};
}

// ---------------------------------------------------------------------------
// The conversation
// ---------------------------------------------------------------------------

type ConversationProps = {
  /** The company, for the operator. Empty means "my own thread". */
  tenantId: string;
  me: Author;
  heading: string;
  subheading?: string;
  action?: React.ReactNode;
};

export function SupportConversation({tenantId, me, heading, subheading, action}: ConversationProps) {
  const {toast} = useApp();
  const [confirm, confirmDialog] = useConfirm();
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [otherOnline, setOtherOnline] = useState(false);
  const [otherTyping, setOtherTyping] = useState(false);
  const [draft, setDraft] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [voiceLength, setVoiceLength] = useState(0);
  const [sending, setSending] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const foot = useRef<HTMLDivElement>(null);
  const socket = useRef<SupportSocket | null>(null);
  const typingSentAt = useRef(0);

  const them: Author = me === 'company' ? 'operator' : 'company';

  /** Upsert by id: a message arrives from the POST and again on the socket. */
  const absorb = useCallback((incoming: SupportMessage) => {
    setMessages(old => {
      const index = old.findIndex(m => m.id === incoming.id);
      if (index >= 0) {
        const merged = [...old];
        merged[index] = incoming;
        return merged;
      }
      return [...old, incoming].sort((a, b) => a.createdAt - b.createdAt);
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const body = await supportApi.conversation(tenantId);
      setMessages(body.messages);
      setOtherOnline(Boolean(me === 'company' ? body.operatorOnline : body.companyOnline));
      setError('');
      // Opening the thread is reading it. Done here rather than on every
      // incoming message so a long backlog is one request, not fifty.
      await supportApi.markRead(tenantId);
      setMessages(old => old.map(m =>
        m.author === them && m.readAt === null ? {...m, readAt: Date.now() / 1000} : m));
    } catch (cause) {
      setError(errorText(cause, 'Could not load the conversation.'));
    } finally {
      setLoading(false);
    }
  }, [tenantId, me, them]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  // While this is on screen, nothing in it is "waiting".
  useEffect(() => {
    markThreadOpen(tenantId);
    return () => markThreadOpen(null);
  }, [tenantId]);

  useEffect(() => {
    const live = new SupportSocket(
      async () => (await supportApi.ticket(me === 'company' ? undefined : tenantId)).ticket,
      (event: SocketEvent) => {
        switch (event.type) {
          case 'hello':
            setOtherOnline(me === 'company' ? event.operatorOnline : event.companyOnline);
            break;
          case 'message':
            absorb(event.message);
            if (event.message.author === them) {
              setOtherTyping(false);
              // They are being read right now, so say so — the sender's second
              // tick is the only thing that makes "seen" mean anything.
              void supportApi.markRead(tenantId).catch(() => undefined);
            }
            break;
          case 'read':
            if (event.by === them) {
              setMessages(old => old.map(m =>
                m.author === me && m.readAt === null ? {...m, readAt: Date.now() / 1000} : m));
            }
            break;
          case 'typing':
            if (event.side === them) setOtherTyping(event.on);
            break;
          case 'presence':
            if (event.side === them) setOtherOnline(event.online);
            break;
          case 'deleted':
            setMessages(old => old.filter(m => m.id !== event.messageId));
            break;
          case 'resync':
            void load();
            break;
        }
      },
      setConnected,
    );
    socket.current = live;
    void live.open();
    return () => {
      live.close();
      socket.current = null;
    };
  }, [tenantId, me, them, absorb, load]);

  // Stay at the newest message, the way every chat does.
  useEffect(() => {
    foot.current?.scrollIntoView({block: 'end', behavior: messages.length > 40 ? 'auto' : 'smooth'});
  }, [messages.length, otherTyping]);

  const choose = (chosen: File | null) => {
    if (!chosen) return;
    const refusal = tooLarge(chosen);
    if (refusal) {toast(refusal); return}
    setFile(chosen);
    setVoiceLength(0);
  };

  const recorder = useVoiceRecorder((made, seconds) => {
    setFile(made);
    setVoiceLength(seconds);
  });

  const clearAttachment = () => {
    setFile(null);
    setVoiceLength(0);
    if (picker.current) picker.current.value = '';
  };

  const noteTyping = (text: string) => {
    setDraft(text);
    const now = Date.now();
    // One frame every two seconds rather than one per keystroke.
    if (now - typingSentAt.current > 2000) {
      typingSentAt.current = now;
      socket.current?.send({type: 'typing', on: true});
    }
  };

  const send = async () => {
    const text = draft.trim();
    if ((!text && !file) || sending) return;
    setSending(true);
    socket.current?.send({type: 'typing', on: false});
    try {
      const written = await supportApi.send(tenantId, text, file, voiceLength);
      absorb(written);
      setDraft('');
      clearAttachment();
    } catch (cause) {
      toast(errorText(cause, 'That did not send.'));
    } finally {
      setSending(false);
    }
  };

  const remove = (message: SupportMessage) => {
    confirm({
      title: 'Delete this message?',
      body: 'It goes for both sides — there is one shared history here, so hiding '
        + 'it from only yourself would leave the other person answering something '
        + 'you can no longer see.',
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: async () => {
        await supportApi.remove(tenantId, message.id);
        setMessages(old => old.filter(m => m.id !== message.id));
      },
    });
  };

  // Day headings, so a thread read a week later has somewhere to anchor.
  const withDays = useMemo(() => {
    let last = '';
    return messages.map(message => {
      const day = dayLabel(message.createdAt);
      const first = day !== last;
      last = day;
      return {message, day: first ? day : ''};
    });
  }, [messages]);

  const theirName = me === 'company' ? 'Support' : heading;

  return <div className="stack">
    <PageHead eyebrow={me === 'company' ? 'Help' : 'Support'} title={heading}
      description={subheading} action={action}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    <div className="card chat-card">
      <div className="chat-head">
        <div className="row" style={{gap: 10}}>
          <Circle size={9} className={otherOnline ? 'presence on' : 'presence'}/>
          <div>
            <strong>{theirName}</strong>
            <div className="small muted">
              {otherOnline ? 'Online now' : me === 'company'
                ? 'Not online — leave a message and it will be answered here'
                : 'Not online — they will see this when they next sign in'}
            </div>
          </div>
        </div>
        <Badge tone={connected ? 'live' : 'warning'}>
          {connected ? 'Live' : 'Reconnecting'}
        </Badge>
      </div>

      <div className="chat-scroll">
        {loading ? <Loading label="Loading the conversation…"/>
          : messages.length === 0
            ? <div className="chat-empty">
                <strong>{me === 'company' ? 'Nothing here yet' : 'No messages from them yet'}</strong>
                <p className="small muted">
                  {me === 'company'
                    ? 'Ask anything about your account, your numbers or a call that went '
                      + 'wrong. Send a screenshot, a screen recording or a voice note if it '
                      + 'is easier than describing it.'
                    : 'You can start the conversation — they will see it next time they '
                      + 'open their workspace, and get a badge on it.'}
                </p>
              </div>
            : withDays.map(({message, day}) => <div key={message.id}>
                {day && <div className="chat-day"><span>{day}</span></div>}
                <Bubble message={message} tenantId={tenantId} me={me} onDelete={remove}/>
              </div>)}
        {otherTyping && <div className="chat-line theirs">
          <div className="chat-bubble typing">
            <span className="dot-typing"/><span className="dot-typing"/><span className="dot-typing"/>
          </div>
        </div>}
        <div ref={foot}/>
      </div>

      {recorder.error && <div className="notice danger small">{recorder.error}</div>}

      {recorder.recording && <div className="chat-recording">
        <span className="recording-dot"/>
        <strong>Recording {formatClock(recorder.seconds)}</strong>
        <div className="row" style={{gap: 8, marginInlineStart: 'auto'}}>
          <Button variant="outline" small onClick={recorder.cancel}>Discard</Button>
          <Button small onClick={recorder.stop}><Square size={13}/> Stop</Button>
        </div>
      </div>}

      {file && !recorder.recording && <div className="chat-pending">
        {kindOf(file.type) === 'image'
          ? <img src={URL.createObjectURL(file)} alt=""/>
          : kindOf(file.type) === 'voice' ? <Play size={16}/> : <FileText size={16}/>}
        <span>
          <strong>{file.name}</strong>
          <div className="small muted">
            {KIND_LABEL[kindOf(file.type)]} · {formatBytes(file.size)}
            {voiceLength > 0 && ` · ${formatClock(voiceLength)}`}
          </div>
        </span>
        <button className="icon-btn" onClick={clearAttachment} aria-label="Remove the attachment">
          <X size={16}/>
        </button>
      </div>}

      <div className="chat-composer">
        <textarea className="textarea" rows={1} value={draft}
          placeholder={`Write to ${me === 'company' ? 'support' : heading}…`}
          onChange={e => noteTyping(e.target.value)}
          onBlur={() => socket.current?.send({type: 'typing', on: false})}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault(); void send()}
          }}/>
        <input ref={picker} type="file" hidden accept={ACCEPT}
          onChange={e => choose(e.target.files?.[0] ?? null)}/>
        <button className="icon-btn" title="Attach a photo, video or file"
          onClick={() => picker.current?.click()} disabled={sending || recorder.recording}>
          <Paperclip size={18}/>
        </button>
        <button className={'icon-btn' + (recorder.recording ? ' recording' : '')}
          title="Record a voice note" disabled={sending}
          onClick={() => recorder.recording ? recorder.stop() : void recorder.start()}>
          <Mic size={18}/>
        </button>
        <Button onClick={() => void send()}
          disabled={sending || recorder.recording || (!draft.trim() && !file)}>
          {sending ? <Loader2 size={15} className="spin"/> : <Send size={15}/>}
        </Button>
      </div>
      <div className="help chat-hint">
        Enter sends, Shift+Enter starts a line. Photos up to 10 MB, video up to
        40 MB.
      </div>
    </div>
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// The company's screen
// ---------------------------------------------------------------------------

export function SupportScreen() {
  return <SupportConversation
    tenantId="" me="company" heading="Contact support"
    subheading="A direct line to the people who run the platform. Send a screenshot, a screen recording or a voice note."
  />;
}

// ---------------------------------------------------------------------------
// The operator's screens
// ---------------------------------------------------------------------------

export function PlatformSupport() {
  const [rows, setRows] = useState<ThreadRow[] | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setRows((await supportApi.threads()).threads);
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not load the conversations.'));
    }
  }, []);
  useEffect(() => {void load()}, [load]);

  // The lobby socket: any company writing reorders this list, so the operator
  // does not have to refresh to find out somebody is waiting.
  useEffect(() => {
    const live = new SupportSocket(
      async () => (await supportApi.ticket('')).ticket,
      event => {
        if (event.type === 'message' || event.type === 'read' || event.type === 'deleted') {
          void load();
        }
      },
      () => undefined,
    );
    void live.open();
    return () => live.close();
  }, [load]);

  const waiting = (rows ?? []).filter(row => row.unreadForOperator > 0);

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Support"
      description="Every company that can write to you, and whatever they are waiting on."/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    {waiting.length > 0 && <div className="notice">
      <strong>{waiting.length} {waiting.length === 1 ? 'company is' : 'companies are'} waiting
      </strong> — {waiting.map(row => row.name).join(', ')}.
    </div>}

    {rows === null && !error ? <Loading label="Loading conversations…"/>
      : (rows ?? []).length === 0
        ? <Empty icon={Building2} title="No companies yet"
            body="A conversation appears here as soon as there is a company to have one with."/>
        : <div className="card">
            {(rows ?? []).map(row => <Link className="list-row thread-row"
              key={row.tenantId} to={'/platform/support/' + encodeURIComponent(row.tenantId)}>
              <div className="list-row-main">
                <div className="row" style={{gap: 9}}>
                  <Circle size={8} className={row.online ? 'presence on' : 'presence'}/>
                  <strong>{row.name}</strong>
                  {row.suspended && <Badge tone="danger">Switched off</Badge>}
                  {row.unreadForOperator > 0
                    && <Badge tone="live">{row.unreadForOperator} new</Badge>}
                </div>
                <div className="small muted thread-preview">
                  {row.lastPreview
                    ? <>{row.lastAuthor === 'operator' && <span className="muted">You: </span>}
                        {row.lastPreview}</>
                    : 'No messages yet'}
                </div>
              </div>
              <div className="small muted">{formatWhen(row.lastMessageAt)}</div>
            </Link>)}
          </div>}
  </div>;
}

export function PlatformSupportThread() {
  const {tenantId} = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState('');

  useEffect(() => {
    if (!tenantId) return;
    supportApi.conversation(tenantId, 1)
      .then(body => setName(body.company?.name || tenantId))
      .catch(() => setName(tenantId));
  }, [tenantId]);

  if (!tenantId) {
    navigate('/platform/support');
    return null;
  }

  return <SupportConversation
    tenantId={tenantId} me="operator" heading={name || tenantId}
    subheading="They see your replies the moment you send them, and get a badge if they are away."
    action={<div className="row wrap">
      <Button variant="outline" to="/platform/support"><ArrowLeft size={15}/> All conversations</Button>
      <Button variant="outline" to={'/platform/companies/' + encodeURIComponent(tenantId)}>
        <Building2 size={15}/> Their account
      </Button>
    </div>}
  />;
}
