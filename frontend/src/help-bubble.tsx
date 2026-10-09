/**
 * The help assistant, as a bubble in the corner of every screen.
 *
 * It used to be a card at the top of the Guides page, which is the wrong place
 * for it twice over: you had to already know the page existed, and getting to
 * it meant leaving the screen you were confused about. The question is nearly
 * always about what is in front of you — "why does this say Not verified" —
 * so the answer should arrive without going anywhere.
 *
 * It answers from the product documentation and can be shown a screenshot. It
 * is deliberately not given the company's own knowledge base: this answers
 * "where is that setting", not "what are our fees", and mixing the two would
 * have it quoting a price list at somebody trying to connect a number.
 *
 * For anything about the account itself there is a person, not a model — the
 * panel says so and links to Contact support.
 */
import {useEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {
  Image as ImageIcon, LifeBuoy, Loader2, Paperclip, Send, Sparkles, X,
} from 'lucide-react';
import {API_BASE, getAuthToken} from './lib/api/http';

type Answer = {answered: boolean; text: string};
type Turn = {you: string; attachment?: string; answer?: Answer};

const EXAMPLES = [
  'Why does my number say Not verified?',
  'How do I let the agent send a WhatsApp message?',
  'What does the 24-hour window mean?',
];

const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,application/pdf,.txt,.md,.csv';
/** The server's own ceiling, said before an upload is spent finding out. */
const MAX_BYTES = 8 * 1024 * 1024;

export function HelpBubble() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const picker = useRef<HTMLInputElement>(null);
  const foot = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) foot.current?.scrollIntoView({block: 'end', behavior: 'smooth'});
  }, [turns.length, busy, open]);

  useEffect(() => {
    if (!open) return;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {if (e.key === 'Escape') setOpen(false)};
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    const node = box.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = Math.min(node.scrollHeight, 110) + 'px';
  }, [question, open]);

  const choose = (chosen: File | null) => {
    if (!chosen) return;
    if (chosen.size > MAX_BYTES) {
      setError(`That file is ${Math.round(chosen.size / 1_048_576)} MB. Keep it under 8 MB.`);
      return;
    }
    setError('');
    setFile(chosen);
  };

  const ask = async () => {
    const text = question.trim();
    if ((!text && !file) || busy) return;
    setBusy(true);
    setError('');
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
    } finally {
      setBusy(false);
    }
  };

  if (!open) return <button className="hb-launch" onClick={() => setOpen(true)}
    title="Ask about this screen" aria-label="Ask about this screen">
    <Sparkles size={23}/>
  </button>;

  return <div className="hb-panel" role="dialog" aria-modal="false" aria-label="Help">
    <div className="hb-head">
      <Sparkles size={19}/>
      <div>
        <strong>Ask about this screen</strong>
        <div className="hb-sub">Answers how the platform works. It cannot see your account.</div>
      </div>
      <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Close">
        <X size={18}/>
      </button>
    </div>

    <div className="hb-body">
      {turns.length === 0 && <>
        <div className="hb-reply">
          Ask anything about using the platform — a setting you cannot find, a
          message in red, why a number will not verify. Attach a screenshot and
          it will read it.
        </div>
        {EXAMPLES.map(example => <button key={example} className="hb-chip"
          onClick={() => {setQuestion(example); box.current?.focus()}}>
          {example}
        </button>)}
        <Link className="hb-chip" to="/app/support" onClick={() => setOpen(false)}>
          <LifeBuoy size={13}/> Something about my own account — message a person
        </Link>
      </>}

      {turns.map((turn, index) => <div key={index} className="stack" style={{gap: 8}}>
        <div className="hb-ask">
          {turn.attachment && <div className="row small" style={{gap: 6, opacity: .85}}>
            <ImageIcon size={12}/> {turn.attachment}
          </div>}
          {turn.you}
        </div>
        {turn.answer && <div className={'hb-reply' + (turn.answer.answered ? '' : ' unsure')}>
          {turn.answer.text}
        </div>}
      </div>)}

      {busy && <div className="hb-reply">
        <span className="row" style={{gap: 8}}>
          <Loader2 size={13} className="spin"/> Reading…
        </span>
      </div>}
      {error && <div className="hb-reply unsure">{error}</div>}
      <div ref={foot}/>
    </div>

    {file && <div className="hb-attached">
      <Paperclip size={13}/>
      <span style={{flex: 1, minWidth: 0, overflowWrap: 'anywhere'}}>{file.name}</span>
      <button className="icon-btn" aria-label="Remove the attachment"
        onClick={() => {setFile(null); if (picker.current) picker.current.value = ''}}>
        <X size={14}/>
      </button>
    </div>}

    <div className="hb-foot">
      <input ref={picker} type="file" hidden accept={ACCEPT}
        onChange={e => choose(e.target.files?.[0] ?? null)}/>
      <button className="sc-icon" title="Attach a screenshot"
        onClick={() => picker.current?.click()} disabled={busy}>
        <Paperclip size={17}/>
      </button>
      <textarea ref={box} className="textarea" rows={1} value={question}
        placeholder="Describe what you are seeing"
        onChange={e => setQuestion(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey) {e.preventDefault(); void ask()}
        }}/>
      <button className="sc-send" onClick={() => void ask()} aria-label="Ask"
        disabled={busy || (!question.trim() && !file)}>
        {busy ? <Loader2 size={16} className="spin"/> : <Send size={16}/>}
      </button>
    </div>
  </div>;
}
