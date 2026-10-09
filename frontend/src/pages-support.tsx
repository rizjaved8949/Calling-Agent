/**
 * Live chat between a company and the platform operator, as a messenger.
 *
 * Laid out the way a desktop messenger is, because that layout is not a style
 * choice: the shell owns its height, the history scrolls inside it, and the
 * composer is pinned. Written as a page of cards instead, the compose box ends
 * up below the fold and the page scrolls underneath you while you are reading.
 *
 * One conversation component serves both sides. The operator gets it beside a
 * list of companies; the company gets it alone, because a company only ever
 * talks to one party and a list of one is furniture. Writing it twice would
 * mean two places to fix whenever a message grows a field.
 *
 * Attachments are as much the point as the text. "The number says Not
 * verified" is a screenshot and "the agent sounds wrong" is a recording of it,
 * so each renders as the thing it is, playable in the bubble.
 */
import {Fragment, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useNavigate, useParams} from 'react-router-dom';
import {
  ArrowLeft, Building2, Check, CheckCheck, Download, FileText, Image as ImageIcon,
  Loader2, Mic, MicOff, Paperclip, Search, Send, Trash2, Video, X,
} from 'lucide-react';
import {Badge, Button, PageHead} from './app';
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

/** Up to two initials, so a list of names reads as a list of people. */
function initials(name: string): string {
  const words = (name || '?').trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function Avatar({name, online, small}: {name: string; online?: boolean; small?: boolean}) {
  return <span className={'sc-avatar' + (small ? ' small' : '') + (online ? ' online' : '')}
    aria-hidden="true">
    {initials(name)}
    {online !== undefined && <span className="sc-dot"/>}
  </span>;
}

/**
 * Make an element fill whatever is left below it.
 *
 * This was `height: calc(100vh - 250px)` in CSS, which has to assume how tall
 * everything above it is — and the page heading wraps to three lines on a
 * narrow window, which pushed the compose box off the bottom of the screen.
 * Measuring the element's own top is exact at every width.
 *
 * `visualViewport` rather than `innerHeight` where it exists, so an on-screen
 * keyboard shrinks the conversation instead of shoving the composer out of
 * sight behind it.
 */
function useFillsRemainingHeight(minimum = 420) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fit = () => {
      const node = ref.current;
      if (!node) return;
      const viewport = window.visualViewport?.height ?? window.innerHeight;
      const top = node.getBoundingClientRect().top;
      // The gap below matches the page padding, so the shell does not sit
      // flush against the bottom edge of the window.
      const gap = window.innerWidth <= 760 ? 22 : 34;
      node.style.height = Math.max(minimum, viewport - top - gap) + 'px';
    };

    fit();
    // Twice more on the next frames: web fonts and the lazily-loaded page
    // above can both still be settling when this first runs, and either moves
    // our top edge.
    const soon = requestAnimationFrame(fit);
    const later = window.setTimeout(fit, 250);

    window.addEventListener('resize', fit);
    window.visualViewport?.addEventListener('resize', fit);
    window.visualViewport?.addEventListener('scroll', fit);
    return () => {
      cancelAnimationFrame(soon);
      window.clearTimeout(later);
      window.removeEventListener('resize', fit);
      window.visualViewport?.removeEventListener('resize', fit);
      window.visualViewport?.removeEventListener('scroll', fit);
    };
  }, [minimum]);

  return ref;
}

// ---------------------------------------------------------------------------
// One attachment
// ---------------------------------------------------------------------------

/**
 * The file, as the thing it is.
 *
 * The URL is fetched when the bubble renders rather than stored on the
 * message: it is signed and short-lived, so one minted at page load would have
 * expired by the time somebody scrolled back to it.
 */
