/**
 * Two small pieces that only exist while this build talks to a real backend.
 *
 * `ApiKeyGate` asks for the company API key, because the backend identifies a
 * company by that key and there is no sign-in yet. **It is a stand-in.** A
 * company API key can send WhatsApp messages and place calls, and keeping one
 * in a browser is not how this should end up — `setAuthToken` in `lib/api/http`
 * is the seam where real sign-in replaces it.
 *
 * `ComingSoon` is the other half of being honest about this build: the screens
 * the backend does not serve yet show this instead of fixture data, so nothing
 * on screen is invented.
 */
import {useState} from 'react';
import {KeyRound, Loader2, Wrench} from 'lucide-react';
import {hasAuthToken, setAuthToken} from './lib/api';
import {live} from './lib/api/live';

export function ApiKeyGate({onReady}: {onReady: () => void}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const candidate = key.trim();
    if (!candidate) return;
    setBusy(true);
    setError('');
    // Stored before the check, because the request is authenticated with it.
    setAuthToken(candidate);
    try {
      await live.company();
      onReady();
    } catch (cause) {
      setAuthToken(null);
      setError(
        cause instanceof Error && cause.message
          ? cause.message
          : 'That key was not accepted.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="auth-card stack" onSubmit={submit}>
        <h1><KeyRound size={20}/> Connect this workspace</h1>
        <p className="muted small">
          Paste the API key issued when your company was registered. It is shown
          once, at registration.
        </p>
        <input
          className="input"
          type="password"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          placeholder="ca_…"
          value={key}
          onChange={event => setKey(event.target.value)}
        />
        {error && <p className="small danger">{error}</p>}
        <button className="button" type="submit" disabled={busy || !key.trim()}>
          {busy ? <><Loader2 size={15} className="spin"/> Checking…</> : 'Continue'}
        </button>
        <p className="small muted">
          Temporary: sign-in replaces this, and the key stops living in the browser.
        </p>
      </form>
    </div>
  );
}

export function hasBackendKey(): boolean {
  return hasAuthToken();
}

/**
 * A screen whose data this build does not have.
 *
 * Deliberately says what is missing rather than "coming soon" alone: someone
 * evaluating the product should be able to tell the difference between a
 * feature that is planned and one that is broken.
 */
export function ComingSoon({title, detail}: {title: string; detail?: string}) {
  return (
    <div className="card stack" style={{alignItems: 'flex-start', gap: 12}}>
      <span className="badge"><Wrench size={13}/> Coming soon</span>
      <h2 style={{margin: 0}}>{title}</h2>
      <p className="muted" style={{maxWidth: '54ch'}}>
        {detail ?? 'This part of the workspace is still being built. Everything else here is live.'}
      </p>
    </div>
  );
}
