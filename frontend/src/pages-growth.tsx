/**
 * Three screens that read from the live API: what has been used, what the
 * agent could not answer, and outbound campaigns.
 *
 * The gaps screen is the one worth explaining. It is not an error log — every
 * entry is a question a real person asked that the business has no published
 * answer to, which makes it the most direct list of what to upload next.
 */
import {useCallback, useEffect, useState} from 'react';
import {HelpCircle, Loader2, Pause, Play, Plus, Trash2, TrendingUp} from 'lucide-react';
import {Badge, Button, Empty, Field, PageHead, Tabs} from './app';
import {useApp} from './app-context';
import {api} from './lib/api';

const when = (epoch?: number) =>
  epoch ? new Date(epoch * 1000).toLocaleDateString(undefined,
    {day: 'numeric', month: 'short'}) : '';

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

export function UsageScreen() {
  const {t} = useApp();
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    void (async () => {
      try {
        setData((await api.getUsage('')) as Record<string, any>);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Could not read usage.');
      }
    })();
  }, []);

  if (error) return <div className="stack">
    <PageHead eyebrow="Account" title="Usage"/>
    <div className="notice danger">{error}</div>
  </div>;
  if (!data) return <div className="stack">
    <PageHead eyebrow="Account" title="Usage"/>
    <div className="small muted">{t('Loading…')}</div>
  </div>;

  const month = data.thisMonth ?? {};
  const all = data.allTime ?? {};
  const storage = data.storage ?? {};
  const mb = (storage.recordingBytes ?? 0) / 1048576;

  const tile = (label: string, value: string | number, note?: string) =>
    <div className="card stack" style={{gap: 4, minWidth: 150, flex: '1 1 150px'}}>
      <span className="small muted">{t(label)}</span>
      <strong style={{fontSize: '1.5rem'}}>{value}</strong>
      {note && <span className="small muted">{t(note)}</span>}
    </div>;

  return <div className="stack">
    <PageHead eyebrow="Account" title="Usage"
      description="Counted from your calls and messages, not from a separate meter — these are the same rows the history screen shows, added up."/>

    <h3 style={{margin: 0}}>{t('This month')}</h3>
    <div className="row" style={{flexWrap: 'wrap', gap: 12}}>
      {tile('Calls', month.calls ?? 0)}
      {tile('Minutes', month.minutes ?? 0, 'rounded up per call')}
      {tile('Recorded', month.recorded ?? 0)}
      {tile('Messages in', month.messagesIn ?? 0)}
      {tile('Messages out', month.messagesOut ?? 0)}
    </div>

    <h3 style={{margin: '8px 0 0'}}>{t('All time')}</h3>
    <div className="row" style={{flexWrap: 'wrap', gap: 12}}>
      {tile('Calls', all.calls ?? 0, `${all.answered ?? 0} answered`)}
      {tile('Minutes', all.minutes ?? 0)}
      {tile('Recordings', all.recordings ?? 0)}
      {tile('Messages', all.messages ?? 0)}
      {tile('Failed calls', all.failed ?? 0)}
    </div>

    <div className="card stack">
      <strong>{t('Storage')}</strong>
      <span className="small muted">
        {mb < 1 ? `${Math.round(mb * 1024)} KB` : `${mb.toFixed(1)} MB`} {t('of recordings')}
        {storage.inGoogleDrive
          ? t(' — in your own Google Drive, under your quota')
          : t(' — connect Google Drive to keep these in your own account')}
      </span>
      <span className="small muted">
        {storage.knowledgeDocuments ?? 0} {t('documents')},{' '}
        {(storage.knowledgeChars ?? 0).toLocaleString()} {t('characters of knowledge')}
      </span>
    </div>

    {(data.byMonth ?? []).length > 1 && <div className="card stack">
      <strong>{t('By month')}</strong>
      <table className="table">
        <thead><tr>
          <th>{t('Month')}</th><th>{t('Calls')}</th><th>{t('Minutes')}</th>
          <th>{t('In')}</th><th>{t('Out')}</th>
        </tr></thead>
        <tbody>
          {(data.byMonth as Record<string, number>[]).map(row => <tr key={row.month as unknown as string}>
            <td>{row.month}</td><td>{row.calls}</td><td>{row.minutes}</td>
            <td>{row.messagesIn}</td><td>{row.messagesOut}</td>
          </tr>)}
        </tbody>
      </table>
    </div>}
  </div>;
}

