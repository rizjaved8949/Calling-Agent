/**
 * Call recordings, and one call in full.
 *
 * Written against the API rather than the prototype's fixture store. The old
 * screen filtered on fields the backend has never returned (`outcome`,
 * `channelId`, a separate transcript table), so most of its nine filters did
 * nothing and its recording column could not play anything. These filter on
 * what `GET /api/calls` actually takes, and the recording is playable,
 * downloadable and deletable from the row it belongs to.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {
  ArrowDownLeft, ArrowLeft, ArrowUpRight, AudioLines, Download, FileText, Loader2, Mic2,
  Pause, Phone, Play, RefreshCw, Search, Trash2, User, X,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {anythingInFlight, useSettlingPoll} from './lib/settling';
import {ErrorNote, Loading, RecordingPlayer, Select, TextInput, useConfirm} from './ui';
import {
  CHANNEL_LABEL, RECORDING_LABEL, RESOLVED_BY_LABEL, STATUS_LABEL, callsApi, formatBytes,
  formatDuration, formatWhen, statusTone, type CallStatus, type WireCall,
} from './lib/api/calls';
import {numberName, numbers as numbersApi, type PhoneNumber} from './lib/api/numbers';

const PAGE = 50;

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function Who({call}: {call: WireCall}) {
  const out = call.direction === 'OUTBOUND';
  return <span className="row small muted" style={{gap: 6}}>
    {out ? <ArrowUpRight size={13}/> : <ArrowDownLeft size={13}/>}
    {out ? 'we called them' : 'they called us'}
  </span>;
}

function HandledBy({call}: {call: WireCall}) {
  if (call.mode === 'human') return <span className="row small" style={{gap: 5}}><User size={13}/> a person</span>;
  if (call.handledBy === 'operator') return <span className="row small" style={{gap: 5}}><User size={13}/> taken over</span>;
  return <span className="row small" style={{gap: 5}}><Mic2 size={13}/> the agent</span>;
}

// ---------------------------------------------------------------------------
// The list
// ---------------------------------------------------------------------------

/**
 * A transcript, read as the conversation it was.
 *
 * Stored as "who: what" lines, which in a <pre> is a wall of text where the
 * speakers are impossible to separate at a glance — the thing you are reading
 * it for. Each turn becomes its own bubble, the caller on one side and the
 * agent on the other, so a long call can be skimmed.
 */
function Transcript({text}: {text: string}) {
  const turns = text.split('\n').map(line => {
    const at = line.indexOf(':');
    const who = at > 0 ? line.slice(0, at).trim().toLowerCase() : '';
    const known = who === 'agent' || who === 'caller' || who === 'system';
    return {
      who: known ? who : 'agent',
      text: known ? line.slice(at + 1).trim() : line.trim(),
    };
  }).filter(turn => turn.text);

  if (turns.length === 0) return <p className="muted small">Nothing was said.</p>;

  return <div className="transcript-turns">
    {turns.map((turn, index) => turn.who === 'system'
      ? <div className="transcript-note" key={index}>{turn.text}</div>
      : <div className={'transcript-turn ' + turn.who} key={index}>
          <span className="transcript-who">{turn.who === 'agent' ? 'Agent' : 'Caller'}</span>
          <p>{turn.text}</p>
        </div>)}
  </div>;
}

/** Buttons inside a clickable row must not also open the row. */
const stop = (event: React.MouseEvent) => event.stopPropagation();

/**
 * Playing a recording straight from the list.
 *
 * One player for the whole page: starting a second recording stops the first,
 * because two calls talking over each other is nobody's idea of review. The
 * URL is minted per call and short-lived, so it is fetched on the click rather
 * than for all fifty rows up front.
 */
