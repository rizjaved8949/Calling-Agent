/// <reference types="vite/client" />

/**
 * The environment this build was compiled with.
 *
 * `VITE_API_URL` is the switch between the fixture prototype and the real
 * backend: unset means the app runs entirely on its in-memory store, set means
 * every screen the backend covers talks to it.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
