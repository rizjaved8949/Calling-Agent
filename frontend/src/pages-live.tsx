/**
 * Calls happening right now, and taking one over.
 *
 * Polled rather than streamed: a call list changes a few times a minute, and a
 * second websocket purely to watch a list would be more moving parts than the
 * thing it replaces. The *audio* is a socket, which is where it matters.
 */
import {useCallback, useEffect, useRef, useState} from 'react';
import {Headphones, Loader2, PhoneCall, PhoneOff} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {Link} from 'react-router-dom';
import {useApp} from './app-context';
import {api} from './lib/api';
import {joinCallAsOperator, type OperatorCall} from './lib/operator';
import {canCallOut, numberName, numbers as numberApi, type PhoneNumber} from './lib/api/numbers';

type LiveCall = {
  callId: string;
  counterparty: string;
  channel: string;
  direction: string;
  seconds: number;
  handledBy: string;
  speaking: boolean;
  transcript: string;
  operatorToken: string;
};

const CHANNEL: Record<string, string> = {
  PHONE: 'Phone line',
  WHATSAPP_CALL: 'WhatsApp call',
  BROWSER: 'Browser',
};

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.max(0, seconds - m * 60)).padStart(2, '0')}`;
}

/**
 * Placing a call.
 *
 * WhatsApp and a phone line differ in one way that matters to whoever is
 * looking at this: WhatsApp needs the person's permission first, and Meta
 * refuses without it. So the permission request is a button of its own rather
 * than something hidden inside "call", which would fail for a reason nobody
 * could see.
 */
function PlaceCall({onPlaced}: {onPlaced: () => void}) {
  const {t, toast} = useApp();
  const [number, setNumber] = useState('');
  const [lines, setLines] = useState<PhoneNumber[]>([]);
  const [lineId, setLineId] = useState('');
  const [busy, setBusy] = useState('');
  useEffect(() => {
    numberApi.list().then(b => {
      // Only numbers with an outbound agent: this form puts the agent on the
      // call. Calling yourself is the Dialer's job.
      const usable = b.numbers.filter(n => canCallOut(n) && n.outboundAgentId);
      setLines(usable);
      if (usable[0]) setLineId(id => id || usable[0].id);
    }).catch(() => {});
  }, []);
  const line = lines.find(l => l.id === lineId);
  const channel: 'PHONE' | 'WHATSAPP_CALL' = line?.kind === 'whatsapp' ? 'WHATSAPP_CALL' : 'PHONE';

  const call = async () => {
    if (!number.trim()) {toast(t('Enter a number first.')); return}
    setBusy('call');
    try {
      if (!line) {toast(t('Give a verified number an outbound agent on the Numbers page first.')); return}
      await numberApi.placeCall({to: number.trim(), channel, lineId: line.id});
      toast(t('Calling…'));
      setNumber('');
      onPlaced();
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : t('That call could not be placed.'));
    } finally {
      setBusy('');
    }
  };

  const askPermission = async () => {
    if (!number.trim()) {toast(t('Enter a number first.')); return}
    setBusy('permission');
    try {
      await numberApi.askPermission(number.trim(), lineId);
      toast(t('Asked. They will see a message with an accept button.'));
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : t('That request could not be sent.'));
    } finally {
      setBusy('');
    }
  };

  return <div className="card stack">
    <div>
      <strong>{t('Have the agent call someone')}</strong>
      <div className="help">{t('Your agent does the talking. To speak yourself, use the Dialer.')}</div>
    </div>
    {/* Two fields of comparable weight, then the actions. The number field used
        to take the whole width and push the picker and the button into a
        corner, which made the choice of number look like an afterthought. */}
    <div className="field-grid">
      <div className="field">
        <label>{t('Number to call')}</label>
        <input className="input" value={number} inputMode="tel" disabled={Boolean(busy)}
          placeholder="03001112222" onChange={e => setNumber(e.target.value)}
          onKeyDown={e => {if (e.key === 'Enter') void call()}}/>
        <div className="help">{t('A local number is expanded using your country code.')}</div>
      </div>
      <div className="field">
        <label>{t('Call from')}</label>
        <select className="select" value={lineId} disabled={Boolean(busy) || !lines.length}
          onChange={e => setLineId(e.target.value)}>
          {lines.length === 0 && <option value="">{t('No number with an outbound agent')}</option>}
          {lines.map(l => <option key={l.id} value={l.id}>{l.kind === 'whatsapp' ? 'WhatsApp · ' : 'Phone · '}{numberName(l)}</option>)}
        </select>
        <div className="help">
          {lines.length === 0
            ? <Link to="/app/numbers">{t('Give a verified number an outbound agent →')}</Link>
            : t('The agent assigned to this number answers for it.')}
        </div>
      </div>
    </div>
    <div className="row wrap">
      <Button onClick={() => void call()} disabled={Boolean(busy) || !number.trim() || !line}>
        {busy === 'call'
          ? <><Loader2 size={14} className="spin"/> {t('Calling…')}</>
          : <><PhoneCall size={14}/> {t('Call')}</>}
      </Button>
      {channel === 'WHATSAPP_CALL' && <Button variant="outline" disabled={Boolean(busy)}
        onClick={() => void askPermission()}>
        {busy === 'permission' ? t('Asking…') : t('Ask permission first')}
      </Button>}
    </div>
    {channel === 'WHATSAPP_CALL' && <p className="small muted">
      {t('WhatsApp requires the person to grant permission before a business may call them. Ask first; they only need to accept once.')}
    </p>}
  </div>;
}

export function LiveCalls() {
  const {t, toast, canManage} = useApp();
  const [calls, setCalls] = useState<LiveCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [joining, setJoining] = useState('');
  const [onCall, setOnCall] = useState<string>('');
  const session = useRef<OperatorCall | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await api.getLiveCalls();
      setCalls((body?.calls ?? []) as LiveCall[]);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not read live calls.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => {void load()}, 4000);
    return () => clearInterval(timer);
  }, [load]);

  // Leaving the page must not leave the microphone open.
  useEffect(() => () => session.current?.leave(), []);

  const take = async (call: LiveCall) => {
    setJoining(call.callId);
    try {
      session.current = await joinCallAsOperator(call.callId, call.operatorToken, event => {
        if (event.type === 'error') toast(event.detail ?? t('The call connection failed.'));
        if (event.type === 'left') setOnCall('');
      });
      setOnCall(call.callId);
      toast(t('You are on the call. The agent has stopped.'));
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : t('Could not join the call.'));
    } finally {
      setJoining('');
    }
  };

  const leave = async (callId: string) => {
    session.current?.leave();
    session.current = null;
    setOnCall('');
    // Leaving does not end the call: the caller is still there, so the agent
    // picks it back up rather than the line going dead.
    try {
      await api.handBackCall(callId);
      toast(t('The agent has the call again.'));
    } catch {
      /* the socket closing already hands it back server-side */
    }
    void load();
  };

  return <div className="stack">
    <PageHead eyebrow="Calls" title="Happening now"
      description="Calls with audio actually moving through them. A call that shows here is one you can join."
      action={loading ? <Badge>{t('Checking…')}</Badge> : <Badge tone={calls.length ? 'success' : ''}>
        {calls.length} {t(calls.length === 1 ? 'live call' : 'live calls')}
      </Badge>}/>

    {error && <div className="notice danger">{error}</div>}

    {canManage && <PlaceCall onPlaced={load}/>}

    {calls.length === 0 && !loading
      ? <Empty icon={PhoneCall} title="No calls in progress"
          body="When someone calls, they appear here while they are still on the line — with what has been said so far, and a button to take over."/>
      : <div className="stack">
          {calls.map(call => {
            const mine = onCall === call.callId;
            return <div className="card stack" key={call.callId}>
              <div className="row" style={{justifyContent: 'space-between', alignItems: 'flex-start'}}>
                <div className="setup-row-main">
                  <strong>{call.counterparty || t('Unknown number')}</strong>
                  <span className="small muted">
                    {t(CHANNEL[call.channel] ?? call.channel)} · {call.direction === 'INBOUND' ? t('incoming') : t('outgoing')} · {clock(call.seconds)}
                  </span>
                </div>
                <div className="row">
                  {call.handledBy === 'operator'
                    ? <Badge tone="warning">{t('a person is on this call')}</Badge>
                    : call.speaking
                      ? <Badge tone="success">{t('agent speaking')}</Badge>
                      : <Badge>{t('agent listening')}</Badge>}
                  {canManage && (mine
                    ? <Button small variant="outline" onClick={() => void leave(call.callId)}>
                        <PhoneOff size={14}/> {t('Give back to the agent')}
                      </Button>
                    : <Button small disabled={Boolean(joining)}
                        onClick={() => void take(call)}>
                        {joining === call.callId
                          ? <><Loader2 size={14} className="spin"/> {t('Joining…')}</>
                          : <><Headphones size={14}/> {t('Take over')}</>}
                      </Button>)}
                </div>
              </div>

              {mine && <div className="notice">
                {t('Your microphone is live on this call. The caller hears you, not the agent.')}
              </div>}

              {call.transcript && <pre className="small muted"
                style={{whiteSpace: 'pre-wrap', margin: 0, maxHeight: 220, overflow: 'auto'}}>
                {call.transcript}
              </pre>}
            </div>;
          })}
        </div>}
  </div>;
}