function useRowPlayer() {
  const [playing, setPlaying] = useState('');
  const [loading, setLoading] = useState('');
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => () => {audio.current?.pause(); audio.current = null}, []);

  const toggle = async (callId: string, onError: (message: string) => void) => {
    if (playing === callId) {
      audio.current?.pause();
      setPlaying('');
      return;
    }
    audio.current?.pause();
    setLoading(callId);
    try {
      const url = await callsApi.playbackUrl(callId);
      const element = new Audio(url);
      element.onended = () => setPlaying('');
      element.onerror = () => {setPlaying(''); onError('That recording could not be played.')};
      audio.current = element;
      await element.play();
      setPlaying(callId);
    } catch (cause) {
      onError(errorText(cause, 'That recording could not be played.'));
    } finally {
      setLoading('');
    }
  };

  return {playing, loading, toggle};
}

export function CallHistoryScreen() {
  const {t, toast, canManage} = useApp();
  const navigate = useNavigate();
  const player = useRowPlayer();
  const [confirm, confirmDialog] = useConfirm();
  const [calls, setCalls] = useState<WireCall[] | null>(null);
  const [lines, setLines] = useState<PhoneNumber[]>([]);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [status, setStatus] = useState<CallStatus | ''>('');
  const [channel, setChannel] = useState('');
  const [direction, setDirection] = useState('');
  const [lineId, setLineId] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [recordedOnly, setRecordedOnly] = useState(true);
  const [busy, setBusy] = useState(false);

  // Searching is a server-side filter on an exact number, so it waits for the
  // typing to stop rather than firing a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => {setQuery(search.trim()); setPage(0)}, 400);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(async ({quiet = false} = {}) => {
    if (!quiet) setBusy(true);
    try {
      const body = await callsApi.list({
        limit: PAGE, offset: page * PAGE,
        status: status || undefined, channel: (channel || undefined) as never,
        counterparty: query.length >= 3 ? query : undefined,
      });
      setCalls(body.calls);
      setError('');
    } catch (cause) {
      // A failed background refresh keeps what is on screen rather than
      // replacing a readable list with an error.
      if (!quiet) setError(errorText(cause, 'Could not load your calls.'));
    } finally {if (!quiet) setBusy(false)}
  }, [page, status, channel, query]);

  useEffect(() => {void load()}, [load]);
  useEffect(() => {numbersApi.list().then(b => setLines(b.numbers)).catch(() => {})}, []);

  // A call on this page can still be happening. Its row is rewritten on the
  // server as it rings, is answered and ends, and without this the screen
  // keeps showing "ringing" for a call that finished ten minutes ago — with
  // nothing to suggest a reload would help. Stops as soon as everything here
  // has settled, so an old page of calls polls nothing.
  useSettlingPoll(anythingInFlight(calls), () => load({quiet: true}));

  // Direction and number are filtered here rather than server-side: the API
  // takes neither, and both are cheap on a page of fifty.
  const shown = useMemo(() => (calls ?? []).filter(c =>
    (!direction || c.direction === direction)
    && (!lineId || c.lineId === lineId)
    && (!recordedOnly || c.recording.available)), [calls, direction, lineId, recordedOnly]);

  const lineLabel = (call: WireCall) => {
    const line = lines.find(l => l.id === call.lineId);
    return line ? numberName(line) : call.fromNumber || '—';
  };

  const remove = (call: WireCall) => confirm({
    title: 'Delete this call?',
    body: <>The record of the call with <b>{call.counterparty}</b> and its recording,
      transcript and summary are deleted for everyone. This cannot be undone.</>,
    confirmLabel: 'Delete call',
    onConfirm: async () => {
      await callsApi.remove(call.id);
      setCalls(old => (old ?? []).filter(c => c.id !== call.id));
    },
  });

  const removeRecording = (call: WireCall) => confirm({
    title: 'Delete this recording?',
    body: <>The audio is erased. The call record, transcript and summary stay.
      If it was copied to Google Drive, that copy is removed too.</>,
    confirmLabel: 'Delete recording',
    onConfirm: async () => {
      await callsApi.deleteRecording(call.id);
      setCalls(old => (old ?? []).map(c => c.id === call.id
        ? {...c, recording: {...c.recording, state: 'NONE', available: false}} : c));
    },
  });

  // What the bulk delete would actually take: the rows on screen with audio.
  const recorded = shown.filter(c => c.recording.available);
  const channelName = channel === 'PHONE' ? 'phone line'
    : channel === 'WHATSAPP_CALL' ? 'WhatsApp' : '';

  const deleteShown = () => confirm({
    title: `Delete ${recorded.length} recording${recorded.length === 1 ? '' : 's'}?`,
    body: <>
      <p style={{margin: '0 0 10px'}}>
        The audio for {recorded.length === 1 ? 'the call' : 'every call'} shown
        {channelName ? <> on your <b>{channelName}</b></> : null} is erased for
        everyone, including any copy in your Google Drive. This cannot be undone.
      </p>
      <p style={{margin: 0}}>
        The call records, transcripts and summaries stay — only the audio goes.
        {!filtered && ' Use the filters above first if you only meant some of them.'}
      </p>
    </>,
    confirmLabel: `Delete ${recorded.length} recording${recorded.length === 1 ? '' : 's'}`,
    onConfirm: async () => {
      // The exact rows on screen, so "delete these" means these — the screen
      // filters on direction and number, which the list endpoint does not.
      const result = await callsApi.deleteMany({
        callIds: recorded.map(c => c.id),
      });
      toast(result.failed
        ? `${result.deleted} deleted, ${result.failed} could not be.`
        : `${result.deleted} recording${result.deleted === 1 ? '' : 's'} deleted.`);
      await load();
    },
  });

  const clearFilters = () => {
    setStatus(''); setChannel(''); setDirection(''); setLineId('');
    setSearch(''); setRecordedOnly(true); setPage(0);
  };
  const filtered = Boolean(status || channel || direction || lineId || query);
  const hiddenByRecorded = recordedOnly ? (calls ?? []).length - shown.length : 0;

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Call recordings"
      description={canManage
        ? 'Every recorded call. Open one to play it, read the transcript, download the audio or delete it.'
        : 'The calls you placed. Open one to play it, read the transcript or download the audio.'}
      action={<div className="row wrap">
        <Button variant="outline" onClick={() => void load()} disabled={busy}>
          <RefreshCw size={15}/> {t('Refresh')}
        </Button>
        {canManage && recorded.length > 0 && <Button variant="outline" onClick={deleteShown}>
          <Trash2 size={15}/> {t('Delete these recordings')}
        </Button>}
      </div>}/>

    <div className="card stack">
      <div className="field-grid tight">
        <TextInput label="Search by number" value={search} onChange={setSearch}
          placeholder="+923001112222"
          help="The full number, as it appears in the list."/>
        <Select label="Result" value={status} onChange={v => {setStatus(v as CallStatus); setPage(0)}}>
          <option value="">Any result</option>
          {(['COMPLETED', 'NO_ANSWER', 'FAILED', 'IN_PROGRESS', 'HANDED_OFF'] as CallStatus[])
            .map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </Select>
        <Select label="Channel" value={channel} onChange={v => {setChannel(v); setPage(0)}}>
          <option value="">Phone and WhatsApp</option>
          <option value="PHONE">Phone line</option>
          <option value="WHATSAPP_CALL">WhatsApp call</option>
        </Select>
        <Select label="Direction" value={direction} onChange={setDirection}>
          <option value="">Both ways</option>
          <option value="INBOUND">They called us</option>
          <option value="OUTBOUND">We called them</option>
        </Select>
        <Select label="Number" value={lineId} onChange={setLineId}>
          <option value="">Any of your numbers</option>
          {lines.map(l => <option key={l.id} value={l.id}>{numberName(l)}</option>)}
        </Select>
        <Select label="Show" value={recordedOnly ? 'recorded' : 'all'}
          onChange={v => setRecordedOnly(v === 'recorded')}
          help="A call with no audio is still listed under “Every call” — it may have failed or gone unanswered.">
          <option value="recorded">Calls with a recording</option>
          <option value="all">Every call</option>
        </Select>
      </div>
      <div className="row small muted wrap" style={{gap: 10}}>
        <span>{shown.length} {shown.length === 1 ? 'call' : 'calls'} on this page</span>
        {hiddenByRecorded > 0 && <button className="link-btn" onClick={() => setRecordedOnly(false)}>
          {hiddenByRecorded} more without a recording — show {hiddenByRecorded === 1 ? 'it' : 'them'}
        </button>}
        {filtered && <button className="link-btn" onClick={clearFilters}>Clear filters</button>}
      </div>
    </div>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    {calls === null ? <Loading label="Loading your calls…"/>
      : shown.length === 0
        ? <Empty icon={filtered ? Search : Phone}
            title={filtered ? 'No calls match' : recordedOnly ? 'No recordings yet' : 'No calls yet'}
            body={filtered
              ? 'Try clearing a filter, or look at an earlier page.'
              : recordedOnly
                ? 'Recorded calls appear here with their audio and transcript. Switch “Show” to every call if you are looking for one that failed or went unanswered.'
                : 'When someone rings a connected number, or your agent calls out, the call appears here with its recording and transcript.'}
            action={filtered ? <Button variant="outline" onClick={clearFilters}>Clear filters</Button>
              : <Button variant="outline" to="/app/numbers">Connect a number</Button>}/>
        : <div className="card">
            <div className="table-wrap"><table className="table stack-on-phone">
              <thead><tr>
                <th>When</th><th>Who</th><th>On</th><th>Handled by</th>
                <th>Length</th><th>Result</th><th>Audio</th><th>Call</th>
              </tr></thead>
              <tbody>{shown.map(call => <tr key={call.id} className="row-link" tabIndex={0}
                role="link" aria-label={`Open the call with ${call.counterparty}`}
                onClick={() => navigate('/app/recordings/' + call.id)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    navigate('/app/recordings/' + call.id);
                  }
                }}>
                <td data-label="When">{formatWhen(call.startedAt)}</td>
                <td data-label="Who">
                  <div className="mono">{call.counterparty || 'unknown'}</div>
                  <Who call={call}/>
                </td>
                <td data-label="On">
                  <div className="small">{CHANNEL_LABEL[call.channel] ?? call.channel}</div>
                  <div className="small muted mono">{lineLabel(call)}</div>
                </td>
                <td data-label="Handled by"><HandledBy call={call}/></td>
                <td data-label="Length" className="mono">{formatDuration(call.durationSeconds)}</td>
                <td data-label="Result">
                  <Badge tone={statusTone(call.status)}>{STATUS_LABEL[call.status]}</Badge>
                  {call.error && <div className="small muted">{call.error}</div>}
                </td>
                <td data-label="Recording" onClick={stop}>
                  {call.recording.available
                    ? <span className="row" style={{gap: 6}}>
                        <button className="player-button" style={{width: 32, height: 32}}
                          disabled={player.loading === call.id}
                          title={player.playing === call.id ? 'Pause' : 'Play this recording'}
                          aria-label={player.playing === call.id ? 'Pause' : 'Play this recording'}
                          onClick={() => void player.toggle(call.id, toast)}>
                          {player.loading === call.id ? <Loader2 size={14} className="spin"/>
                            : player.playing === call.id ? <Pause size={14}/> : <Play size={14}/>}
                        </button>
                        <button className="icon-btn" title="Download the audio"
                          onClick={() => void callsApi.download(call.id)}><Download size={15}/></button>
                        <button className="icon-btn" title="Delete the audio, keep the call record"
                          aria-label="Delete the audio" onClick={() => removeRecording(call)}>
                          <AudioLines size={15}/><X size={11} style={{marginInlineStart: -4}}/>
                        </button>
                      </span>
                    : <span className="small muted">{RECORDING_LABEL[call.recording.state]}</span>}
                </td>
                <td data-label="" onClick={stop}>
                  <div className="row" style={{gap: 6}}>
                    <Link className="button outline small" to={'/app/recordings/' + call.id}>Open</Link>
                    {/* The server refuses this for an employee, so offering
                        it would only be a button that fails. */}
                    {canManage && <button className="icon-btn"
                      title="Delete the whole call — record, audio and transcript"
                      aria-label="Delete the whole call" onClick={() => remove(call)}>
                      <Trash2 size={15}/>
                    </button>}
                  </div>
                </td>
              </tr>)}</tbody>
            </table></div>
          </div>}

    <div className="row between">
      <Button variant="outline" small disabled={page === 0 || busy}
        onClick={() => setPage(p => Math.max(0, p - 1))}>Previous</Button>
      <span className="small muted">Page {page + 1}</span>
      <Button variant="outline" small disabled={busy || (calls ?? []).length < PAGE}
        onClick={() => setPage(p => p + 1)}>Next</Button>
    </div>
    {confirmDialog}
  </div>;
}