function Attachment({message, tenantId, onGrew}: {
  message: SupportMessage; tenantId: string; onGrew?: () => void;
}) {
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

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => {if (e.key === 'Escape') setFull(false)};
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [full]);

  if (failed) return <div className="sc-file-wait">
    {KIND_LABEL[attachment.kind]} unavailable — it may have been removed.
  </div>;
  if (!url) return <div className="sc-file-wait">
    <Loader2 size={13} className="spin"/> Loading the {KIND_LABEL[attachment.kind].toLowerCase()}…
  </div>;

  if (attachment.kind === 'image') return <>
    <button type="button" className="sc-photo" onClick={() => setFull(true)}
      title="See it full size">
      <img src={url} alt={attachment.name} loading="lazy" onLoad={() => onGrew?.()}/>
    </button>
    {full && <div className="sc-lightbox" role="dialog" aria-modal="true"
      onClick={() => setFull(false)}>
      <button className="icon-btn sc-lightbox-x" aria-label="Close"><X size={22}/></button>
      <img src={url} alt={attachment.name} onClick={e => e.stopPropagation()}/>
    </div>}
  </>;

  if (attachment.kind === 'video') return <video className="sc-video" src={url}
    controls preload="metadata" playsInline onLoadedMetadata={() => onGrew?.()}/>;

  if (attachment.kind === 'voice') return <div className="sc-voice">
    <audio src={url} controls preload="metadata"/>
    {attachment.durationSeconds > 0
      && <span className="small muted">{formatClock(attachment.durationSeconds)}</span>}
  </div>;

  return <a className="sc-doc" href={url} target="_blank" rel="noreferrer">
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

function Message({message, tenantId, me, tail, showAuthor, onDelete, onGrew}: {
  message: SupportMessage; tenantId: string; me: Author;
  tail: boolean; showAuthor: boolean;
  onDelete: (message: SupportMessage) => void;
  /** Called when an attachment finishes loading and the bubble changes size. */
  onGrew: () => void;
}) {
  const mine = message.author === me;
  // Either side may withdraw their own; the operator may remove anything,
  // because the operator is who has to deal with what was uploaded.
  const canDelete = mine || me === 'operator';

  return <div className={'sc-turn ' + (mine ? 'out' : 'in') + (tail ? ' tail' : '')}>
    <div className="sc-msg">
      {canDelete && <span className="sc-tools">
        <button type="button" onClick={() => onDelete(message)}
          aria-label="Delete this message"><Trash2 size={12}/></button>
      </span>}
      {!mine && showAuthor && message.authorName
        && <span className="sc-author">{message.authorName}</span>}
      {message.attachment && <Attachment message={message} tenantId={tenantId} onGrew={onGrew}/>}
      {message.body && <p className="sc-text">{message.body}</p>}
      <span className="sc-stamp">
        {formatWhen(message.createdAt)}
        {mine && (message.readAt
          ? <CheckCheck size={13} className="sc-tick seen" aria-label="Read"/>
          : <Check size={13} className="sc-tick" aria-label="Sent"/>)}
      </span>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// Recording a voice note
// ---------------------------------------------------------------------------

/**
 * A voice note, recorded in the browser.
 *
 * `audio/webm` is what Chrome and Firefox produce and Safari accepts; the
 * server also takes ogg, mp4 and wav, so whatever the platform hands back is
 * sendable. The length is measured here because a webm from MediaRecorder
 * carries no duration in its header and an audio element reports `Infinity`
 * for it — without this the player would show no length at all.
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

  return {
    recording, seconds, error,
    start, stop: () => finish(true), cancel: () => finish(false),
  };
}

// ---------------------------------------------------------------------------
// The conversation pane
// ---------------------------------------------------------------------------

type PaneProps = {
  /** The company, for the operator. Empty means "my own thread". */
  tenantId: string;
  me: Author;
  /** Who is on the other end, named. */
  them: string;
  /** Shown to the left of the name in the pane header. */
  back?: React.ReactNode;
};

