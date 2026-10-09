/**
 * Call history, and one call in full.
 *
 * Written against the API rather than the prototype's fixture store. The old
 * screen filtered on fields the backend has never returned (`outcome`,
 * `channelId`, a separate transcript table), so most of its nine filters did
 * nothing and its recording column could not play anything. These filter on
 * what `GET /api/calls` actually takes, and the recording is playable,
 * downloadable and deletable from the row it belongs to.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {
  ArrowDownLeft, ArrowLeft, ArrowUpRight, Download, FileText, Mic2, Phone, RefreshCw,
  Search, Trash2, User,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
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

export function CallHistoryScreen() {
  const {t} = useApp();
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
  const [busy, setBusy] = useState(false);

  // Searching is a server-side filter on an exact number, so it waits for the
  // typing to stop rather than firing a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => {setQuery(search.trim()); setPage(0)}, 400);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const body = await callsApi.list({
        limit: PAGE, offset: page * PAGE,
        status: status || undefined, channel: (channel || undefined) as never,
        counterparty: query.length >= 3 ? query : undefined,
      });
      setCalls(body.calls);
      setError('');
    } catch (cause) {
      setError(errorText(cause, 'Could not load your calls.'));
    } finally {setBusy(false)}
  }, [page, status, channel, query]);

  useEffect(() => {void load()}, [load]);
  useEffect(() => {numbersApi.list().then(b => setLines(b.numbers)).catch(() => {})}, []);

  // Direction and number are filtered here rather than server-side: the API
  // takes neither, and both are cheap on a page of fifty.
  const shown = useMemo(() => (calls ?? []).filter(c =>
    (!direction || c.direction === direction) && (!lineId || c.lineId === lineId)), [calls, direction, lineId]);

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

  const clearFilters = () => {
    setStatus(''); setChannel(''); setDirection(''); setLineId(''); setSearch(''); setPage(0);
  };
  const filtered = Boolean(status || channel || direction || lineId || query);

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Call history"
      description="Every call this company has made or answered. Open one to read what was said and hear it."
      action={<Button variant="outline" onClick={() => void load()} disabled={busy}>
        <RefreshCw size={15}/> {t('Refresh')}
      </Button>}/>

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
      </div>
      {filtered && <div className="row small muted" style={{gap: 10}}>
        <span>{shown.length} {shown.length === 1 ? 'call' : 'calls'} on this page match</span>
        <button className="link-btn" onClick={clearFilters}>Clear filters</button>
      </div>}
    </div>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    {calls === null ? <Loading label="Loading your calls…"/>
      : shown.length === 0
        ? <Empty icon={filtered ? Search : Phone}
            title={filtered ? 'No calls match' : 'No calls yet'}
            body={filtered
              ? 'Try clearing a filter, or look at an earlier page.'
              : 'When someone rings a connected number, or your agent calls out, the call appears here with its recording and transcript.'}
            action={filtered ? <Button variant="outline" onClick={clearFilters}>Clear filters</Button>
              : <Button variant="outline" to="/app/numbers">Connect a number</Button>}/>
        : <div className="card">
            <div className="table-wrap"><table className="table stack-on-phone">
              <thead><tr>
                <th>When</th><th>Who</th><th>On</th><th>Handled by</th>
                <th>Length</th><th>Result</th><th>Recording</th><th/>
              </tr></thead>
              <tbody>{shown.map(call => <tr key={call.id}>
                <td data-label="When"><Link to={'/app/history/' + call.id}>{formatWhen(call.startedAt)}</Link></td>
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
                <td data-label="Recording">
                  {call.recording.available
                    ? <span className="row" style={{gap: 6}}>
                        <Badge tone="success">ready</Badge>
                        <button className="icon-btn" title="Download"
                          onClick={() => void callsApi.download(call.id)}><Download size={15}/></button>
                        <button className="icon-btn" title="Delete recording"
                          onClick={() => removeRecording(call)}><Trash2 size={15}/></button>
                      </span>
                    : <span className="small muted">{RECORDING_LABEL[call.recording.state]}</span>}
                </td>
                <td data-label="">
                  <div className="row" style={{gap: 6}}>
                    <Link className="button outline small" to={'/app/history/' + call.id}>Open</Link>
                    <button className="icon-btn" title="Delete call" onClick={() => remove(call)}>
                      <Trash2 size={15}/>
                    </button>
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
  const {toast} = useApp();
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
    onConfirm: async () => {await callsApi.remove(call.id); navigate('/app/history')},
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
        <Button variant="outline" to="/app/history"><ArrowLeft size={15}/> All calls</Button>
        <Button variant="outline" onClick={removeCall}><Trash2 size={15}/> Delete call</Button>
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
                  <Button variant="outline" onClick={removeRecording}>
                    <Trash2 size={15}/> Delete recording
                  </Button>
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
          <h2>What was said</h2>
          {call.summary && <div className="notice">{call.summary}</div>}
          {call.transcript
            ? <pre className="transcript">{call.transcript}</pre>
            : <Empty icon={FileText} title="No transcript"
                body="A transcript is kept for calls the agent handled. A call a person took in the browser has none."/>}
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
