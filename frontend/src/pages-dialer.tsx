/**
 * The dialer: an employee calls a customer from one of the company's numbers
 * and talks through their own browser.
 *
 * The customer sees the company's number, not the employee's phone. No agent
 * is involved — the server starts the call in "human" mode, and once the
 * customer answers, the browser's microphone and speaker are connected to the
 * call. Hanging up here hangs up the call.
 */
import {useCallback, useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {Delete, Loader2, MessageCircle, Phone, PhoneCall, PhoneOff} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {canCallOut, numberName, numbers as api, type PhoneNumber} from './lib/api/numbers';
import {joinCallAsOperator, type OperatorCall} from './lib/operator';

type Phase = 'idle' | 'dialing' | 'ringing' | 'connected' | 'ended';

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, '0')}`;
}

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '+', '0', '#'];

export function DialerScreen() {
  const {session, toast} = useApp();
  const [lines, setLines] = useState<PhoneNumber[] | null>(null);
  const [lineId, setLineId] = useState('');
  const [to, setTo] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [status, setStatus] = useState('');
  const [seconds, setSeconds] = useState(0);
  const [callId, setCallId] = useState('');
  const [busy, setBusy] = useState(false);
  const audio = useRef<OperatorCall | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const joining = useRef(false);

  useEffect(() => {
    api.list().then(b => {
      const usable = b.numbers.filter(canCallOut);
      setLines(usable);
      if (usable[0]) setLineId(id => id || usable[0].id);
    }).catch(() => setLines([]));
  }, []);

  const stopPolling = () => {if (poll.current) {clearInterval(poll.current); poll.current = null}};
  // Leaving the page hangs up: an open microphone nobody is looking at is
  // the one outcome worse than a dropped call.
  useEffect(() => () => {stopPolling(); audio.current?.leave()}, []);

  useEffect(() => {
    if (phase !== 'connected') return;
    const timer = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(timer);
  }, [phase]);

  const line = lines?.find(l => l.id === lineId);

  const finish = useCallback((text: string) => {
    stopPolling();
    audio.current?.leave();
    audio.current = null;
    setPhase('ended');
    setStatus(text);
  }, []);

  const watch = (id: string) => {
    stopPolling();
    poll.current = setInterval(async () => {
      try {
        const s = await api.callSession(id);
        if (audio.current) {
          // Still polled while talking: the customer hanging up ends the
          // session on the server, and this is how the browser hears of it.
          if (!s.live) finish('The customer hung up.');
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
            setStatus('Connected — you are talking to the customer.');
          } catch (cause) {
            toast(cause instanceof Error ? cause.message : 'Could not open the microphone.');
            void api.endCall(id).catch(() => {});
            finish('Call ended: the microphone could not be opened.');
          }
          return;
        }
        if (joining.current) return;
        if (['FAILED', 'NO_ANSWER', 'COMPLETED'].includes(s.status)) {
          finish(s.status === 'NO_ANSWER' ? 'No answer.' : s.error ? `Call failed: ${s.error}` : 'Call ended.');
        } else {
          setPhase('ringing');
          setStatus('Ringing…');
        }
      } catch {/* a missed poll is retried on the next tick */}
    }, 1500);
  };

  const call = async () => {
    if (!line || !to.trim()) return;
    setBusy(true);
    joining.current = false;
    setPhase('dialing');
    setStatus('Placing the call…');
    try {
      const placed = await api.placeCall({
        to: to.trim(),
        channel: line.kind === 'whatsapp' ? 'WHATSAPP_CALL' : 'PHONE',
        lineId: line.id,
        human: true,
        placedBy: session?.email,
      });
      setCallId(placed.id);
      setPhase('ringing');
      setStatus('Ringing…');
      watch(placed.id);
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

  const inCall = phase === 'dialing' || phase === 'ringing' || phase === 'connected';

  if (lines !== null && lines.length === 0) return <div className="stack">
    <PageHead eyebrow="Calls" title="Dialer"/>
    <Empty icon={Phone} title="No number to call from"
      body="The dialer uses one of your company’s verified numbers set to outbound or both. Ask an owner to connect one."
      action={<Button variant="outline" to="/app/numbers">Go to Numbers</Button>}/>
  </div>;

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Dialer"
      description="Call a customer from your company’s number and talk through this browser. They see the company number, not yours."/>
    <div className="split">
      <div className="card stack" style={{maxWidth: 440}}>
        <div className="field">
          <label>Call from</label>
          <select className="select" value={lineId} disabled={inCall} onChange={e => setLineId(e.target.value)}>
            {(lines ?? []).map(l => <option key={l.id} value={l.id}>{l.kind === 'whatsapp' ? 'WhatsApp · ' : 'Phone · '}{numberName(l)}</option>)}
          </select>
        </div>
        <input className="input" style={{fontSize: '1.6rem', textAlign: 'center', letterSpacing: '.04em', minHeight: 60}}
          value={to} disabled={inCall} placeholder="+92 300 1234567" inputMode="tel"
          onChange={e => setTo(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') void call()}}/>
        <div className="grid" style={{gridTemplateColumns: 'repeat(3, 1fr)', gap: 10}}>
          {KEYS.map(k => <button key={k} type="button" className="button secondary" disabled={inCall}
            style={{minHeight: 52, fontSize: '1.2rem'}} onClick={() => setTo(v => v + k)}>{k}</button>)}
        </div>
        <div className="row" style={{gap: 10}}>
          {inCall
            ? <Button variant="danger" onClick={() => void hangUp()}><PhoneOff size={17}/> Hang up</Button>
            : <Button onClick={() => void call()} disabled={!to.trim() || !line || busy}>
                {busy ? <Loader2 size={17} className="spin"/> : <PhoneCall size={17}/>} Call
              </Button>}
          <Button variant="outline" disabled={inCall || !to} onClick={() => setTo(v => v.slice(0, -1))}><Delete size={17}/></Button>
        </div>
        {line?.kind === 'whatsapp' && !inCall && <div className="small muted">
          WhatsApp only lets a business call someone who has allowed it. <button className="link-btn" onClick={() => void askPermission()}>
            <MessageCircle size={13}/> Ask this person for permission</button>
        </div>}
      </div>

      <div className="card stack" style={{alignItems: 'center', justifyContent: 'center', textAlign: 'center', minHeight: 260}}>
        {phase === 'idle' && !status && <p className="muted">Enter a number and press Call. Your browser will ask to use the microphone.</p>}
        {phase !== 'idle' && <>
          {phase === 'connected' ? <div className="live-orb"/> : inCall ? <Loader2 size={40} className="spin"/> : <Phone size={40}/>}
          <strong style={{fontSize: '1.3rem'}}>{to}</strong>
          {phase === 'connected' && <div className="stat-number">{clock(seconds)}</div>}
          <Badge tone={phase === 'connected' ? 'live' : phase === 'ended' ? '' : 'warning'}>{phase}</Badge>
          <p className="small muted">{status}</p>
          {phase === 'ended' && <div className="row">
            <Button small variant="outline" onClick={() => {setPhase('idle'); setStatus(''); setCallId('')}}>New call</Button>
            {callId && <Link className="button small outline" to={'/app/history/' + callId}>View call</Link>}
          </div>}
        </>}
      </div>
    </div>
  </div>;
}