// ---------------------------------------------------------------------------
// One call
// ---------------------------------------------------------------------------

export function CallDetailScreen() {
  const {id} = useParams();
  const {toast, canManage} = useApp();
  const navigate = useNavigate();
  const [confirm, confirmDialog] = useConfirm();
  const [call, setCall] = useState<WireCall | null>(null);
  const [line, setLine] = useState<PhoneNumber | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const row = await callsApi.get(id);
      setCall(row);
      setError('');
      if (row.lineId) {
        numbersApi.list().then(b => setLine(b.numbers.find(n => n.id === row.lineId) ?? null)).catch(() => {});
      }
    } catch (cause) {
      setError(errorText(cause, 'Could not load this call.'));
    }
  }, [id]);
  useEffect(() => {void load()}, [load]);

  // This is the page somebody sits on while the call they just placed is
  // still running, so it is the one that most has to be right: the status,
  // the duration and the recording all arrive after the page did.
  useSettlingPoll(anythingInFlight(call ? [call] : []), load);

  if (!call) return <div className="stack">
    <PageHead eyebrow="Calls" title="Call"/>
    {error ? <ErrorNote error={error} onRetry={() => void load()}/> : <Loading/>}
  </div>;

  const fetchFromCarrier = async () => {
    setBusy('fetch');
    try {
      const result = await callsApi.fetchFromCarrier(call.id);
      toast(result.detail ?? (result.status === 'ready'
        ? 'The recording is stored and ready to play.'
        : `The carrier says: ${result.status}.`));
      await load();
    } catch (cause) {
      toast(errorText(cause, 'The carrier would not hand over the recording.'));
    } finally {setBusy('')}
  };

  const removeRecording = () => confirm({
    title: 'Delete this recording?',
    body: <>The audio is erased. This call's record, transcript and summary stay.</>,
    confirmLabel: 'Delete recording',
    onConfirm: async () => {await callsApi.deleteRecording(call.id); await load()},
  });

  const removeCall = () => confirm({
    title: 'Delete this call?',
    body: <>The whole record — recording, transcript and summary — is deleted for
      everyone. This cannot be undone.</>,
    confirmLabel: 'Delete call',
    onConfirm: async () => {await callsApi.remove(call.id); navigate('/app/recordings')},
  });

  const facts: [string, React.ReactNode][] = [
    ['Started', formatWhen(call.startedAt)],
    ['Answered', call.answeredAt ? formatWhen(call.answeredAt) : 'never answered'],
    ['Length', formatDuration(call.durationSeconds)],
    ['Channel', CHANNEL_LABEL[call.channel] ?? call.channel],
    ['Your number', line ? numberName(line) : call.fromNumber || '—'],
    ['Direction', call.direction === 'OUTBOUND' ? 'we called them' : 'they called us'],
    ['Handled by', call.mode === 'human' ? `a person${call.placedBy ? ` (${call.placedBy})` : ''}`
      : call.handledBy === 'operator' ? 'the agent, then a person took over' : 'the agent'],
    ['Answered from', call.knowledgeBaseName
      ? `${call.knowledgeBaseName}${call.resolvedBy ? ` — chosen by ${RESOLVED_BY_LABEL[call.resolvedBy] ?? call.resolvedBy}` : ''}`
      : 'everything the company has uploaded'],
  ];

  return <div className="stack">
    <PageHead eyebrow="Calls" title={call.counterparty || 'Unknown number'}
      description={`${CHANNEL_LABEL[call.channel] ?? call.channel} · ${formatWhen(call.startedAt)}`}
      action={<div className="row wrap">
        <Button variant="outline" to="/app/recordings"><ArrowLeft size={15}/> All calls</Button>
        {canManage && <Button variant="outline" onClick={removeCall}>
          <Trash2 size={15}/> Delete call
        </Button>}
      </div>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    <div className="row wrap">
      <Badge tone={statusTone(call.status)}>{STATUS_LABEL[call.status]}</Badge>
      {call.topic && <span className="badge raw">{call.topic}</span>}
      {call.language && <Badge>{call.language}</Badge>}
    </div>
    {call.error && <div className="notice danger">{call.error}</div>}

    <div className="split">
      <div className="stack">
        <div className="card stack">
          <h2>Recording</h2>
          {call.recording.available
            ? <>
                <RecordingPlayer callId={call.id} getUrl={callsApi.playbackUrl}
                  seconds={call.durationSeconds}/>
                <div className="row wrap small muted" style={{gap: 14}}>
                  <span>{formatBytes(call.recording.bytes ?? 0)}</span>
                  <span>{call.recording.mime}</span>
                </div>
                <div className="row wrap">
                  <Button variant="outline" onClick={() => void callsApi.download(call.id)}>
                    <Download size={15}/> Download
                  </Button>
                  {canManage && <Button variant="outline" onClick={removeRecording}>
                    <Trash2 size={15}/> Delete recording
                  </Button>}
                </div>
              </>
            : <>
                <p className="muted small" style={{margin: 0}}>
                  {call.recording.state === 'PENDING'
                    ? 'The call is still being recorded, or the audio has not arrived yet.'
                    : call.recording.state === 'ABSENT'
                      ? 'The call ended and no recording ever arrived.'
                      : call.recording.state === 'FAILED'
                        ? call.recording.error || 'The recording failed.'
                        : 'This call was not recorded.'}
                </p>
                {call.channel === 'PHONE' && call.recording.state !== 'NONE' &&
                  <div className="row">
                    <Button variant="outline" disabled={busy === 'fetch'} onClick={() => void fetchFromCarrier()}>
                      <RefreshCw size={15}/> {busy === 'fetch' ? 'Asking the carrier…' : 'Fetch from the carrier'}
                    </Button>
                  </div>}
              </>}
        </div>

        <div className="card stack">
          <div className="row between">
            <h2 style={{margin: 0}}>What was said</h2>
            {call.transcript && <Button small variant="outline"
              onClick={() => {
                void navigator.clipboard?.writeText(call.transcript ?? '');
                toast('Transcript copied');
              }}>Copy</Button>}
          </div>
          {call.summary && <div className="notice">{call.summary}</div>}
          {call.transcript
            ? <Transcript text={call.transcript}/>
            : <Empty icon={FileText} title="No transcript"
                body={call.durationSeconds
                  ? 'This call was not transcribed. A call a person took in the browser has no transcript, since no model was listening.'
                  : 'Nobody spoke on this call, so there is nothing to transcribe.'}/>}
        </div>
      </div>

      <div className="card stack">
        <h2>Details</h2>
        {facts.map(([label, value]) => <div className="row between" key={label} style={{gap: 12}}>
          <span className="small muted">{label}</span>
          <span className="small" style={{textAlign: 'end', overflowWrap: 'anywhere'}}>{value}</span>
        </div>)}
        <div className="row between" style={{gap: 12}}>
          <span className="small muted">Call id</span>
          <span className="small mono">{call.id}</span>
        </div>
      </div>
    </div>
    {confirmDialog}
  </div>;
}
