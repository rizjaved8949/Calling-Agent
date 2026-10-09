/**
 * Calls, recordings and WhatsApp messages, in the shapes the backend actually
 * returns.
 *
 * The older client in `live.ts` converts every row into the fixture-era
 * `Call`/`Message` types the prototype screens were written against, which
 * drops the fields that now matter (which number carried it, whether a person
 * or the agent spoke, why a recording is missing) and invents ones the backend
 * does not have. The live screens read this instead.
 */
import { API_BASE, request, requestBlob, saveBlob } from './http';

export type Channel = 'PHONE' | 'WHATSAPP_CALL' | 'WHATSAPP_MESSAGE' | 'BROWSER';
export type Direction = 'INBOUND' | 'OUTBOUND';
export type CallStatus =
  | 'QUEUED' | 'RINGING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'NO_ANSWER' | 'HANDED_OFF';
export type RecordingState = 'NONE' | 'PENDING' | 'READY' | 'ABSENT' | 'FAILED';

export type WireCall = {
  id: string;
  channel: Channel;
  direction: Direction;
  status: CallStatus;
  counterparty: string;
  fromNumber: string | null;
  startedAt: number;
  answeredAt: number | null;
  endedAt: number | null;
  durationSeconds: number;
  recording: {
    state: RecordingState;
    mime: string | null;
    bytes: number | null;
    available: boolean;
    error: string | null;
  };
  transcript: string | null;
  summary: string | null;
  topic: string | null;
  language: string | null;
  handledBy: string;
  agentId: string | null;
  knowledgeBaseId: string | null;
  knowledgeBaseName: string | null;
  resolvedBy: string | null;
  lineId: string | null;
  mode: string;
  placedBy: string | null;
  error: string | null;
};

export type WireMessage = {
  id: string;
  providerMessageId: string | null;
  direction: Direction;
  counterparty: string;
  kind: string;
  body: string;
  templateName: string | null;
  status: string;
  error: string | null;
  createdAt: number;
  callId: string | null;
  lineId: string | null;
};

export type CallQuery = {
  limit?: number;
  offset?: number;
  status?: CallStatus | '';
  channel?: Channel | '';
  counterparty?: string;
};

export const callsApi = {
  list: (query: CallQuery = {}) =>
    request<{ calls: WireCall[]; limit: number; offset: number }>('/api/calls', {
      query: {
        limit: query.limit ?? 50,
        offset: query.offset ?? 0,
        status: query.status || undefined,
        channel: query.channel || undefined,
        counterparty: query.counterparty || undefined,
      },
    }),
  get: (callId: string) => request<WireCall>(`/api/calls/${callId}`),
  stats: () => request<Record<string, number>>('/api/calls/stats'),
  end: (callId: string) => request<WireCall>(`/api/calls/${callId}/end`, { method: 'POST' }),
  remove: (callId: string) => request<void>(`/api/calls/${callId}`, { method: 'DELETE' }),

  // ---- Recordings --------------------------------------------------------

  /** A URL an `<audio>` element can load: short-lived, and for this call only. */
  playbackUrl: async (callId: string): Promise<string> => {
    const body = await request<{ url: string }>(`/api/calls/${callId}/recording/link`, {
      method: 'POST',
    });
    return `${API_BASE}${body.url}`;
  },
  download: async (callId: string) => {
    const { blob, filename } = await requestBlob(`/api/calls/${callId}/recording`, {
      query: { download: true },
    });
    saveBlob(blob, filename);
  },
  deleteRecording: (callId: string) =>
    request<void>(`/api/calls/${callId}/recording`, { method: 'DELETE' }),
  /** Ask the carrier for its own copy, for a call recorded on their side. */
  fetchFromCarrier: (callId: string) =>
    request<{ status: string; detail?: string }>(`/api/calls/${callId}/recording/fetch`, {
      method: 'POST',
    }),
};

export const messagesApi = {
  list: (limit = 300) =>
    request<{ messages: WireMessage[] }>('/api/messages', { query: { limit } }),
  sendText: (to: string, body: string, lineId = '') =>
    request<WireMessage>('/api/messages/text', { method: 'POST', body: { to, body, lineId } }),
  sendTemplate: (to: string, template: string, parameters: string[], language: string, lineId = '') =>
    request<WireMessage>('/api/messages/template', {
      method: 'POST',
      body: { to, template, parameters, language, lineId },
    }),
  remove: (messageId: string) => request<void>(`/api/messages/${messageId}`, { method: 'DELETE' }),
  markRead: (messageId: string) =>
    request<{ status: string }>(`/api/messages/${messageId}/read`, { method: 'POST' }),
  templates: (lineId = '') =>
    request<{ templates: { name: string; status: string; category: string; language: string }[] }>(
      '/api/messages/templates',
      { query: lineId ? { lineId } : undefined },
    ),
};

// ---------------------------------------------------------------------------
// Words for the values above
// ---------------------------------------------------------------------------

export const CHANNEL_LABEL: Record<string, string> = {
  PHONE: 'Phone line',
  WHATSAPP_CALL: 'WhatsApp call',
  WHATSAPP_MESSAGE: 'WhatsApp message',
  BROWSER: 'Browser',
};

/** Said as a person would: "nobody answered", not "NO_ANSWER". */
export const STATUS_LABEL: Record<CallStatus, string> = {
  QUEUED: 'queued',
  RINGING: 'ringing',
  IN_PROGRESS: 'on the call',
  COMPLETED: 'completed',
  FAILED: 'failed',
  NO_ANSWER: 'no answer',
  HANDED_OFF: 'handed to a person',
};

export const RECORDING_LABEL: Record<RecordingState, string> = {
  NONE: 'not recorded',
  PENDING: 'waiting for audio',
  READY: 'ready',
  ABSENT: 'none arrived',
  FAILED: 'recording failed',
};

export function statusTone(status: CallStatus): string {
  if (status === 'COMPLETED') return 'success';
  if (status === 'FAILED') return 'danger';
  if (status === 'NO_ANSWER') return 'warning';
  if (status === 'IN_PROGRESS' || status === 'RINGING') return 'live';
  return '';
}

/** "Why did it answer that?" — the chain in services/routing.py, in words. */
export const RESOLVED_BY_LABEL: Record<string, string> = {
  number: 'the agent assigned to this number',
  setup: 'a call setup for this line',
  explicit: 'a knowledge base chosen for this call',
  agent: "the agent's own knowledge base",
  company: 'everything the company has uploaded',
};

export function formatDuration(seconds: number): string {
  if (!seconds) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

export function formatWhen(epochSeconds: number): string {
  if (!epochSeconds) return '—';
  const date = new Date(epochSeconds * 1000);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : date.toLocaleString(undefined, {
        day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
      });
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '—';
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
