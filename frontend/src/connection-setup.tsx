import {useState} from 'react';
import {Check, ChevronDown, Copy, Eye, EyeOff, Loader2, PhoneCall, ServerCog, Trash2} from 'lucide-react';
import {Badge,Button,Empty,Field} from './app';
import {useApp} from './app-context';
import {formatDate} from './lib/format';
import {api, LIVE} from './lib/api';
import {API_BASE} from './lib/api/http';
import {auth as firebaseAuth} from './lib/firebase';
import type {ChannelType} from './lib/types';

/** Finishes what signup could not: the real WhatsApp phone_number_id, which
 * `PATCH /companies/me` refuses on purpose (see credentials.py and
 * auth.py's connect_number — changing a tenant's primary key there would
 * orphan everything already filed under the old one). A self-signed-up
 * company is left on a `pending-…` id until this runs once. */
function ConnectWhatsAppNumber({onConnected}: {onConnected: () => void}) {
  const {t, toast} = useApp();
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [displayNumber, setDisplayNumber] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const connect = async () => {
    const user = firebaseAuth?.currentUser;
    if (!user) { setError('Sign in again to do this.'); return }
    if (!phoneNumberId.trim()) { setError('Enter the phone number ID from Meta.'); return }
    setBusy(true); setError('');
    try {
      const idToken = await user.getIdToken();
      const res = await fetch(`${API_BASE}/api/auth/connect-number`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', Authorization: `Bearer ${idToken}`},
        body: JSON.stringify({phoneNumberId: phoneNumberId.trim(), displayPhoneNumber: displayNumber.trim()}),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error?.message || 'Could not connect that number.');
      toast('WhatsApp number connected.');
      onConnected();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not connect that number.');
    } finally { setBusy(false) }
  };

  return <div className="card stack" style={{borderColor: 'var(--warning)'}}>
    <div className="row"><PhoneCall size={18}/><strong>{t('Connect your WhatsApp number')}</strong></div>
    <p className="small muted">
      {t('Your account was created without a number attached yet. Find the "Phone number ID" in Meta for Developers — WhatsApp → API Setup — and enter it here, once. This cannot be changed again afterward without contacting support, since every call and credential gets filed under it.')}
    </p>
    <Field label="Phone number ID" value={phoneNumberId} onChange={setPhoneNumberId} placeholder="674871172379324"/>
    <Field label="Display number (optional)" value={displayNumber} onChange={setDisplayNumber} placeholder="+1 555 0100"/>
    {error && <div className="notice danger">{error}</div>}
    <div className="row"><Button disabled={busy || !phoneNumberId.trim()} onClick={connect}>
      {busy ? <Loader2 size={15} className="spin"/> : t('Connect this number')}
    </Button></div>
  </div>;
}

/**
 * Connection setup, shared by the company workspace and the platform portal.
 *
 * Companies enter their own provider credentials here. This is the one screen
 * where provider names are unavoidable — you cannot ask someone for a Meta app
 * secret without saying so — so the plain-language rule that governs the rest of
 * the company UI is relaxed here, and only here.
 *
 * Values are write-only: on save the input is cleared and only the last four
 * characters are kept, so nothing can read a secret back out of the app.
 */

const CHANNEL_LABEL: Record<ChannelType, string> = {
  sim: 'Phone line',
  whatsapp_call: 'WhatsApp calling',
  whatsapp_message: 'WhatsApp messaging',
};

type CredSpec = { key: string; label: string; hint: string; secret: boolean };

/**
 * What each channel needs — field names matching exactly what the backend
 * accepts (`FIELD_MAP` in models/credentials.py). These used to be invented
 * names (apiKey, baseUrl, metaAppId, applicationId, sipTrunkId) that the
 * backend has never recognised, so saving any of them either did nothing or
 * was rejected outright; fixed here rather than papered over, since the
 * wrong names were the actual reason this screen never worked.
 *
 * `phoneNumberId` is deliberately not listed — see ConnectWhatsAppNumber
 * above for why it is a one-time, separate step rather than a field here.
 */
