/**
 * The company's first screen.
 *
 * The one it replaces was written against the prototype's fixtures: its
 * "numbers needing attention" counted fixture channels, its unanswered
 * questions came from a fixture table, and its chart drew fourteen days of
 * sample calls. Against a real backend every one of those was either empty or
 * invented — the worst possible thing for the screen somebody opens first.
 *
 * This reads the same endpoints the rest of the workspace does, and shows
 * nothing it cannot source. While setup is unfinished the checklist leads; once
 * calls are flowing, the numbers do.
 */
import {useCallback, useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {
  Activity, ArrowRight, BrainCircuit, CircleAlert, Clock, MessageSquare, Mic2, Phone,
  PhoneIncoming, PhoneOutgoing, RefreshCw,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {ErrorNote, Loading} from './ui';
import {SetupChecklist} from './pages-numbers';
import {
  CHANNEL_LABEL, STATUS_LABEL, callsApi, formatDuration, formatWhen, statusTone,
  type WireCall,
} from './lib/api/calls';
import {numberName, numbers as numbersApi, type PhoneNumber} from './lib/api/numbers';
import {workspace, type Agent} from './lib/api/workspace';
import {request} from './lib/api/http';

type Stats = {
  total?: number; last24h?: number; answered24h?: number; failed24h?: number;
  handedOff24h?: number; recordings24h?: number; averageSeconds?: number;
};

type Gap = {question: string; askedCount?: number; count?: number};

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function Tile({label, value, hint, to, tone}: {
  label: string; value: React.ReactNode; hint?: string; to?: string; tone?: string;
}) {
  const inner = <>
    <div className="stat-label">{label}</div>
    <div className="stat-number mono" style={tone ? {color: `var(--${tone})`} : undefined}>{value}</div>
    {hint && <div className="small muted">{hint}</div>}
  </>;
  return to ? <Link className="card lift" to={to}>{inner}</Link> : <div className="card">{inner}</div>;
}

export function DashboardScreen() {
  const {org, t} = useApp();
  const [stats, setStats] = useState<Stats | null>(null);
  const [calls, setCalls] = useState<WireCall[]>([]);
  const [liveCount, setLiveCount] = useState(0);
  const [lines, setLines] = useState<PhoneNumber[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      // Fetched together, and a failure in one does not blank the rest: a
      // company with no gaps endpoint data still deserves its call counts.
      const [statsResult, callsResult, numbersResult, agentsResult, gapsResult, liveResult] =
        await Promise.allSettled([
          callsApi.stats(),
          callsApi.list({limit: 8}),
          numbersApi.list(),
          workspace.agents(),
          request<{gaps?: Gap[]; questions?: Gap[]}>('/api/gaps', {query: {limit: 5}}),
          request<{total: number}>('/api/calls/live'),
        ]);
      if (statsResult.status === 'fulfilled') setStats(statsResult.value);
      if (callsResult.status === 'fulfilled') setCalls(callsResult.value.calls);
      if (numbersResult.status === 'fulfilled') setLines(numbersResult.value.numbers);
      if (agentsResult.status === 'fulfilled') setAgents(agentsResult.value.agents);
      if (gapsResult.status === 'fulfilled') {
        setGaps(gapsResult.value.gaps ?? gapsResult.value.questions ?? []);
      }
      if (liveResult.status === 'fulfilled') setLiveCount(liveResult.value.total ?? 0);
      setError(statsResult.status === 'rejected'
        ? errorText(statsResult.reason, 'Could not load your figures.') : '');
    } finally {setBusy(false)}
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const verified = lines.filter(n => n.status === 'verified');
  const broken = lines.filter(n => n.status === 'failed');
  const unanswered = verified.filter(n => n.mode !== 'outbound' && !n.inboundAgentId);
  const answered = stats?.answered24h ?? 0;
  const day = stats?.last24h ?? 0;
  const answerRate = day ? Math.round((answered / day) * 100) : 0;

  if (stats === null && !error) return <div className="stack">
    <PageHead eyebrow="Overview" title={org.name}/>
    <Loading label="Loading your workspace…"/>
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Overview" title={org.name}
      description="What your agent has been doing, and anything that needs you."
      action={<div className="row wrap">
        {liveCount > 0 && <Link className="button" to="/app/live">
          <Activity size={15}/> {liveCount} {liveCount === 1 ? 'call live now' : 'calls live now'}
        </Link>}
        <Button variant="outline" onClick={() => void load()} disabled={busy}>
          <RefreshCw size={15}/> {t('Refresh')}
        </Button>
      </div>}/>

    {error && <ErrorNote error={error} onRetry={() => void load()}/>}

    <SetupChecklist/>

    {/* Things that are actually wrong, each with the one link that fixes it. */}
    {broken.length > 0 && <div className="notice danger">
      <strong>{broken.length === 1 ? 'A number is not verified' : `${broken.length} numbers are not verified`}.</strong>{' '}
      {broken.map(n => n.phoneNumber).join(', ')} — the provider refused the credentials, so
      calls on {broken.length === 1 ? 'it' : 'them'} will not work.{' '}
      <Link to="/app/numbers">Fix the credentials →</Link>
    </div>}
    {unanswered.length > 0 && <div className="notice warning">
      <strong>Nobody is answering {unanswered.map(n => n.phoneNumber).join(', ')}.</strong>{' '}
      The number is verified but has no inbound agent, so incoming calls are declined.{' '}
      <Link to="/app/numbers">Choose an agent →</Link>
    </div>}

    <div className="stat-grid">
      <Tile label="Calls in the last 24 hours" value={day}
        hint={day ? `${answered} answered` : 'nothing yet today'} to="/app/recordings"/>
      <Tile label="Answered" value={day ? `${answerRate}%` : '—'}
        tone={day && answerRate < 70 ? 'warning' : undefined}
        hint={stats?.failed24h ? `${stats.failed24h} failed` : undefined}/>
      <Tile label="Average call" value={formatDuration(stats?.averageSeconds ?? 0)}/>
      <Tile label="Calls all time" value={(stats?.total ?? 0).toLocaleString()} to="/app/recordings"/>
      <Tile label="Numbers live" value={`${verified.length}/${lines.length}`}
        hint={lines.length ? undefined : 'none connected'} to="/app/numbers"/>
      <Tile label="Agents" value={agents.length}
        hint={agents.filter(a => a.status === 'live').length + ' live'} to="/app/agents"/>
    </div>

    <div className="split">
      <div className="card stack">
        <div className="row between">
          <h2 style={{margin: 0}}>Recent calls</h2>
          <Button small variant="outline" to="/app/recordings">See all</Button>
        </div>
        {calls.length === 0
          ? <Empty icon={Phone} title="No calls yet"
              body="Once a number is verified and has an agent, calls to it appear here."/>
          : <div className="table-wrap"><table className="table stack-on-phone">
              <thead><tr><th>When</th><th>Who</th><th>On</th><th>Length</th><th>Result</th></tr></thead>
              <tbody>{calls.map(call => <tr key={call.id}>
                <td data-label="When"><Link to={'/app/recordings/' + call.id}>{formatWhen(call.startedAt)}</Link></td>
                <td data-label="Who">
                  <span className="row small" style={{gap: 5}}>
                    {call.direction === 'OUTBOUND' ? <PhoneOutgoing size={12}/> : <PhoneIncoming size={12}/>}
                    <span className="mono">{call.counterparty || 'unknown'}</span>
                  </span>
                </td>
                <td data-label="On" className="small">{CHANNEL_LABEL[call.channel] ?? call.channel}</td>
                <td data-label="Length" className="mono">{formatDuration(call.durationSeconds)}</td>
                <td data-label="Result">
                  <Badge tone={statusTone(call.status)}>{STATUS_LABEL[call.status]}</Badge>
                </td>
              </tr>)}</tbody>
            </table></div>}
      </div>

      <div className="stack">
        <div className="card stack">
          <div className="row between">
            <h2 style={{margin: 0}}>Questions it could not answer</h2>
            {gaps.length > 0 && <Button small variant="outline" to="/app/unanswered">All</Button>}
          </div>
          {gaps.length === 0
            ? <p className="small muted" style={{margin: 0}}>
                Nothing yet. When a caller asks something your documents do not cover,
                it is listed here so you know what to upload next.
              </p>
            : <div className="stack">
                {gaps.slice(0, 5).map((gap, index) => <div className="row between" key={index} style={{gap: 10}}>
                  <span className="small">{gap.question}</span>
                  <span className="small muted mono">{gap.askedCount ?? gap.count ?? 1}×</span>
                </div>)}
                <Button small variant="outline" to="/app/knowledge">
                  <BrainCircuit size={14}/> Add the answers
                </Button>
              </div>}
        </div>

        <div className="card stack">
          <h2 style={{margin: 0}}>Your numbers</h2>
          {lines.length === 0
            ? <Button variant="outline" to="/app/numbers"><Phone size={15}/> Connect your first number</Button>
            : lines.slice(0, 5).map(line => <div className="row between" key={line.id} style={{gap: 10}}>
                <div className="list-row-main">
                  <span className="small mono">{numberName(line)}</span>
                  <div className="small muted">
                    {line.kind === 'whatsapp' ? 'WhatsApp' : 'Phone'} · {line.mode}
                  </div>
                </div>
                <Badge tone={line.status === 'verified' ? 'success' : line.status === 'failed' ? 'danger' : 'warning'}>
                  {line.status}
                </Badge>
              </div>)}
          {lines.length > 0 && <Link className="link-btn small" to="/app/numbers">
            Manage numbers <ArrowRight size={13}/>
          </Link>}
        </div>

        <div className="card stack">
          <h2 style={{margin: 0}}>Do something</h2>
          <Link className="nav-link" to="/app/dialer"><Phone size={16}/> Call someone from the dialer</Link>
          <Link className="nav-link" to="/app/campaigns"><PhoneOutgoing size={16}/> Work through a call list</Link>
          <Link className="nav-link" to="/app/messages"><MessageSquare size={16}/> Answer WhatsApp messages</Link>
          <Link className="nav-link" to="/app/agents"><Mic2 size={16}/> Change what the agent says</Link>
          <Link className="nav-link" to="/app/guides"><Clock size={16}/> Read the setup guides</Link>
        </div>
      </div>
    </div>

    {day === 0 && (stats?.total ?? 0) === 0 && verified.length > 0 && <div className="notice">
      <CircleAlert size={14}/> No calls have come in yet. Try the dialer, or ring your own
      number from a phone to hear what a caller hears.
    </div>}
  </div>;
}
