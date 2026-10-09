/**
 * The pieces every screen needs and none of them should write twice: a modal,
 * a confirmation, a copy field, a section header, an audio player.
 *
 * Deliberately importing nothing from `app.tsx` — that module imports the page
 * modules, and the page modules import this one, so a dependency the other way
 * would close the loop. The classes are the same ones `style.css` defines, so
 * these look like everything else.
 */
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {AlertTriangle, Check, Copy, Loader2, Pause, Play, X} from 'lucide-react';

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

/**
 * A dialog. Closes on Escape and on a backdrop click, returns focus when it
 * goes, and scrolls inside itself rather than growing the page — a long form
 * in a modal that pushes the page down is how a Save button ends up off
 * screen with no way to reach it.
 */
export function Modal({title, subtitle, onClose, children, footer, width = 560}: {
  title: string;
  subtitle?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {if (e.key === 'Escape') onClose()};
    document.addEventListener('keydown', onKey);
    // The panel itself takes focus, so Escape and Tab land inside the dialog
    // instead of on whatever was behind it.
    panel.current?.focus();
    const {overflow} = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [onClose]);

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="modal modal-panel" ref={panel} tabIndex={-1} role="dialog" aria-modal="true"
      aria-label={title} style={{width: `min(100%, ${width}px)`}} onClick={e => e.stopPropagation()}>
      <div className="modal-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="small muted" style={{margin: '4px 0 0'}}>{subtitle}</p>}
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18}/></button>
      </div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// Confirming something destructive
// ---------------------------------------------------------------------------

export type ConfirmRequest = {
  title: string;
  /** What will happen, in plain words. Shown above the buttons. */
  body: React.ReactNode;
  confirmLabel?: string;
  /** Require the exact text to be typed — for deleting a whole account. */
  typeToConfirm?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<unknown>;
};

/**
 * `confirm()` is a browser dialog: it cannot say what will be deleted, cannot
 * be styled, and on some setups is suppressed entirely — a delete button that
 * silently does nothing. This replaces it.
 *
 *     const confirm = useConfirm();
 *     confirm({title: 'Delete this call?', body: '…', onConfirm: () => …});
 */
export function useConfirm(): [(request: ConfirmRequest) => void, React.ReactNode] {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState('');

  const ask = useCallback((next: ConfirmRequest) => {
    setRequest(next); setTyped(''); setError(''); setBusy(false);
  }, []);

  const close = () => {if (!busy) setRequest(null)};

  const go = async () => {
    if (!request) return;
    setBusy(true); setError('');
    try {
      await request.onConfirm();
      setRequest(null);
    } catch (cause) {
      // Kept open on failure: closing it would leave the thing undeleted with
      // nothing on screen to say so.
      setError(cause instanceof Error && cause.message ? cause.message : 'That did not work.');
    } finally {setBusy(false)}
  };

  const blocked = Boolean(request?.typeToConfirm) && typed.trim() !== request?.typeToConfirm;

  const dialog = request ? <Modal title={request.title} onClose={close} width={460}
    footer={<>
      <button className="button secondary" onClick={close} disabled={busy}>Cancel</button>
      <button className={'button ' + (request.danger === false ? '' : 'danger')}
        onClick={() => void go()} disabled={busy || blocked}>
        {busy ? <><Loader2 size={15} className="spin"/> Working…</> : request.confirmLabel ?? 'Delete'}
      </button>
    </>}>
    <div className="stack">
      <div className="row" style={{alignItems: 'flex-start', gap: 12}}>
        {request.danger !== false && <AlertTriangle size={20} color="var(--destructive)" style={{flex: 'none', marginTop: 2}}/>}
        <div className="small" style={{lineHeight: 1.6}}>{request.body}</div>
      </div>
      {request.typeToConfirm && <div className="field">
        <label>Type <b>{request.typeToConfirm}</b> to confirm</label>
        <input className="input" value={typed} autoFocus onChange={e => setTyped(e.target.value)}/>
      </div>}
      {error && <div className="notice danger small">{error}</div>}
    </div>
  </Modal> : null;

  return [ask, dialog];
}

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

/** A read-only value with a copy button — webhook URLs, API keys, ids. */
export function CopyField({label, value, help}: {label?: string; value: string; help?: React.ReactNode}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {/* clipboard refused; the text is selectable either way */}
  };
  return <div className="field">
    {label && <label>{label}</label>}
    <div className="copy-row">
      <input className="input mono" readOnly value={value} onFocus={e => e.currentTarget.select()}/>
      <button className="button outline" onClick={() => void copy()} type="button"
        aria-label={copied ? 'Copied' : 'Copy'}>
        {copied ? <Check size={15}/> : <Copy size={15}/>}
      </button>
    </div>
    {help && <div className="help">{help}</div>}
  </div>;
}

