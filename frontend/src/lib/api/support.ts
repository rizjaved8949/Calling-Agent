/**
 * The support conversation, and the socket that keeps it live.
 *
 * Both sides of the chat use this one module: the company talks to
 * `/api/support/...` and the operator to `/api/platform/support/{id}/...`,
 * which are the same shapes with a different front door. `endpointFor` is the
 * only place that difference lives, so the chat screen itself is written once.
 */
import { API_BASE, request } from './http';

export type Author = 'company' | 'operator';
export type AttachmentKind = 'image' | 'video' | 'voice' | 'file';

export type SupportAttachment = {
  mime: string;
  name: string;
  bytes: number;
  kind: AttachmentKind;
  durationSeconds: number;
};

export type SupportMessage = {
  id: string;
  author: Author;
  authorName: string;
  body: string;
  attachment: SupportAttachment | null;
  createdAt: number;
  readAt: number | null;
};

export type SupportThread = {
  tenantId: string;
  lastMessageAt: number;
  lastPreview: string;
  lastAuthor: Author | null;
  unreadForCompany: number;
  unreadForOperator: number;
};

export type ThreadRow = SupportThread & {
  name: string;
  suspended: boolean;
  online: boolean;
};

export type Conversation = {
  messages: SupportMessage[];
  thread: SupportThread;
  operatorOnline?: boolean;
  companyOnline?: boolean;
  company?: { id: string; name: string; suspended: boolean };
};

/** Where this side's requests go. `tenantId` empty means "I am the company". */
function endpointFor(tenantId: string): string {
  return tenantId ? `/api/platform/support/${encodeURIComponent(tenantId)}` : '/api/support';
}

export const supportApi = {
  conversation: (tenantId = '', limit = 200) =>
    request<Conversation>(`${endpointFor(tenantId)}/messages`, { query: { limit } }),

  /** Multipart: the attachment is a file, and base64 would inflate a video. */
  send: (tenantId: string, body: string, file: File | null, durationSeconds = 0) => {
    const form = new FormData();
    form.append('body', body);
    form.append('durationSeconds', String(durationSeconds));
    if (file) form.append('file', file, file.name);
    return request<SupportMessage>(`${endpointFor(tenantId)}/messages`, {
      method: 'POST', raw: form,
    });
  },

  markRead: (tenantId = '') =>
    request<{ read: number }>(`${endpointFor(tenantId)}/read`, { method: 'POST' }),

  remove: (tenantId: string, messageId: string) =>
    request<{ deleted: boolean }>(`${endpointFor(tenantId)}/messages/${messageId}`, {
      method: 'DELETE',
    }),

  /**
   * A URL the browser's own `<img>`, `<video>` or `<audio>` can load.
   *
   * Minted per read rather than stored on the message: it is signed, names one
   * file, and expires, so a link that escapes the page is worth nothing
   * afterwards.
   */
  attachmentUrl: async (tenantId: string, messageId: string): Promise<string> => {
    const where = tenantId
      ? `/api/platform/support/${encodeURIComponent(tenantId)}/attachments/${messageId}`
      : `/api/support/attachments/${messageId}`;
    const link = await request<{ url: string }>(where);
    return `${API_BASE}${link.url}`;
  },

  threads: () =>
    request<{ threads: ThreadRow[]; waiting: number }>('/api/platform/support/threads'),

  ticket: (tenantId?: string) =>
    request<{ ticket: string }>(
      tenantId === undefined
        ? '/api/support/ticket'
        : '/api/platform/support/ticket',
      tenantId ? { query: { tenantId } } : undefined,
    ),
};

// ---------------------------------------------------------------------------
// The socket
// ---------------------------------------------------------------------------

export type SocketEvent =
  | { type: 'hello'; side: Author; tenantId: string; operatorOnline: boolean; companyOnline: boolean }
  | { type: 'message'; tenantId: string; message: SupportMessage }
  | { type: 'read'; tenantId: string; by: Author }
  | { type: 'typing'; tenantId: string; side: Author; on: boolean }
  | { type: 'presence'; tenantId: string; side: Author; online: boolean }
  | { type: 'deleted'; tenantId: string; messageId: string; thread: SupportThread }
  | { type: 'resync'; tenantId?: string }
  | { type: 'ping' };

