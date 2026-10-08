/**
 * The two screens a company actually sets its agent up on.
 *
 * Both talk to the real API rather than the fixture store, so they only appear
 * in a build that has a backend — see the routing in `app.tsx`. They replace
 * the fixture versions, which modelled knowledge as several bases with chunks
 * and similarity scores; the API has one list of documents, and a screen that
 * pretends otherwise teaches the wrong thing about the product.
 */
import {useCallback, useEffect, useRef, useState} from 'react';
import {FileText, Loader2, Trash2, Upload} from 'lucide-react';
import {Badge, Button, Empty, Field, PageHead} from './app';
import {useApp} from './app-context';
import {api} from './lib/api';

type Document = {id: string; name: string; chars: number; updatedAt?: number};

/** Characters, as a size somebody can judge. */
function size(chars: number): string {
  if (chars >= 1000) return `${Math.round(chars / 1000).toLocaleString()}k characters`;
  return `${chars} characters`;
}

export function KnowledgeScreen() {
  const {t, toast, canManage} = useApp();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [used, setUsed] = useState(0);
  const [limit, setLimit] = useState(120000);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState('');
  const picker = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const body = await api.getKnowledgeDocuments();
      setDocuments(body?.documents ?? []);
      setUsed(body?.totalChars ?? 0);
      setLimit(body?.limitChars ?? 120000);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load your documents.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {void load()}, [load]);

  const upload = async (file: File) => {
    setBusy(file.name);
    setError('');
    try {
      const stored = await api.uploadKnowledgeDocument(file);
      toast(`${file.name} added — ${size(stored?.chars ?? 0)} read.`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That file could not be read.');
    } finally {
      setBusy('');
      // Cleared so the same file can be chosen again after a correction.
      if (picker.current) picker.current.value = '';
    }
  };

  const remove = async (document: Document) => {
    setBusy(document.id);
    try {
      await api.deleteKnowledgeDocument(document.id);
      toast(`${document.name} removed.`);
      await load();
    } finally {
      setBusy('');
    }
  };

  const showPreview = async () => {
    const body = await api.previewKnowledge();
    setPreview(body?.preview || t('Nothing is stored yet.'));
  };

  const full = used >= limit;

  return <div className="stack">
    <PageHead
      eyebrow="Agent"
      title="What your agent knows"
      description="Upload your price list, prospectus or FAQ. Your agent answers from these and says it will check rather than guessing when something is not here."
      action={canManage ? <>
        <input ref={picker} type="file" hidden accept=".pdf,.txt,.md,.csv,text/*,application/pdf"
          onChange={event => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}/>
        <Button onClick={() => picker.current?.click()} disabled={Boolean(busy)}>
          {busy ? <><Loader2 size={15} className="spin"/> Reading…</> : <><Upload size={15}/> Add a document</>}
        </Button>
      </> : undefined}/>

    {error && <div className="notice danger">{error}</div>}

    <div className="card stack">
      <div className="row" style={{justifyContent: 'space-between'}}>
        <div>
          <strong>{size(used)}</strong> <span className="muted small">of {size(limit)} used</span>
        </div>
        {used > 0 && <Button small variant="outline" onClick={showPreview}>
          {t('See what the agent reads')}
        </Button>}
      </div>
      {full && <div className="notice danger">
        {t('Your material no longer fits in one answer. Remove something, or split the largest document.')}
      </div>}

      {loading
        ? <div className="small muted">{t('Loading…')}</div>
        : documents.length === 0
          ? <Empty icon={FileText} title="No documents yet"
              body="Until you add something, your agent will not state any facts about your business — it will offer to have someone follow up instead."/>
          : <div className="stack">
              {documents.map(document => <div className="setup-row" key={document.id}>
                <div className="setup-row-main">
                  <strong>{document.name}</strong>
                  <span className="small muted">{size(document.chars)}</span>
                </div>
                {canManage && <Button small variant="outline" disabled={busy === document.id}
                  onClick={() => void remove(document)}>
                  <Trash2 size={14}/> {t('Remove')}
                </Button>}
              </div>)}
            </div>}
    </div>

    {preview && <div className="card stack">
      <div className="row" style={{justifyContent: 'space-between'}}>
        <strong>{t('What the agent reads')}</strong>
        <Button small variant="outline" onClick={() => setPreview('')}>{t('Close')}</Button>
      </div>
      <pre className="small muted" style={{whiteSpace: 'pre-wrap', maxHeight: 360, overflow: 'auto'}}>
        {preview}
      </pre>
    </div>}
  </div>;
}