/** A labelled group inside a card, so long forms read as sections. */
export function Section({title, help, children, action}: {
  title: string; help?: React.ReactNode; children: React.ReactNode; action?: React.ReactNode;
}) {
  return <section className="form-section">
    <div className="row between" style={{gap: 12}}>
      <div>
        <h3>{title}</h3>
        {help && <p className="help" style={{margin: '2px 0 0'}}>{help}</p>}
      </div>
      {action}
    </div>
    <div className="stack">{children}</div>
  </section>;
}

/** An uncontrolled-feeling text input that does not fight the keyboard. */
export function TextInput({label, value, onChange, help, placeholder, type = 'text', rows, required, disabled, autoFocus, suffix}: {
  label?: string;
  value: string;
  onChange: (v: string) => void;
  help?: React.ReactNode;
  placeholder?: string;
  type?: string;
  rows?: number;
  required?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  suffix?: React.ReactNode;
}) {
  const shared = {
    value, placeholder, disabled, required, autoFocus,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(e.target.value),
  };
  return <div className="field">
    {label && <label>{label}{required ? ' *' : ''}</label>}
    {rows
      ? <textarea className="textarea" rows={rows} {...shared}/>
      : suffix
        ? <div className="copy-row"><input className="input" type={type} {...shared}/>{suffix}</div>
        : <input className="input" type={type} {...shared}/>}
    {help && <div className="help">{help}</div>}
  </div>;
}

export function Select({label, value, onChange, children, help, disabled}: {
  label?: string; value: string; onChange: (v: string) => void;
  children: React.ReactNode; help?: React.ReactNode; disabled?: boolean;
}) {
  return <div className="field">
    {label && <label>{label}</label>}
    <select className="select" value={value} disabled={disabled}
      onChange={e => onChange(e.target.value)}>{children}</select>
    {help && <div className="help">{help}</div>}
  </div>;
}

// ---------------------------------------------------------------------------
// Playing a recording
// ---------------------------------------------------------------------------

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.floor(seconds - m * 60)).padStart(2, '0')}`;
}

/**
 * A recording player that says what is happening.
 *
 * The native `<audio controls>` is not enough here: the URL is short-lived and
 * fetched on demand, and when it fails the native element shows an empty
 * control with no reason. This asks for the URL when Play is first pressed,
 * reports the failure in words, and keeps the scrub bar usable because the
 * server honours range requests.
 */
export function RecordingPlayer({callId, getUrl, seconds}: {
  callId: string;
  getUrl: (callId: string) => Promise<string>;
  seconds?: number;
}) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [url, setUrl] = useState('');
  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState(0);
  const [total, setTotal] = useState(seconds ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {setUrl(''); setPlaying(false); setAt(0); setError('')}, [callId]);

  const toggle = async () => {
    setError('');
    if (playing) {audio.current?.pause(); return}
    if (!url) {
      setBusy(true);
      try {
        setUrl(await getUrl(callId));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'The recording could not be loaded.');
        return;
      } finally {setBusy(false)}
      return; // autoPlay on the fresh element starts it
    }
    try {await audio.current?.play()} catch {setError('Your browser would not play this file.')}
  };

  return <div className="player">
    <button className="player-button" onClick={() => void toggle()} disabled={busy}
      aria-label={playing ? 'Pause' : 'Play'}>
      {busy ? <Loader2 size={17} className="spin"/> : playing ? <Pause size={17}/> : <Play size={17}/>}
    </button>
    <div className="player-track">
      <input className="player-range" type="range" min={0} max={Math.max(total, 1)} step={0.1} value={at}
        aria-label="Position in the recording"
        onChange={e => {
          const next = Number(e.target.value);
          setAt(next);
          if (audio.current) audio.current.currentTime = next;
        }}/>
      <div className="row between small muted"><span>{clock(at)}</span><span>{clock(total)}</span></div>
      {error && <div className="small" style={{color: 'var(--destructive)'}}>{error}</div>}
    </div>
    {url && <audio ref={audio} src={url} autoPlay preload="metadata"
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={() => {setPlaying(false); setAt(0)}}
      onTimeUpdate={e => setAt(e.currentTarget.currentTime)}
      onLoadedMetadata={e => {
        const d = e.currentTarget.duration;
        if (Number.isFinite(d) && d > 0) setTotal(d);
      }}
      onError={() => {
        setPlaying(false);
        setError('The recording could not be played. It may have been moved or deleted.');
      }}/>}
  </div>;
}

/** The three-value state every async screen is in, said the same way each time. */
export function Loading({label = 'Loading…'}: {label?: string}) {
  return <div className="row small muted" style={{gap: 8, padding: '18px 0'}}>
    <Loader2 size={15} className="spin"/> {label}
  </div>;
}

export function ErrorNote({error, onRetry}: {error: string; onRetry?: () => void}) {
  return <div className="notice danger">
    <div className="row between" style={{gap: 12}}>
      <span>{error}</span>
      {onRetry && <button className="button outline small" onClick={onRetry}>Try again</button>}
    </div>
  </div>;
}
