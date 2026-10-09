/**
 * Who is signed in, as distinct from which company's API key the app ends up
 * holding.
 *
 * Firebase Authentication is the only thing in this app that talks to a
 * third party directly from the browser rather than through the Python
 * backend — by design: `sendPasswordResetEmail` and Google sign-in are
 * Firebase's own flows, and routing them through a server this app also owns
 * would just be relaying something Firebase already does end to end.
 *
 * Once someone is signed in, `lib/api/auth.ts` exchanges their Firebase ID
 * token for a company API key from the backend, and `setAuthToken` (the seam
 * `lib/api/http.ts` already names for this) takes over from there — nothing
 * downstream of that point knows Firebase exists.
 */
import { initializeApp, getApps, type FirebaseApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider, type Auth } from 'firebase/auth';

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY ?? '',
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN ?? '',
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID ?? '',
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET ?? '',
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ?? '',
  appId: import.meta.env.VITE_FIREBASE_APP_ID ?? '',
};

/** False for the fixture build, and for a live build that has not been given
 * a Firebase project yet — both run without ever importing `firebase/auth`'s
 * network code. */
export const firebaseConfigured = Boolean(config.apiKey && config.projectId);

let app: FirebaseApp | undefined;
let authInstance: Auth | undefined;

if (firebaseConfigured) {
  app = getApps()[0] ?? initializeApp(config);
  authInstance = getAuth(app);
}

/** Null when `firebaseConfigured` is false — every caller checks that first. */
export const auth: Auth | null = authInstance ?? null;
export const googleProvider = new GoogleAuthProvider();