const CREDENTIALS_FOR: Record<ChannelType, CredSpec[]> = {
  sim: [
    { key: 'infobipApiKey', label: 'API key', hint: 'Infobip portal, Developer Tools → API Keys.', secret: true },
    { key: 'infobipBaseUrl', label: 'Base URL', hint: 'The host for your account, e.g. https://xyz123.api.infobip.com.', secret: false },
    { key: 'infobipPhoneNumber', label: 'Phone number', hint: 'The number itself, as it appears in your Infobip account.', secret: false },
    { key: 'infobipCallsConfigurationId', label: 'Calls configuration ID', hint: 'Infobip portal, Developer Tools → Applications and Entities → Calls.', secret: false },
  ],
  whatsapp_call: [
    { key: 'accessToken', label: 'Access token', hint: 'A permanent system-user token with WhatsApp messaging permission.', secret: true },
    { key: 'metaAppSecret', label: 'Meta app secret', hint: 'Meta for Developers, your app, Settings → Basic.', secret: true },
    { key: 'businessAccountId', label: 'WhatsApp business account ID', hint: 'The account that owns this number.', secret: false },
    { key: 'webhookVerifyToken', label: 'Webhook verify token', hint: 'You choose this. Any phrase. Paste the same one into Meta.', secret: true },
  ],
  whatsapp_message: [
    { key: 'accessToken', label: 'Access token', hint: 'The same token as calling, if this is the same number.', secret: true },
    { key: 'businessAccountId', label: 'WhatsApp business account ID', hint: 'Needed so we can list your message templates.', secret: false },
  ],
};

