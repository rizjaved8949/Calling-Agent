/**
 * Turning "signed in with Firebase" into "holding this company's API key."
 *
 * Every function here does the same two things in order: ask Firebase to
 * prove who somebody is, then hand that proof to the backend and get back
 * the one thing the rest of the app actually runs on — a company API key.
 * Nothing past `finish()` needs to know Firebase was ever involved.
 */
import {
  createUserWithEmailAndPassword,
  sendPasswordResetEmail as firebaseSendPasswordReset,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
} from 'firebase/auth';
import { auth, firebaseConfigured, googleProvider } from '../firebase';
import { request } from './http';

export { firebaseConfigured };

export type AuthSession = {
  apiKey: string;
  phoneNumberId: string;
  companyName: string;
  email: string;
  role: 'owner' | 'staff';
};

/** Thrown by `login` specifically when the credential was fine but no
 * company is linked yet — the caller's cue to ask for a company name and
 * call `signup` instead. */
export class NeedsSignup extends Error {
  constructor(readonly email: string) {
    super('No company is linked to this account yet.');
    this.name = 'NeedsSignup';
  }
}

function requireAuth() {
  if (!auth) {
    throw new Error(
      'Sign-in is not configured in this build (VITE_FIREBASE_* is unset).',
    );
  }
  return auth;
}

async function login(idToken: string, email: string): Promise<AuthSession> {
  try {
    return await request<AuthSession>('/api/auth/login', { method: 'POST', bearer: idToken });
  } catch (cause) {
    const code = (cause as { code?: string }).code;
    if (code === 'needs_signup') throw new NeedsSignup(email);
    throw cause;
  }
}

/** Create the Firebase account, then (if it's new) ask for a company name
 * before calling signup — `onNeedsCompanyName` resolves with that name. */
export async function registerWithEmail(
  email: string,
  password: string,
  companyName: string,
): Promise<AuthSession> {
  const credential = await createUserWithEmailAndPassword(requireAuth(), email, password);
  const idToken = await credential.user.getIdToken();
  return request<AuthSession>('/api/auth/signup', {
    method: 'POST',
    bearer: idToken,
    body: { companyName },
  });
}

/** Signs in an existing email/password account. Throws `NeedsSignup` if this
 * Firebase account has never finished setting up a company. */
export async function loginWithEmail(email: string, password: string): Promise<AuthSession> {
  const credential = await signInWithEmailAndPassword(requireAuth(), email, password);
  const idToken = await credential.user.getIdToken();
  return login(idToken, email);
}

/** Google sign-in. Throws `NeedsSignup` for a Google account signing in for
 * the first time — the caller should ask for a company name and call
 * `completeGoogleSignup`. */
export async function loginWithGoogle(): Promise<AuthSession> {
  const result = await signInWithPopup(requireAuth(), googleProvider);
  const idToken = await result.user.getIdToken();
  return login(idToken, result.user.email ?? '');
}

/** Finishes a Google sign-in that came back as `NeedsSignup` — the Firebase
 * session from `loginWithGoogle` is still active, so this just asks the
 * backend to create the company against the same signed-in person. */
export async function completeGoogleSignup(companyName: string): Promise<AuthSession> {
  const user = requireAuth().currentUser;
  if (!user) throw new Error('Signed out before the company name was given.');
  const idToken = await user.getIdToken();
  return request<AuthSession>('/api/auth/signup', {
    method: 'POST',
    bearer: idToken,
    body: { companyName },
  });
}

/** What an invite link is for, before signing in — shown on the
 * invite-accept page so somebody knows what they're agreeing to join. */
export async function peekInvite(
  token: string,
): Promise<{ email: string; role: string; companyName: string }> {
  return request(`/api/team/invite/${encodeURIComponent(token)}`, { anonymous: true });
}

/** Joining a company somebody already runs, from an invite link. The
 * Firebase account is created or signed into first (by the caller), then
 * this links it to the company the invite names. */
export async function acceptInvite(token: string): Promise<AuthSession> {
  const user = requireAuth().currentUser;
  if (!user) throw new Error('Sign in before accepting an invitation.');
  const idToken = await user.getIdToken();
  return request<AuthSession>('/api/auth/accept-invite', {
    method: 'POST', bearer: idToken, body: { token },
  });
}

/** Firebase's own hosted flow: it emails a reset link and handles the rest.
 * Nothing in this backend is involved, and nothing here can fail quietly —
 * Firebase throws for an invalid address, which the caller surfaces. */
export async function sendPasswordReset(email: string): Promise<void> {
  await firebaseSendPasswordReset(requireAuth(), email);
}

export async function signOut(): Promise<void> {
  if (auth) await firebaseSignOut(auth);
}

const FRIENDLY: Record<string, string> = {
  'auth/email-already-in-use': 'An account already exists for that email. Try signing in instead.',
  'auth/invalid-email': 'Enter a valid email address.',
  'auth/weak-password': 'Use at least six characters.',
  'auth/wrong-password': 'That password is incorrect.',
  'auth/invalid-credential': 'That email or password is incorrect.',
  'auth/user-not-found': 'No account exists for that email.',
  'auth/too-many-requests': 'Too many attempts. Wait a moment and try again.',
  'auth/popup-closed-by-user': 'The Google sign-in window was closed before finishing.',
  'auth/network-request-failed': 'Could not reach the sign-in service. Check your connection.',
};

/** A sentence worth showing, for anything this module or Firebase itself
 * can throw — a `NeedsSignup`, a backend `ApiError`, or a raw Firebase
 * `auth/...` error code. */
export function friendlyAuthError(error: unknown): string {
  if (error instanceof NeedsSignup) return error.message;
  const code = (error as { code?: string } | undefined)?.code;
  if (code && FRIENDLY[code]) return FRIENDLY[code];
  return error instanceof Error && error.message ? error.message : 'Something did not work.';
}