// ---------------------------------------------------------------------------
// Unanswered questions
// ---------------------------------------------------------------------------

export function GapsScreen() {
  const {t} = useApp();
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    void (async () => {
      try {
        setData((await api.getGaps()) as Record<string, any>);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Could not read this.');
      }
    })();
  }, []);

  if (error) return <div className="stack">
    <PageHead eyebrow="Agent" title="Questions you could not answer"/>
    <div className="notice danger">{error}</div>
  </div>;
  if (!data) return <div className="stack">
    <PageHead eyebrow="Agent" title="Questions you could not answer"/>
    <div className="small muted">{t('Loading…')}</div>
  </div>;

  const gaps = (data.gaps ?? []) as Record<string, any>[];
  const mostAsked = (data.mostAsked ?? []) as {question: string; times: number}[];

  return <div className="stack">
    <PageHead eyebrow="Agent" title="Questions you could not answer"
      description="Every time your agent said it would check and follow up. Each one is a question someone actually asked that your documents do not cover."
      action={<Badge tone={gaps.length ? 'warning' : 'ok'}>
        {data.total ?? 0} {t('found')}
      </Badge>}/>

    {mostAsked.length > 0 && <div className="card stack">
      <strong><TrendingUp size={15}/> {t('Asked more than once')}</strong>
      <p className="small muted">{t('Answering these in your documents has the most effect.')}</p>
      {mostAsked.map(row => <div className="setup-row" key={row.question}>
        <div className="setup-row-main"><strong>{row.question}</strong></div>
        <Badge tone="warning">{row.times}×</Badge>
      </div>)}
    </div>}

    {gaps.length === 0
      ? <Empty icon={HelpCircle} title="Nothing unanswered"
          body="Your agent has answered everything it was asked from the documents you uploaded."/>
      : <div className="stack">
          {gaps.map((gap, i) => <div className="card stack" key={`${gap.sourceId}-${i}`} style={{gap: 6}}>
            <div className="row" style={{justifyContent: 'space-between'}}>
              <strong>{gap.question}</strong>
              {(gap.askedTimes ?? 1) > 1 && <Badge tone="warning">{gap.askedTimes}×</Badge>}
            </div>
            <span className="small muted">{gap.reply}</span>
            <span className="small muted">
              {gap.source === 'call' ? t('on a call') : t('by message')} · {gap.counterparty} · {when(gap.at)}
            </span>
          </div>)}
        </div>}
  </div>;
}

// ---------------------------------------------------------------------------
// Campaigns
// ---------------------------------------------------------------------------