export function SupportPane({tenantId, me, them, back}: PaneProps) {
  const {toast} = useApp();
  const [confirm, confirmDialog] = useConfirm();
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [theyAreHere, setTheyAreHere] = useState(false);
  const [theyAreTyping, setTheyAreTyping] = useState(false);
  const [draft, setDraft] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [voiceLength, setVoiceLength] = useState(0);
  const [sending, setSending] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const foot = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const socket = useRef<SupportSocket | null>(null);
  const typingSentAt = useRef(0);

  const other: Author = me === 'company' ? 'operator' : 'company';

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
      setTheyAreHere(Boolean(me === 'company' ? body.operatorOnline : body.companyOnline));
      setError('');
      // Opening the thread is reading it. Done here rather than per message so
      // a long backlog is one request, not fifty.
      await supportApi.markRead(tenantId);
      setMessages(old => old.map(m =>
        m.author === other && m.readAt === null ? {...m, readAt: Date.now() / 1000} : m));
    } catch (cause) {
      setError(errorText(cause, 'Could not load the conversation.'));
    } finally {
      setLoading(false);
    }
  }, [tenantId, me, other]);

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
            setTheyAreHere(me === 'company' ? event.operatorOnline : event.companyOnline);
            break;
          case 'message':
            absorb(event.message);
            if (event.message.author === other) {
              setTheyAreTyping(false);
              // They are being read right now, so say so — the sender's second
              // tick is the only thing that makes "seen" mean anything.
              void supportApi.markRead(tenantId).catch(() => undefined);
            }
            break;
          case 'read':
            if (event.by === other) {
              setMessages(old => old.map(m =>
                m.author === me && m.readAt === null ? {...m, readAt: Date.now() / 1000} : m));
            }
            break;
          case 'typing':
            if (event.side === other) setTheyAreTyping(event.on);
            break;
          case 'presence':
            if (event.side === other) setTheyAreHere(event.online);
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
  }, [tenantId, me, other, absorb, load]);

  // Stay at the newest message, the way every messenger does.
  const keepAtBottom = useCallback((smooth = true) => {
    foot.current?.scrollIntoView({block: 'end', behavior: smooth ? 'smooth' : 'auto'});
  }, []);

  useEffect(() => {
    keepAtBottom(messages.length <= 40);
  }, [messages.length, theyAreTyping, keepAtBottom]);

  // The compose box grows with what is typed and stops at the CSS ceiling.
  useEffect(() => {
    const node = box.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = Math.min(node.scrollHeight, 150) + 'px';
  }, [draft]);

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

  /**
   * Day pills, and which bubble in a run gets the tail.
   *
   * A messenger groups a run from one person and puts the tail on the last of
   * them; giving every bubble a tail is what makes a long reply look like a
   * stack of unrelated notes.
   */
  const laidOut = useMemo(() => {
    let lastDay = '';
    return messages.map((message, index) => {
      const day = dayLabel(message.createdAt);
      const newDay = day !== lastDay;
      lastDay = day;
      const next = messages[index + 1];
      const previous = messages[index - 1];
      return {
        message,
        day: newDay ? day : '',
        tail: !next || next.author !== message.author
          || dayLabel(next.createdAt) !== day,
        showAuthor: newDay || !previous || previous.author !== message.author,
      };
    });
  }, [messages]);

  return <div className="sc-pane">
    <div className="sc-pane-head">
      {back}
      <Avatar name={them} online={theyAreHere}/>
      <div className="sc-who">
        <strong>{them}</strong>
        <div className={'sc-presence' + (theyAreHere ? ' on' : '')}>
          {theyAreTyping ? 'typing…' : theyAreHere ? 'online' : me === 'company'
            ? 'not online — leave a message, it will be answered here'
            : 'not online — they will see this when they next sign in'}
        </div>
      </div>
      {!connected && <Badge tone="warning">Reconnecting</Badge>}
    </div>

    <div className="sc-history">
      {error && <ErrorNote error={error} onRetry={() => void load()}/>}
      {loading ? <Loading label="Loading the conversation…"/>
        : messages.length === 0
          ? <div className="sc-empty">
              <strong>{me === 'company' ? 'Nothing here yet' : 'No messages yet'}</strong>
              <p className="small muted">
                {me === 'company'
                  ? 'Ask anything about your account, your numbers or a call that went '
                    + 'wrong. Send a screenshot, a screen recording or a voice note if '
                    + 'that is easier than describing it.'
                  : 'You can start this conversation — they will see it next time they '
                    + 'open their workspace, and get a badge on it.'}
              </p>
            </div>
          : laidOut.map(({message, day, tail, showAuthor}) => <Fragment key={message.id}>
              {day && <div className="sc-daypill">{day}</div>}
              <Message message={message} tenantId={tenantId} me={me} tail={tail}
                showAuthor={showAuthor} onDelete={remove} onGrew={keepAtBottom}/>
            </Fragment>)}
      {theyAreTyping && <div className="sc-turn in tail">
        <div className="sc-msg">
          <span className="sc-typing"><i/><i/><i/></span>
        </div>
      </div>}
      <div ref={foot}/>
    </div>

    {recorder.error && <div className="sc-tray">
      <MicOff size={16}/><span className="small">{recorder.error}</span>
    </div>}

    {recorder.recording && <div className="sc-tray">
      <span className="sc-rec-dot"/>
      <span><strong>Recording {formatClock(recorder.seconds)}</strong></span>
      <Button variant="outline" small onClick={recorder.cancel}>Discard</Button>
      <Button small onClick={recorder.stop}>Use it</Button>
    </div>}

    {file && !recorder.recording && <div className="sc-tray">
      {kindOf(file.type) === 'image'
        ? <img src={URL.createObjectURL(file)} alt=""/>
        : kindOf(file.type) === 'video' ? <Video size={18}/>
          : kindOf(file.type) === 'voice' ? <Mic size={18}/> : <FileText size={18}/>}
      <span>
        <strong>{file.name}</strong>
        <div className="small muted">
          {KIND_LABEL[kindOf(file.type)]} · {formatBytes(file.size)}
          {voiceLength > 0 && ` · ${formatClock(voiceLength)}`}
        </div>
      </span>
      <button className="icon-btn" onClick={clearAttachment}
        aria-label="Remove the attachment"><X size={16}/></button>
    </div>}

    <div className="sc-compose">
      <input ref={picker} type="file" hidden accept={ACCEPT}
        onChange={e => choose(e.target.files?.[0] ?? null)}/>
      <button className="sc-icon" title="Attach a photo, video or file"
        onClick={() => picker.current?.click()} disabled={sending || recorder.recording}>
        <Paperclip size={19}/>
      </button>
      <button className={'sc-icon' + (recorder.recording ? ' rec' : '')} disabled={sending}
        title={recorder.recording ? 'Stop recording' : 'Record a voice note'}
        onClick={() => recorder.recording ? recorder.stop() : void recorder.start()}>
        <Mic size={19}/>
      </button>
      <textarea ref={box} className="textarea" rows={1} value={draft}
        placeholder={`Message ${them}`}
        onChange={e => noteTyping(e.target.value)}
        onBlur={() => socket.current?.send({type: 'typing', on: false})}
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault(); void send()}
        }}/>
      <button className="sc-send" onClick={() => void send()} aria-label="Send"
        disabled={sending || recorder.recording || (!draft.trim() && !file)}>
        {sending ? <Loader2 size={17} className="spin"/> : <Send size={17}/>}
      </button>
    </div>
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// The company's screen: the conversation, and nothing beside it
// ---------------------------------------------------------------------------

