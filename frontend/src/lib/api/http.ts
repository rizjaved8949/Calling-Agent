/**
 * The HTTP client for the Python backend.
 *
 * One place that knows the base URL, the credential, and the error envelope —
 * so the rest of the app never writes `fetch` and never has to remember that
 * failures arrive as `{error:{message,code}}` rather than as a thrown network
 * error.
 *
 * ## Authentication, for now
 *
 * The backend identifies a company by its API key, presented as a bearer
 * token. Until real sign-in exists there is nowhere else for that key to come
 * from, so it is held here and read from `localStorage`. **This is a
 * development arrangement, not the finished one**: a company API key can send
 * WhatsApp messages and place calls, and a long-lived credential in a browser
 * is reachable by any script on the page.
 *
 * `setAuthToken` is the seam. When sign-in lands it supplies a short-lived
 * user token instead and nothing else in the app changes.
 */

const RAW_BASE = (import.meta.env.VITE_API_URL ?? '').trim();

/** Whether this build talks to the backend at all. */
export const LIVE = RAW_BASE.length > 0;

/** No trailing slash, so joining a path is always one concatenation. */
export const API_BASE = RAW_BASE.replace(/\/+$/, '');

const TOKEN_KEY = 'calling-agent.api-key';
const ADMIN_KEY = 'calling-agent.admin-key';
const VIEWING_KEY = 'calling-agent.viewing-company';

let token: string | null = null;

function readStoredToken(): string | null {
  // Private windows and blocked site data both throw rather than return null.
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getAuthToken(): string | null {
  if (token === null) token = readStoredToken();
  return token;
}

export function setAuthToken(value: string | null): void {
  token = value;
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Keeping it in memory still gets this session working.
  }
}

export function hasAuthToken(): boolean {
  return Boolean(getAuthToken());
}

/**
 * The operator's own key, for the platform portal.
 *
 * Separate from a company's key because it does a different job: it lists and
 * creates companies, and lets the portal look at one without holding that
 * company's credential. **Same caveat as the company key** — it should not
 * live in a browser once there is real sign-in, and it can do more damage.
 */
let adminKey: string | null = null;

export function getAdminKey(): string | null {
  if (adminKey === null) {
    try {
      adminKey = localStorage.getItem(ADMIN_KEY);
    } catch {
      adminKey = null;
    }
  }
  return adminKey;
}

export function setAdminKey(value: string | null): void {
  adminKey = value;
  try {
    if (value) localStorage.setItem(ADMIN_KEY, value);
    else localStorage.removeItem(ADMIN_KEY);
  } catch {
    /* memory is enough for this session */
  }
}

export function hasAdminKey(): boolean {
  return Boolean(getAdminKey());
}

/**
 * Which company the portal is currently looking at.
 *
 * Only meaningful with an admin key: the API accepts it as `X-Company-Id` and
 * answers as that company. A company key ignores it entirely, so a customer
 * cannot set this and read somebody else's data.
 */
let viewing: string | null = null;

export function getViewingCompany(): string | null {
  if (viewing === null) {
    try {
      viewing = localStorage.getItem(VIEWING_KEY);
    } catch {
      viewing = null;
    }
  }
  return viewing || null;
}

export function setViewingCompany(companyId: string | null): void {
  viewing = companyId;
  try {
    if (companyId) localStorage.setItem(VIEWING_KEY, companyId);
    else localStorage.removeItem(VIEWING_KEY);
  } catch {
    /* as above */
  }
}

/** A failure the backend described, with the status it came back on. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string = 'error',
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** True when signing in again is the fix, rather than retrying. */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  /** Sent as the raw request body, for audio uploads. */
  raw?: BodyInit;
  contentType?: string;
  query?: Record<string, string | number | boolean | undefined>;
  /** Some endpoints are reachable without a company key. */
  anonymous?: boolean;
  /**
   * Authenticate with this instead of the stored company key — a Firebase ID
   * token, for the one moment (signing in) before there is a company key to
   * send. Implies `anonymous`'s effect on the stored key and admin header.
   */
  bearer?: string;
};

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${API_BASE}${path.startsWith('/') ? path : `/${path}`}`, window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value));
  }
  return url.toString();
}

async function readError(response: Response): Promise<ApiError> {
  // A 502 from a proxy is HTML, not our envelope, so the parse is guarded and
  // the status line is the fallback message.
  try {
    const body = await response.json();
    const error = body?.error ?? {};
    return new ApiError(
      response.status,
      error.message || response.statusText || 'The request failed.',
      error.code || 'error',
      error.details,
    );
  } catch {
    return new ApiError(response.status, response.statusText || 'The request failed.');
  }
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = {};
  if (options.bearer) {
    headers.Authorization = `Bearer ${options.bearer}`;
  } else if (!options.anonymous) {
    const key = getAuthToken();
    if (key) headers.Authorization = `Bearer ${key}`;
    const admin = getAdminKey();
    if (admin) {
      headers['X-Admin-Key'] = admin;
      // Only read when the admin key is valid, so this cannot be used by a
      // company to reach another company's data.
      const company = getViewingCompany();
      if (company) headers['X-Company-Id'] = company;
    }
  }

  let body: BodyInit | undefined;
  if (options.raw !== undefined) {
    body = options.raw;
    if (options.contentType) headers['Content-Type'] = options.contentType;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
    });
  } catch (cause) {
    // fetch rejects only on a network-level failure, which is a different
    // problem from a 500 and deserves a different sentence.
    throw new ApiError(0, 'Could not reach the server. Check your connection.', 'offline', cause);
  }

  if (!response.ok) {
    const failure = await readError(response);
    // The portal's "View as" is held in storage, so it survives a reload — and
    // survived the company being removed, which left every screen saying
    // "Company not found" with no way to tell that the account being looked at
    // was not your own. Letting go of it is the only sensible move: the next
    // request is for the operator's own view, which exists.
    if (failure.status === 404 && getViewingCompany() && !options.anonymous) {
      setViewingCompany(null);
      throw new ApiError(
        404,
        'That company no longer exists, so the portal has stopped viewing it. ' +
        'Reload to go back to your own account.',
        'viewed_company_gone',
      );
    }
    throw failure;
  }
  return response;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('content-type') ?? '';
  if (!type.includes('application/json')) return (await response.text()) as unknown as T;
  return (await response.json()) as T;
}

/** For endpoints that answer with a file rather than JSON. */
export async function requestBlob(
  path: string,
  options: RequestOptions = {},
): Promise<{ blob: Blob; filename: string }> {
  const response = await send(path, options);
  const disposition = response.headers.get('content-disposition') ?? '';
  return { blob: await response.blob(), filename: filenameFrom(disposition) };
}

/**
 * The filename the server chose.
 *
 * `filename*` is preferred because it is the UTF-8 form: a company whose name
 * is not plain ASCII gets their own name on the download rather than the
 * stripped-down fallback.
 */
function filenameFrom(disposition: string): string {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(disposition);
  return plain ? plain[1] : 'download';
}

/** Hands the browser a file the API returned. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately cancels the download in Safari; a tick is enough.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