export function ConnectionSetup({orgId, channelId, intro = true}: {orgId: string; channelId?: string; intro?: boolean}) {
  const {store, run, t, toast, canManage} = useApp();
  const all = store.channels.filter(x => x.orgId === orgId);
  const channels = channelId ? all.filter(x => x.id === channelId) : all;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string>(channels[0]?.id || '');
  const [confirm, setConfirm] = useState<string>('');

  const copy = (text: string, what: string) => { navigator.clipboard?.writeText(text); toast(what + ' copied.'); };
  const field = (chId: string, key: string) => chId + '::' + key;

  const save = async (chId: string, spec: CredSpec) => {
    const key = field(chId, spec.key);
    const value = draft[key] || '';
    if (value.trim().length < 4) { toast('That value looks too short.'); return; }
    // The whole value goes to the server, which seals it and stores it; what
    // comes back is only whether it is set and its last four characters. The
    // draft is cleared either way, so the secret does not linger in the form.
    await run(() => api.setCredential(orgId, chId, spec.key, value.trim()),
      spec.label + ' saved. Only its last four characters are ever shown again.');
    setDraft(d => ({...d, [key]: ''}));
    setShown(s => ({...s, [key]: false}));
  };

  const remove = async (credId: string, label: string) => {
    await run(() => api.deleteCredential(credId), label + ' removed.');
    setConfirm('');
  };

  if (channels.length === 0) {
    return <Empty icon={ServerCog} title="No numbers yet" body="Once you have a number it will appear here for setup."/>;
  }

  // A self-signed-up company starts on a placeholder id until this runs
  // once — see ConnectWhatsAppNumber above. Reload after, rather than a
  // local state patch: orgId itself changes, and everything keyed off it
  // (routing, history, the URL this very screen is shown at) needs the
  // fresh company the same way a fresh sign-in already provides it.
  const needsRealNumber = LIVE && orgId.startsWith('pending-');

  return <div className="card stack">
    {intro && <div>
      <h2>{t('Connection setup')}</h2>
      <p className="small muted">{t('Enter the details from your provider account. We save only the last four characters of anything secret, so nothing can be read back out later — not by you, and not by us.')}</p>
    </div>}
    {needsRealNumber && <ConnectWhatsAppNumber onConnected={() => window.location.reload()}/>}

    {channels.map(ch => {
      const specs = CREDENTIALS_FOR[ch.type];
      const stored = store.credentials.filter(c => c.channelId === ch.id);
      const have = specs.filter(sp => stored.some(c => c.keyName === sp.key)).length;
      const ready = have === specs.length;
      const task = store.provisioning.find(p => p.channelId === ch.id);
      const isOpen = channels.length === 1 || open === ch.id;
      return <div className={'conn-block ' + (isOpen ? 'open' : '')} key={ch.id}>
        {channels.length > 1 && <button type="button" className="conn-head" onClick={() => setOpen(isOpen ? '' : ch.id)} aria-expanded={isOpen}>
          <span className="conn-head-main">
            <strong>{ch.label}</strong>
            <span className="small muted">{t(CHANNEL_LABEL[ch.type])} · {ch.displayNumber || t('no number yet')}</span>
          </span>
          <span className="row">
            <Badge tone={ready ? 'success' : have ? 'warning' : 'danger'}>{have} / {specs.length} {t('done')}</Badge>
            <ChevronDown size={17} style={{transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform .16s ease'}}/>
          </span>
        </button>}

        {isOpen && <div className="conn-body">
          {channels.length === 1 && <div className="row between">
            <span className="small muted">{t('Details for')} <strong>{ch.label}</strong></span>
            <Badge tone={ready ? 'success' : have ? 'warning' : 'danger'}>{have} / {specs.length} {t('done')}</Badge>
          </div>}

          {specs.map(spec => {
            const key = field(ch.id, spec.key);
            const row = stored.find(c => c.keyName === spec.key);
            const typed = draft[key] || '';
            return <div className="conn-field" key={spec.key}>
              <div className="conn-field-label">
                <strong>{spec.label}</strong>
                {row
                  ? <span className="small success"><Check size={13}/> {t('Saved')} · <span className="mono">••••{row.lastFour}</span></span>
                  : <span className="small danger">{t('Not entered yet')}</span>}
                <span className="small muted">{spec.hint}</span>
              </div>
              <div className="conn-field-input">
                <span className="conn-input-wrap">
                  <input
                    className="input"
                    type={spec.secret && !shown[key] ? 'password' : 'text'}
                    autoComplete="off"
                    disabled={!canManage}
                    placeholder={row ? t('Enter a new value to replace it') : t('Paste the value and press Enter')}
                    value={typed}
                    onChange={e => setDraft(d => ({...d, [key]: e.target.value}))}
                    onKeyDown={e => { if (e.key === 'Enter') save(ch.id, spec); }}/>
                  {spec.secret && <button type="button" className="conn-eye" aria-label={t(shown[key] ? 'Hide' : 'Show')}
                    onClick={() => setShown(s => ({...s, [key]: !s[key]}))}>
                    {shown[key] ? <EyeOff size={15}/> : <Eye size={15}/>}
                  </button>}
                </span>
                <Button small variant="outline" disabled={!canManage || typed.trim().length < 4} onClick={() => save(ch.id, spec)}>
                  {row ? t('Replace') : t('Save')}
                </Button>
                {row && canManage && <button className="icon-btn" title={t('Remove')} onClick={() => setConfirm(row.id)}><Trash2 size={15}/></button>}
              </div>
              {row && <div className="small muted">{t('Entered by')} {row.setBy} · {formatDate(row.setAt)}</div>}
              {confirm === row?.id && <div className="conn-confirm">
                <span className="small">{t('Remove')} <strong>{spec.label}</strong>? {t('This number will stop working until you enter it again.')}</span>
                <span className="row">
                  <Button small variant="outline" onClick={() => setConfirm('')}>Cancel</Button>
                  <Button small variant="danger" onClick={() => remove(row!.id, spec.label)}>Remove</Button>
                </span>
              </div>}
            </div>;
          })}

          {ch.type !== 'sim' && <div className="conn-webhook">
            <strong className="small">{t('Paste this into Meta')}</strong>
            <div className="conn-copy">
              <span className="small muted">{t('Callback URL')}</span>
              <code>{LIVE ? `${API_BASE}/api/webhooks/whatsapp` : `https://api.callingagent.app/webhooks/whatsapp/${ch.id}`}</code>
              <Button small variant="outline" onClick={() => copy(LIVE ? `${API_BASE}/api/webhooks/whatsapp` : 'https://api.callingagent.app/webhooks/whatsapp/' + ch.id, 'Callback URL')}><Copy size={13}/> Copy</Button>
            </div>
            <div className="small muted">{t('One URL for every number on this account — Meta tells us which one a message or call is for. Use the verify token you entered above, then subscribe your app to the messages and calls fields.')}</div>
          </div>}

          {!LIVE && <div className="conn-actions">
            <Button small variant="outline" disabled={!canManage} onClick={() => run(() => api.testChannel(ch.id), 'Testing the connection…')}>Test connection</Button>
            {task && task.state !== 'active' && <Button small disabled={!canManage || !ready} title={ready ? undefined : t('Enter every detail first')}
              onClick={() => run(() => api.updateProvisioning(task.id, {state: 'active'}), ch.label + ' is live.')}>Turn it on</Button>}
            {!ready && <span className="small danger">{specs.length - have} {t('still to enter, so this number cannot take calls yet.')}</span>}
            {ready && task?.state !== 'active' && <span className="small muted">{t('All entered. Test it, then turn it on.')}</span>}
          </div>}
          {LIVE && <div className="conn-actions">
            {ready
              ? <span className="small success"><Check size={13}/> {t('All set. This channel is already live — nothing further to turn on.')}</span>
              : <span className="small danger">{specs.length - have} {t('still to enter, so this number cannot take calls yet.')}</span>}
          </div>}
        </div>}
      </div>;
    })}
  </div>;
}