/** `wss://` for an `https://` API, and the page's own host when none is set. */
function socketUrl(ticket: string): string {
  const base = API_BASE || window.location.origin;
  const url = new URL(`${base}/api/support/live`, window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('ticket', ticket);
  return url.toString();
}

/**
 * A chat socket that reconnects.
 *
 * The socket carries liveness, never truth: every message is in the database
 * before it is pushed, and `onResync` asks the screen to refetch. So a
 * reconnect is allowed to be lossy, which is what lets the backoff be simple.
 */
export class SupportSocket {
  private socket: WebSocket | null = null;
  private closed = false;
  private attempt = 0;
  private timer: number | null = null;

  constructor(
    private readonly getTicket: () => Promise<string>,
    private readonly onEvent: (event: SocketEvent) => void,
    private readonly onStatus: (connected: boolean) => void,
  ) {}

  async open(): Promise<void> {
    if (this.closed) return;
    let ticket: string;
    try {
      ticket = (await this.getTicket()).trim();
    } catch {
      this.retry();
      return;
    }
    if (!ticket || this.closed) return;

    let socket: WebSocket;
    try {
      socket = new WebSocket(socketUrl(ticket));
    } catch {
      this.retry();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.onStatus(true);
    };
    socket.onmessage = event => {
      let frame: SocketEvent;
      try {
        frame = JSON.parse(event.data as string) as SocketEvent;
      } catch {
        return;
      }
      // The keepalive is the server proving the socket is still there. It
      // carries nothing, so it never reaches the screen.
      if (frame.type !== 'ping') this.onEvent(frame);
    };
    socket.onclose = () => {
      this.socket = null;
      this.onStatus(false);
      this.retry();
    };
    socket.onerror = () => {
      // `onclose` always follows, and that is where the retry lives.
    };
  }

  private retry(): void {
    if (this.closed || this.timer !== null) return;
    // Backs off to half a minute, with jitter so a restarted server is not hit
    // by every open tab at the same instant.
    const wait = Math.min(30_000, 500 * 2 ** this.attempt) * (0.7 + Math.random() * 0.6);
    this.attempt += 1;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.open();
    }, wait);
  }

  send(frame: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify(frame));
      } catch {
        /* the close handler will reconnect */
      }
    }
  }

  close(): void {
    this.closed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      try {
        socket.close();
      } catch {
        /* already gone */
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Words and sizes
// ---------------------------------------------------------------------------

export const KIND_LABEL: Record<AttachmentKind, string> = {
  image: 'Photo', video: 'Video', voice: 'Voice note', file: 'File',
};

/** Matches the server's own limits, so a refusal is shown before the upload. */
export const MAX_BYTES: Record<AttachmentKind, number> = {
  image: 10 * 1024 * 1024,
  video: 40 * 1024 * 1024,
  voice: 10 * 1024 * 1024,
  file: 10 * 1024 * 1024,
};

export const ACCEPT =
  'image/png,image/jpeg,image/webp,image/gif,image/heic,' +
  'video/mp4,video/webm,video/quicktime,' +
  'audio/webm,audio/ogg,audio/mpeg,audio/mp4,audio/wav,' +
  'application/pdf,.txt,.csv';

export function kindOf(mime: string): AttachmentKind {
  const type = (mime || '').split(';')[0].trim().toLowerCase();
  if (type.startsWith('image/')) return 'image';
  if (type.startsWith('video/')) return 'video';
  if (type.startsWith('audio/')) return 'voice';
  return 'file';
}

/** The refusal the server would give, said before spending the upload on it. */
export function tooLarge(file: File): string {
  const kind = kindOf(file.type);
  const limit = MAX_BYTES[kind];
  if (file.size <= limit) return '';
  return `That ${KIND_LABEL[kind].toLowerCase()} is ${Math.round(file.size / 1_048_576)} MB. `
    + `The limit is ${Math.round(limit / 1_048_576)} MB.`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** "14:32" today, "4 Mar 14:32" before that. */
export function formatWhen(epochSeconds: number): string {
  if (!epochSeconds) return '';
  const date = new Date(epochSeconds * 1000);
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay
    ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : date.toLocaleString(undefined, {
        day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
      });
}

/** The heading over a run of messages from the same day. */
export function dayLabel(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000);
  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString(undefined, {
    day: 'numeric', month: 'long', year:
      date.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  });
}
