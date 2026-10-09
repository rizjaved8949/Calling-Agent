/**
 * Guides, and somebody to ask.
 *
 * The old page was a search box, a row of category chips and a grid of cards
 * whose bodies were cut mid-word at 115 characters — so every card ended in
 * "..." and told you almost nothing, and finding the right one meant opening
 * three. Worse, a guide rendered as one unbroken paragraph of prose, which is
 * unreadable at 4 minutes' length.
 *
 * This keeps the library but puts the assistant first, because the real
 * question is usually "why is this screen telling me this", not "which of
 * eleven guides covers it". It can be shown a screenshot, which is what people
 * have in front of them when they are stuck.
 */
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Link, useParams} from 'react-router-dom';
import {
  ArrowLeft, ArrowRight, BookOpen, Image as ImageIcon, Loader2, Paperclip,
  Sparkles, X,
} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {ErrorNote} from './ui';
import {REAL_GUIDES} from './lib/guides-content';
import {API_BASE, getAuthToken} from './lib/api/http';

type Answer = {answered: boolean; text: string};

// ---------------------------------------------------------------------------
// The assistant
// ---------------------------------------------------------------------------

function HelpAssistant() {
  const [question, setQuestion] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [turns, setTurns] = useState<{you: string; attachment?: string; answer?: Answer}[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const picker = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {bottom.current?.scrollIntoView({block: 'end', behavior: 'smooth'})},
    [turns.length, busy]);

  const ask = async () => {
    const text = question.trim();
    if ((!text && !file) || busy) return;
    setBusy(true); setError('');
    setTurns(old => [...old, {you: text || `About ${file?.name}`, attachment: file?.name}]);
    setQuestion('');

    try {
      // Multipart rather than JSON: the attachment is a file, and base64 in a
      // body would inflate a screenshot by a third for no benefit.
      const form = new FormData();
      form.append('question', text);
      if (file) form.append('file', file);
      const token = getAuthToken();
      const response = await fetch(`${API_BASE}/api/help`, {
        method: 'POST',
        headers: token ? {Authorization: `Bearer ${token}`} : undefined,
        body: form,
      });
      if (!response.ok) throw new Error('The assistant could not be reached.');
      const answer = await response.json() as Answer;
      setTurns(old => old.map((turn, i) => i === old.length - 1 ? {...turn, answer} : turn));
      setFile(null);
      if (picker.current) picker.current.value = '';
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'That did not work.');
      setTurns(old => old.slice(0, -1));
    } finally {setBusy(false)}
  };

  const examples = [
    'Why does my number say Not verified?',
    'How do I let the agent send a WhatsApp message?',
    'What does the 24-hour window mean?',
  ];

  return <div className="card stack">
    <div className="row between">
      <div className="row" style={{gap: 10}}>
        <Sparkles size={18} color="var(--accent)"/>
        <div>
          <strong>Ask about anything here</strong>
          <div className="help">
            Attach a screenshot of an error and it will read it. It answers about
            this platform, not about your own customers.
          </div>
        </div>
      </div>
    </div>

    {turns.length > 0 && <div className="help-thread">
      {turns.map((turn, index) => <div key={index} className="stack" style={{gap: 8}}>
        <div className="help-you">
          {turn.attachment && <span className="badge raw"><ImageIcon size={11}/> {turn.attachment}</span>}
          <div>{turn.you}</div>
        </div>
        {turn.answer && <div className={'help-answer' + (turn.answer.answered ? '' : ' unsure')}>
          {turn.answer.text}
        </div>}
      </div>)}
      {busy && <div className="row small muted" style={{gap: 8}}>
        <Loader2 size={14} className="spin"/> Reading…
      </div>}
      <div ref={bottom}/>
    </div>}

    {turns.length === 0 && <div className="row wrap" style={{gap: 8}}>
      {examples.map(example => <button key={example} className="button outline small"
        onClick={() => setQuestion(example)}>{example}</button>)}
    </div>}

    {error && <ErrorNote error={error}/>}

    {file && <div className="row between small" style={{gap: 10}}>
      <span className="row" style={{gap: 6}}><Paperclip size={13}/> {file.name}</span>
      <button className="icon-btn" aria-label="Remove the attachment"
        onClick={() => {setFile(null); if (picker.current) picker.current.value = ''}}>
        <X size={14}/>
      </button>
    </div>}

    <div className="form-row">
      <div className="field" style={{flex: 1}}>
        <textarea className="textarea" rows={2} value={question} style={{minHeight: 48}}
          placeholder="Describe what you are seeing, or ask how something works"
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault(); void ask()}
          }}/>
      </div>
      <input ref={picker} type="file" hidden
        accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,.txt,.md,.csv"
        onChange={e => setFile(e.target.files?.[0] ?? null)}/>
      <Button variant="outline" onClick={() => picker.current?.click()} disabled={busy}
        title="Attach a screenshot or a document">
        <Paperclip size={15}/>
      </Button>
      <Button onClick={() => void ask()} disabled={busy || (!question.trim() && !file)}>
        {busy ? <Loader2 size={15} className="spin"/> : 'Ask'}
      </Button>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// The library
// ---------------------------------------------------------------------------