export function SupportScreen() {
  const shell = useFillsRemainingHeight();
  return <div className="stack">
    <PageHead eyebrow="Help" title="Contact support"
      description="A direct line to the people who run the platform. Send a screenshot, a screen recording or a voice note."/>
    <div className="sc-shell solo" ref={shell}>
      <SupportPane tenantId="" me="company" them="Support"/>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// The operator's screen: the list beside the conversation
// ---------------------------------------------------------------------------

function ThreadList({rows, openId, onOpen, loading}: {
  rows: ThreadRow[]; openId: string; loading: boolean;
  onOpen: (tenantId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const found = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(row =>
      row.name.toLowerCase().includes(needle)
      || row.tenantId.toLowerCase().includes(needle));
  }, [rows, query]);

  const waiting = rows.reduce((total, row) => total + (row.unreadForOperator > 0 ? 1 : 0), 0);

  return <div className="sc-list">
    <div className="sc-list-head">
      <div className="row between">
        <strong>Conversations</strong>
        {waiting > 0 && <Badge tone="live">{waiting} waiting</Badge>}
      </div>
      <div className="sc-search">
        <Search size={15}/>
        <input className="input" value={query} placeholder="Search companies"
          onChange={e => setQuery(e.target.value)}/>
      </div>
    </div>
    <div className="sc-list-scroll">
      {loading && <Loading label="Loading…"/>}
      {!loading && found.length === 0 && <div className="sc-empty">
        <strong>Nothing matches</strong>
        <p className="small muted">No company here by that name.</p>
      </div>}
      {found.map(row => <button type="button" key={row.tenantId}
        className={'sc-row' + (row.tenantId === openId ? ' active' : '')
          + (row.unreadForOperator > 0 ? ' unread' : '')}
        onClick={() => onOpen(row.tenantId)}>
        <Avatar name={row.name} online={row.online}/>
        <span className="sc-row-main">
          <span className="sc-row-top">
            <strong>{row.name}</strong>
            <span className="sc-row-when">{formatWhen(row.lastMessageAt)}</span>
          </span>
          <span className="sc-row-preview">
            {row.lastAuthor === 'operator' && <Check size={12}/>}
            <span>{row.lastPreview || 'No messages yet'}</span>
            {row.suspended && <Badge tone="danger">Off</Badge>}
          </span>
        </span>
        {row.unreadForOperator > 0
          && <span className="sc-count">{row.unreadForOperator}</span>}
      </button>)}
    </div>
  </div>;
}

export function PlatformSupport() {
  const {tenantId = ''} = useParams();
  const navigate = useNavigate();
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

  const open = (rows ?? []).find(row => row.tenantId === tenantId);
  const shell = useFillsRemainingHeight();

  return <div className="stack">
    <PageHead eyebrow="Platform" title="Support"
      description="Every company that can write to you, and whatever they are waiting on."
      action={open && <Button variant="outline"
        to={'/platform/companies/' + encodeURIComponent(open.tenantId)}>
        <Building2 size={15}/> Their account
      </Button>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    <div className={'sc-shell' + (tenantId ? ' has-open' : '')} ref={shell}>
      <ThreadList rows={rows ?? []} openId={tenantId} loading={rows === null && !error}
        onOpen={id => navigate('/platform/support/' + encodeURIComponent(id))}/>
      {tenantId
        ? <SupportPane
            key={tenantId} tenantId={tenantId} me="operator"
            them={open?.name || tenantId}
            back={<button className="sc-icon" aria-label="Back to the list"
              onClick={() => navigate('/platform/support')}><ArrowLeft size={18}/></button>}
          />
        : <div className="sc-pane">
            <div className="sc-history">
              <div className="sc-empty">
                <ImageIcon size={30} style={{color: 'var(--accent)', marginBottom: 10}}/>
                <strong>Pick a company</strong>
                <p className="small muted">
                  Choose a conversation on the left to read it and reply. Anyone
                  waiting on you is at the top, with a count.
                </p>
              </div>
            </div>
          </div>}
    </div>
  </div>;
}
