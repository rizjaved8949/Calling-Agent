/**
 * The dialer: call a customer from one of the company's numbers.
 *
 * Two ways to make that call, and the choice is the first thing on the page:
 *
 *   * **You speak.** The server starts the call in "human" mode and, once the
 *     customer answers, the browser's microphone and speaker are joined to it.
 *     No agent is involved. Hanging up here hangs up the call.
 *   * **The agent speaks.** The call is placed with an agent on it and nobody
 *     here has to say anything. This used to be a form buried at the top of
 *     the Live page, which meant the two ways of calling the same person lived
 *     on two different screens with no hint that the other existed.
 *
 * Either way the customer sees the company's number, not the employee's
 * phone. Both are open to staff as well as owners: placing a call is the work
 * an employee is here to do.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {
  Bot, Delete, Info, Loader2, MessageCircle, Mic, Phone, PhoneCall, PhoneOff,
  User,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {canCallOut, numberName, numbers as api, type PhoneNumber} from './lib/api/numbers';
import {workspace, type Agent} from './lib/api/workspace';
import {joinCallAsOperator, type OperatorCall} from './lib/operator';

type Phase = 'idle' | 'dialing' | 'ringing' | 'connected' | 'ended';
/** Who does the talking. */
type Mode = 'human' | 'agent';

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, '0')}`;
}

/** The letters under the digits, as every phone has had for a century. */
const KEYS: [string, string][] = [
  ['1', ''], ['2', 'ABC'], ['3', 'DEF'],
  ['4', 'GHI'], ['5', 'JKL'], ['6', 'MNO'],
  ['7', 'PQRS'], ['8', 'TUV'], ['9', 'WXYZ'],
  ['+', ''], ['0', ''], ['#', ''],
];

export function DialerScreen() {
  const {session, toast} = useApp();
  const [mode, setMode] = useState<Mode>('human');
  const [lines, setLines] = useState<PhoneNumber[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [lineId, setLineId] = useState('');
  const [agentId, setAgentId] = useState('');
  const [to, setTo] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [status, setStatus] = useState('');
  const [seconds, setSeconds] = useState(0);
  const [callId, setCallId] = useState('');
  const [transcript, setTranscript] = useState('');
  const [busy, setBusy] = useState(false);
  const audio = useRef<OperatorCall | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const joining = useRef(false);
  const transcriptEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.list().then(b => {
      const usable = b.numbers.filter(canCallOut);
      setLines(usable);
      if (usable[0]) setLineId(id => id || usable[0].id);
    }).catch(() => setLines([]));
    // Only agents that are set up to make calls. Offering one that is not
    // would be a call placed and immediately refused.
    workspace.agents()
      .then(b => setAgents(b.agents.filter(a => a.placesCalls)))
      .catch(() => {});
  }, []);

  const stopPolling = () => {if (poll.current) {clearInterval(poll.current); poll.current = null}};
  // Leaving the page hangs up a call you are speaking on: an open microphone
  // nobody is looking at is the one outcome worse than a dropped call. An
  // agent call is left running, because it does not need this tab.
  useEffect(() => () => {stopPolling(); audio.current?.leave()}, []);

  useEffect(() => {
    if (phase !== 'connected') return;
    const timer = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({block: 'end'});
  }, [transcript]);

  const line = lines?.find(l => l.id === lineId);
  const inCall = phase === 'dialing' || phase === 'ringing' || phase === 'connected';

  /** The agent this call would run on, named or inherited from the number. */
  const chosenAgent = useMemo(() => {
    if (agentId) return agents.find(a => a.id === agentId) ?? null;
    return agents.find(a => a.id === line?.outboundAgentId) ?? null;
  }, [agentId, agents, line]);

  const finish = useCallback((text: string) => {
    stopPolling();
    audio.current?.leave();
    audio.current = null;
    setPhase('ended');
    setStatus(text);
  }, []);

  const watch = (id: string, forMode: Mode) => {
    stopPolling();
    poll.current = setInterval(async () => {
      try {
        const s = await api.callSession(id);
        if (s.transcript) setTranscript(s.transcript);

        if (forMode === 'agent') {
          // Nothing to join. The call runs on the server and this is only a
          // window onto it.
          if (!s.live) {
            finish(s.status === 'NO_ANSWER' ? 'Nobody answered.'
              : s.error ? `The call failed: ${s.error}` : 'The call ended.');
            return;
          }
          if (s.status === 'IN_PROGRESS') {
            // Set unconditionally rather than guarded on `phase`: this closure
            // was made when the call was placed and its `phase` is frozen at
            // that moment, so reading it here would always say "ringing".
            setPhase('connected');
            setStatus('The agent is talking to them.');
          }
          return;
        }

        if (audio.current) {
          // Still polled while talking: the customer hanging up ends the
          // session on the server, and this is how the browser hears of it.
          if (!s.live) finish('They hung up.');
          return;
        }
        if (s.live && s.operatorToken && !joining.current) {
          joining.current = true;
          try {
            audio.current = await joinCallAsOperator(id, s.operatorToken, event => {
              if (event.type === 'error') toast(event.detail ?? 'The call audio failed.');
              if (event.type === 'left') finish('Call ended.');
            });
            setSeconds(0);
            setPhase('connected');
            setStatus('Connected — you are talking to them.');
          } catch (cause) {
            toast(cause instanceof Error ? cause.message : 'Could not open the microphone.');
            void api.endCall(id).catch(() => {});
            finish('Call ended: the microphone could not be opened.');
          }
          return;
        }
        if (!s.live) {
          finish(s.status === 'NO_ANSWER' ? 'Nobody answered.'
            : s.error ? `The call failed: ${s.error}` : 'The call ended.');
        } else {
          setPhase('ringing');
          setStatus('Ringing…');
        }
      } catch {/* a missed poll is retried on the next tick */}
    }, 1500);
  };

  const call = async () => {
    if (!line || !to.trim()) return;
    if (mode === 'agent' && !chosenAgent) {
      toast('Choose an agent, or give this number an outbound agent on the Numbers page.');
      return;
    }
    setBusy(true);
    joining.current = false;
    setTranscript('');
    setSeconds(0);
    setPhase('dialing');
    setStatus(mode === 'agent' ? 'Placing the call…' : 'Placing the call…');
    try {
      const placed = await api.placeCall({
        to: to.trim(),
        channel: line.kind === 'whatsapp' ? 'WHATSAPP_CALL' : 'PHONE',
        lineId: line.id,
        human: mode === 'human',
        agentId: mode === 'agent' ? agentId : undefined,
        placedBy: session?.email,
      });
      setCallId(placed.id);
      setPhase('ringing');
      setStatus('Ringing…');
      watch(placed.id, mode);
    } catch (cause) {
      setPhase('idle');
      setStatus('');
      toast(cause instanceof Error ? cause.message : 'The call could not be placed.');
    } finally {setBusy(false)}
  };

  const hangUp = async () => {
    const id = callId;
    finish('Call ended.');
    if (id) await api.endCall(id).catch(() => {});
  };

  const askPermission = async () => {
    if (!line || !to.trim()) return;
    try {
      await api.askPermission(to.trim(), line.id);
      toast('Sent. They will get a WhatsApp message with an “Allow calls” button.');
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : 'Could not send the request.');
    }
  };

  const reset = () => {
    setPhase('idle'); setStatus(''); setCallId(''); setTranscript(''); setSeconds(0);
  };

  if (lines !== null && lines.length === 0) return <div className="stack">
    <PageHead eyebrow="Calls" title="Dialer"/>
    <Empty icon={Phone} title="No number to call from"
      body="The dialer uses one of your company’s verified numbers set to outbound or both. Ask an owner to connect one."
      action={<Button variant="outline" to="/app/numbers">Go to Numbers</Button>}/>
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Dialer"
      description="Call someone from your company’s number — speak to them yourself, or have the agent do it. They see the company number either way."/>

    <div>
      <div className="dial-modes">
        <button type="button" className="dial-mode" aria-pressed={mode === 'human'}
          disabled={inCall} onClick={() => setMode('human')}>
          <span className="dial-mode-icon"><User size={19}/></span>
          <span>
            <strong>I’ll talk to them</strong>
            <div className="help">
              You speak through this browser. It asks for your microphone the
              first time.
            </div>
          </span>
        </button>
        <button type="button" className="dial-mode" aria-pressed={mode === 'agent'}
          disabled={inCall} onClick={() => setMode('agent')}>
          <span className="dial-mode-icon"><Bot size={19}/></span>
          <span>
            <strong>The agent talks to them</strong>
            <div className="help">
              Your agent does the whole call. You can watch what is said and
              close this tab.
            </div>
          </span>
        </button>
      </div>

      <div className="dialer">
        {/* ---- the handset ---- */}
        <div className="card dial-pad">
          <div className="field">
            <label>Call from</label>
            <select className="select" value={lineId} disabled={inCall}
              onChange={e => setLineId(e.target.value)}>
              {(lines ?? []).map(l => <option key={l.id} value={l.id}>
                {l.kind === 'whatsapp' ? 'WhatsApp · ' : 'Phone · '}{numberName(l)}
              </option>)}
            </select>
          </div>

          {mode === 'agent' && <div className="field">
            <label>Who answers for you</label>
            <select className="select" value={agentId} disabled={inCall}
              onChange={e => setAgentId(e.target.value)}>
              <option value="">
                {line?.outboundAgentId && agents.some(a => a.id === line.outboundAgentId)
                  ? `This number’s agent (${agents.find(a => a.id === line.outboundAgentId)?.name})`
                  : 'Choose an agent'}
              </option>
              {agents.map(a => <option key={a.id} value={a.id}>
                {a.name}{a.status !== 'live' ? ` (${a.status})` : ''}
              </option>)}
            </select>
            {agents.length === 0 && <div className="help">
              No agent is set up to make calls. <Link to="/app/agents">Create one</Link> and
              tick “Make calls out”.
            </div>}
          </div>}

          <div className="dial-readout">
            <input value={to} disabled={inCall} placeholder="+92 300 1234567"
              inputMode="tel" aria-label="Number to call"
              onChange={e => setTo(e.target.value)}
              onKeyDown={e => {if (e.key === 'Enter') void call()}}/>
            <button type="button" className="dial-back" disabled={inCall || !to}
              aria-label="Delete the last digit"
              onClick={() => setTo(v => v.slice(0, -1))}>
              <Delete size={18}/>
            </button>
          </div>

          <div className="dial-keys">
            {KEYS.map(([key, letters]) => <button key={key} type="button" className="dial-key"
              disabled={inCall} onClick={() => setTo(v => v + key)}>
              <span>{key}{letters && <small>{letters}</small>}</span>
            </button>)}
          </div>

          {inCall
            ? <button type="button" className="dial-go hang" onClick={() => void hangUp()}
                aria-label="Hang up"><PhoneOff size={24}/></button>
            : <button type="button" className="dial-go" onClick={() => void call()}
                disabled={!to.trim() || !line || busy} aria-label="Call">
                {busy ? <Loader2 size={24} className="spin"/> : <PhoneCall size={24}/>}
              </button>}

          {line?.kind === 'whatsapp' && !inCall && <div className="dial-note">
            <Info size={14}/>
            <span>
              WhatsApp only lets a business call someone who has allowed it.
              {' '}<button className="link-btn" onClick={() => void askPermission()}>
                <MessageCircle size={13}/> Ask this person for permission
              </button>
            </span>
          </div>}
        </div>

        {/* ---- the call ---- */}
        <div className={'card dial-stage' + (phase === 'idle' && !status ? '' : ' busy')}>
          {phase === 'idle' && !status
            ? <div className="dial-stage-empty">
                <span className="dial-orb">
                  {mode === 'agent' ? <Bot size={30}/> : <Mic size={30}/>}
                </span>
                <div>
                  <strong>{mode === 'agent' ? 'The agent is ready' : 'Ready when you are'}</strong>
                  <p className="muted small" style={{marginTop: 6}}>
                    {mode === 'agent'
                      ? chosenAgent
                        ? <>Type a number and press call. <b>{chosenAgent.name}</b> does the
                            talking, and what is said appears here as it happens.</>
                        : <>Choose an agent above, then type a number and press call.</>
                      : <>Type a number and press call. You do the talking — no agent is
                          on this call.</>}
                  </p>
                </div>
                {line && <div className="small muted">
                  They will see <b>{line.phoneNumber}</b>, not your own number.
                </div>}
              </div>
            : <>
                <div className="dial-live">
                  <span className={'dial-orb ' + (phase === 'connected' ? 'connected'
                    : phase === 'ended' ? 'ended' : 'ringing')}>
                    {phase === 'connected'
                      ? (mode === 'agent' ? <Bot size={30}/> : <Mic size={30}/>)
                      : phase === 'ended' ? <PhoneOff size={28}/> : <PhoneCall size={28}/>}
                  </span>
                  <div className="dial-who">{to}</div>
                  {phase === 'connected' && <div className="dial-timer">{clock(seconds)}</div>}
                  <Badge tone={phase === 'connected' ? 'live'
                    : phase === 'ended' ? '' : 'warning'}>
                    {phase === 'connected' ? (mode === 'agent' ? 'agent talking' : 'connected')
                      : phase === 'ended' ? 'ended' : phase}
                  </Badge>
                  <p className="small muted" style={{margin: 0}}>{status}</p>
                </div>

                {mode === 'agent' && transcript && <>
                  <div className="small muted"><strong>What is being said</strong></div>
                  <div className="dial-transcript">
                    {transcript}
                    <div ref={transcriptEnd}/>
                  </div>
                </>}

                {mode === 'agent' && inCall && !transcript && <div className="dial-note">
                  <Info size={14}/>
                  <span>
                    The transcript appears once they answer and someone speaks. You can
                    close this tab — the call keeps running.
                  </span>
                </div>}

                {phase === 'ended' && <div className="row wrap" style={{justifyContent: 'center'}}>
                  <Button small variant="outline" onClick={reset}>New call</Button>
                  {callId && <Link className="button small outline"
                    to={'/app/recordings/' + callId}>See this call</Link>}
                </div>}
              </>}
        </div>
      </div>
    </div>
  </div>;
}
