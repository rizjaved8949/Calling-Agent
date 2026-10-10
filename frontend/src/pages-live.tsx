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

    {/* Placing a call lives on the Dialer now, where both ways of making
        one sit side by side. Two screens offering the same thing is how one
        of them goes stale. */}
    <div className="notice">
      <div className="row between wrap" style={{gap: 12}}>
        <span>
          <strong>Want to start a call?</strong> The Dialer does both — you
          talk to them, or the agent does.
        </span>
        <Button variant="outline" to="/app/dialer"><PhoneCall size={15}/> Open the Dialer</Button>
      </div>
    </div>

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