export function AgentScreen() {
  const {t, toast, canManage} = useApp();
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setSettings((await api.getAgentSettings()) as Record<string, unknown>);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load your settings.');
    }
  }, []);
  useEffect(() => {void load()}, [load]);

  const save = async (patch: Record<string, unknown>) => {
    setSaving(true);
    setError('');
    try {
      await api.updateAgentSettings(patch);
      setSettings(current => ({...(current ?? {}), ...patch}));
      toast(t('Saved.'));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  if (!settings) {
    return <div className="stack">
      <PageHead eyebrow="Agent" title="Your agent"/>
      {error ? <div className="notice danger">{error}</div> : <div className="small muted">{t('Loading…')}</div>}
    </div>;
  }

  const value = (key: string) => String(settings[key] ?? '');
  const flag = (key: string) => Boolean(settings[key]);

  return <div className="stack">
    <PageHead eyebrow="Agent" title="Your agent"
      description="How your agent introduces itself and answers. These apply to calls and to WhatsApp messages alike."
      action={saving ? <Badge>{t('Saving…')}</Badge> : undefined}/>

    {error && <div className="notice danger">{error}</div>}

    <div className="card stack">
      <Field label="Who your agent is" rows={5} value={value('persona')}
        disabled={!canManage}
        onChange={v => void save({persona: v})}
        placeholder="You are Ayesha, the admissions assistant for…"
        help="Written in plain language. This is the single description used for both calls and messages."/>

      <Field label="Opening line" value={value('greeting')} disabled={!canManage}
        onChange={v => void save({agentGreeting: v})}
        placeholder="Thank you for calling. How can I help?"
        help="Spoken first on a call. Leave it empty and your agent waits for the caller to speak, which on an answered call is two people listening to each other."/>

      <Field label="Language" value={value('language')} disabled={!canManage}
        onChange={v => void save({language: v})} placeholder="ur-PK"
        help="The language your agent starts in. It follows whoever it is speaking to if they use another."/>

      <Field label="Voice" value={value('voice')} disabled={!canManage}
        onChange={v => void save({ttsVoice: v})} placeholder="leave empty for the default"
        help="A named voice from your speech provider."/>
    </div>

    <div className="card stack">
      <div className="use-row row" style={{justifyContent: 'space-between'}}>
        <div className="setup-row-main">
          <strong>{t('Answer WhatsApp messages automatically')}</strong>
          <span className="small muted">
            {t('Replies to incoming messages using the documents above. Without any documents, your agent will not state facts about your business.')}
          </span>
        </div>
        <input className="switch" type="checkbox" checked={flag('autoReply')} disabled={!canManage}
          onChange={event => void save({autoReply: event.target.checked})}/>
      </div>

      <div className="use-row row" style={{justifyContent: 'space-between'}}>
        <div className="setup-row-main">
          <strong>{t('Record calls')}</strong>
          <span className="small muted">
            {t('Both sides, stored in your own Google Drive when you have connected one.')}
          </span>
        </div>
        <input className="switch" type="checkbox" checked={flag('recordCalls')} disabled={!canManage}
          onChange={event => void save({recordCalls: event.target.checked})}/>
      </div>
    </div>

    {value('webhookUrl') && <div className="card stack">
      <strong>{t('Webhook address')}</strong>
      <p className="small muted">
        {t('Paste this into your WhatsApp app settings so your messages and calls reach us.')}
      </p>
      <code className="small" style={{overflowWrap: 'anywhere'}}>{value('webhookUrl')}</code>
    </div>}
  </div>;
}