export function CampaignsScreen() {
  const {t, toast, canManage} = useApp();
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState('');
  const [draft, setDraft] = useState({name: '', channel: 'PHONE', numbers: '', gapSeconds: '20'});

  const load = useCallback(async () => {
    try {
      setRows(((await api.getCampaigns('')) ?? []) as Record<string, any>[]);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load campaigns.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    // Polled while something is running, so progress moves without a refresh.
    const timer = setInterval(() => {void load()}, 8000);
    return () => clearInterval(timer);
  }, [load]);

  const create = async () => {
    if (!draft.name.trim() || !draft.numbers.trim()) {
      toast(t('A name and at least one number are both needed.'));
      return;
    }
    setBusy('new');
    try {
      await api.createCampaignLive({
        name: draft.name.trim(),
        channel: draft.channel,
        numbers: draft.numbers,
        gapSeconds: Number(draft.gapSeconds) || 20,
      });
      setDraft({name: '', channel: 'PHONE', numbers: '', gapSeconds: '20'});
      setAdding(false);
      await load();
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : t('That could not be created.'));
    } finally {
      setBusy('');
    }
  };

  const act = async (id: string, what: 'start' | 'pause' | 'delete') => {
    setBusy(id);
    try {
      if (what === 'start') {await api.startCampaign(id); toast(t('Calling has started.'))}
      if (what === 'pause') {await api.pauseCampaign(id); toast(t('Paused after the call in progress.'))}
      if (what === 'delete') await api.deleteCampaignLive(id);
      await load();
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : t('That did not work.'));
    } finally {
      setBusy('');
    }
  };

  return <div className="stack">
    <PageHead eyebrow="Outbound" title="Campaigns"
      description="A list of people your agent calls, one at a time with a gap between. Nobody is redialled automatically."
      action={canManage ? <Button onClick={() => setAdding(x => !x)}>
        <Plus size={15}/> {t('New campaign')}
      </Button> : undefined}/>

    {error && <div className="notice danger">{error}</div>}

    {adding && <div className="card stack">
      <Field label="Name" value={draft.name} onChange={v => setDraft(d => ({...d, name: v}))}
        placeholder="Spring intake follow-up"/>
      <Field label="Numbers" rows={6} value={draft.numbers}
        onChange={v => setDraft(d => ({...d, numbers: v}))}
        placeholder={'03001112222, Ayesha\n03001112223, Omar'}
        help="One per line. A name after a comma is optional. Any number that cannot be read stops the whole list, so nobody is silently skipped."/>
      <div className="row" style={{alignItems: 'flex-end', gap: 10}}>
        <select className="select" value={draft.channel}
          onChange={e => setDraft(d => ({...d, channel: e.target.value}))}>
          <option value="PHONE">{t('Phone line')}</option>
          <option value="WHATSAPP_CALL">{t('WhatsApp')}</option>
        </select>
        <div style={{width: 160}}>
          <Field label="Seconds between calls" type="number" value={draft.gapSeconds}
            onChange={v => setDraft(d => ({...d, gapSeconds: v}))}/>
        </div>
        <Button onClick={() => void create()} disabled={busy === 'new'}>
          {busy === 'new' ? <><Loader2 size={14} className="spin"/> {t('Creating…')}</> : t('Create')}
        </Button>
        <Button variant="outline" onClick={() => setAdding(false)}>{t('Cancel')}</Button>
      </div>
    </div>}

    {loading
      ? <div className="small muted">{t('Loading…')}</div>
      : rows.length === 0
        ? <Empty icon={Play} title="No campaigns yet"
            body="Create one with a list of numbers and your agent will work through it, one call at a time."/>
        : <div className="stack">
            {rows.map(row => {
              const counts = (row.counts ?? {}) as Record<string, number>;
              const running = row.status === 'RUNNING';
              return <div className="card stack" key={row.id as string}>
                <div className="row" style={{justifyContent: 'space-between', alignItems: 'flex-start'}}>
                  <div className="setup-row-main">
                    <strong>{row.name}</strong>
                    <span className="small muted">
                      {row.channel === 'PHONE' ? t('Phone line') : t('WhatsApp')} ·{' '}
                      {row.total} {t('contacts')} · {t('every')} {row.gapSeconds}s
                    </span>
                  </div>
                  <div className="row">
                    <Badge tone={running ? 'ok' : row.status === 'DONE' ? '' : 'warning'}>
                      {t(String(row.status).toLowerCase())}
                    </Badge>
                    {canManage && (running
                      ? <Button small variant="outline" disabled={busy === row.id}
                          onClick={() => void act(row.id as string, 'pause')}>
                          <Pause size={14}/> {t('Pause')}
                        </Button>
                      : <Button small disabled={busy === row.id}
                          onClick={() => void act(row.id as string, 'start')}>
                          <Play size={14}/> {t('Start')}
                        </Button>)}
                    {canManage && !running && <Button small variant="outline" disabled={busy === row.id}
                      onClick={() => void act(row.id as string, 'delete')}>
                      <Trash2 size={14}/>
                    </Button>}
                  </div>
                </div>

                <div className="row small muted" style={{gap: 16, flexWrap: 'wrap'}}>
                  <span>{t('waiting')} {counts.WAITING ?? 0}</span>
                  <span>{t('called')} {counts.DONE ?? 0}</span>
                  <span>{t('failed')} {counts.FAILED ?? 0}</span>
                  <span>{row.progress ?? 0}% {t('done')}</span>
                </div>
                {row.lastError && <div className="notice danger">{row.lastError}</div>}
              </div>;
            })}
          </div>}
  </div>;
}