/** The first full sentence, so a card says something rather than trailing off. */
function opener(body: string): string {
  const stop = body.indexOf('. ');
  const first = stop > 0 ? body.slice(0, stop + 1) : body;
  return first.length > 190 ? first.slice(0, 187).trimEnd() + '…' : first;
}

export function GuidesScreen() {
  const {t} = useApp();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');

  const categories = useMemo(
    () => ['All', ...Array.from(new Set(REAL_GUIDES.map(g => g.category)))], []);

  const found = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return REAL_GUIDES.filter(guide =>
      (category === 'All' || guide.category === category)
      && (!needle
        || guide.title.toLowerCase().includes(needle)
        || guide.bodyMd.toLowerCase().includes(needle)));
  }, [search, category]);

  return <div className="stack">
    <PageHead eyebrow="Help" title="Guides"
      description="How each part of this platform works, and someone to ask when a screen is not doing what you expect."/>

    <HelpAssistant/>

    <div className="card stack">
      <div className="form-row">
        <div className="field" style={{flex: 1}}>
          <label>{t('Search the guides')}</label>
          <input className="input" value={search} placeholder="Infobip, template, recording…"
            onChange={e => setSearch(e.target.value)}/>
        </div>
      </div>
      <div className="row wrap" style={{gap: 8}}>
        {categories.map(name => <button key={name} type="button"
          className={'button small ' + (category === name ? '' : 'outline')}
          onClick={() => setCategory(name)}>{name}</button>)}
      </div>
    </div>

    {found.length === 0
      ? <Empty icon={BookOpen} title="Nothing matches"
          body="Try a different word, or ask the assistant above — it answers in your own words rather than matching them."/>
      : <div className="grid cols-2">
          {found.map(guide => <Link className="card lift stack" to={'/app/guides/' + guide.slug}
            key={guide.slug} style={{gap: 10}}>
            <div className="row between">
              <Badge>{guide.category}</Badge>
              <span className="small muted">{guide.readingMinutes} min</span>
            </div>
            <h2 style={{margin: 0}}>{guide.title}</h2>
            <p className="small muted" style={{margin: 0, lineHeight: 1.6}}>{opener(guide.bodyMd)}</p>
            <span className="link-btn small">Read <ArrowRight size={13}/></span>
          </Link>)}
        </div>}
  </div>;
}

export function GuideScreen() {
  const {slug} = useParams();
  const guide = REAL_GUIDES.find(g => g.slug === slug);
  const [helpful, setHelpful] = useState<string>('');
  const {toast} = useApp();

  const related = useMemo(
    () => guide ? REAL_GUIDES.filter(g => g.slug !== guide.slug && g.category === guide.category) : [],
    [guide]);

  const load = useCallback(() => undefined, []);
  if (!guide) return <div className="stack">
    <PageHead eyebrow="Help" title="Guide"/>
    <Empty icon={BookOpen} title="No such guide"
      body="It may have been renamed. Browse the library or ask the assistant."
      action={<Button to="/app/guides">All guides</Button>}/>
  </div>;

  // Written as prose, one idea per sentence. Split into paragraphs so a four
  // minute read is not a single wall of text.
  const paragraphs = guide.bodyMd.split(/\n+/).flatMap(block => {
    const sentences = block.split(/(?<=\.)\s+/);
    const out: string[] = [];
    for (let i = 0; i < sentences.length; i += 3) {
      out.push(sentences.slice(i, i + 3).join(' '));
    }
    return out;
  }).filter(Boolean);

  return <div className="stack">
    <PageHead eyebrow={guide.category} title={guide.title}
      description={`${guide.readingMinutes} min read`}
      action={<Button variant="outline" to="/app/guides"><ArrowLeft size={15}/> All guides</Button>}/>

    <div className="split">
      <article className="card stack guide-body">
        {paragraphs.map((text, index) => <p key={index}>{text}</p>)}

        <div className="notice">
          <div className="row between wrap" style={{gap: 10}}>
            <strong>Was this useful?</strong>
            <div className="row">
              {['Yes', 'No'].map(answer => <Button key={answer} small
                variant={helpful === answer ? '' : 'outline'}
                onClick={() => {setHelpful(answer); toast('Thanks — noted.'); load()}}>
                {answer}
              </Button>)}
            </div>
          </div>
          {helpful === 'No' && <p className="small muted" style={{margin: '10px 0 0'}}>
            Ask the assistant on the Guides page — it can look at a screenshot of
            what you are seeing.
          </p>}
        </div>
      </article>

      <div className="stack">
        <div className="card stack">
          <strong>Still stuck?</strong>
          <p className="small muted" style={{margin: 0}}>
            The assistant on the Guides page answers in your own words and can read
            a screenshot of an error.
          </p>
          <Button variant="outline" to="/app/guides"><Sparkles size={15}/> Ask it</Button>
        </div>
        {related.length > 0 && <div className="card stack">
          <strong>More on {guide.category.toLowerCase()}</strong>
          {related.map(other => <Link className="nav-link" to={'/app/guides/' + other.slug}
            key={other.slug}><BookOpen size={16}/>{other.title}</Link>)}
        </div>}
      </div>
    </div>
  </div>;
}
