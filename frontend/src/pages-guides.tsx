/**
 * Guides, and somebody to ask.
 *
 * The old page was a search box, a row of category chips and a grid of cards
 * whose bodies were cut mid-word at 115 characters — so every card ended in
 * "..." and told you almost nothing, and finding the right one meant opening
 * three. Worse, a guide rendered as one unbroken paragraph of prose, which is
 * unreadable at 4 minutes' length.
 *
 * The assistant that used to sit at the top of this page is now a bubble in
 * the corner of every screen, because the real question is usually "why is
 * this screen telling me this" — and you should not have to find this page,
 * and leave that screen, in order to ask it.
 */
import {useCallback, useMemo, useState} from 'react';
import {Link, useParams} from 'react-router-dom';
import {ArrowLeft, ArrowRight, BookOpen, LifeBuoy} from 'lucide-react';
import {Badge, Button, Empty, PageHead} from './app';
import {useApp} from './app-context';
import {REAL_GUIDES} from './lib/guides-content';

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

    {/* The assistant is the bubble in the corner of every screen now. It
        used to be a card here, which meant finding this page before you could
        ask — and leaving the screen you were confused about to do it. */}
    <div className="notice">
      <div className="row between wrap" style={{gap: 12}}>
        <span>
          <strong>Stuck on a particular screen?</strong> Ask the assistant in the
          bottom-right corner of any page — it reads screenshots. For anything
          about your own account, message a person.
        </span>
        <Button variant="outline" to="/app/support"><LifeBuoy size={15}/> Contact support</Button>
      </div>
    </div>

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
          body="Try a different word, or ask the assistant in the corner — it answers in your own words rather than matching them."/>
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
            Ask the assistant in the bottom-right corner — it can look at a
            screenshot of what you are seeing.
          </p>}
        </div>
      </article>

      <div className="stack">
        <div className="card stack">
          <strong>Still stuck?</strong>
          <p className="small muted" style={{margin: 0}}>
            The assistant in the bottom-right corner answers in your own words and
            can read a screenshot. If it is about your own account, message a
            person instead.
          </p>
          <Button variant="outline" to="/app/support"><LifeBuoy size={15}/> Contact support</Button>
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
