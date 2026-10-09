/**
 * The backend, translated into the shapes this app already speaks.
 *
 * The app reads a single `Store` snapshot synchronously and every screen is
 * built against those types. Rather than rewrite all of them, this module
 * fetches from the API and maps the result into the same shapes — so a screen
 * does not know or care whether its data came from a fixture or from Postgres.
 *
 * Where the two models differ, the backend wins and the mapping absorbs it:
 *
 * - The backend keys a company by Meta's `phone_number_id`; the app calls that
 *   an `orgId`. They are the same string here.
 * - The backend stores times as epoch seconds, the app as ISO strings.
 * - The backend has one tenant per *number*. The app's "channels" are the three
 *   ways that number can be used, derived from which credentials are set.
 */
import type {
  Call,
  Channel,
  ChannelStatus,
  ChannelType,
  Credential,
  Message,
  Organization,
  RecordingState,
  WaTemplate,
} from '../types';
import { API_BASE, request, requestBlob, saveBlob } from './http';

// ---------------------------------------------------------------------------
// Wire shapes — what the API actually returns
// ---------------------------------------------------------------------------

type CredentialStatus = { set: boolean; hint: string; secret: boolean; readOnly?: boolean };

export type WireCompany = {
  phoneNumberId: string;
  name: string;
  displayPhoneNumber: string | null;
  configured: boolean;
  language: string | null;
  ttsVoice: string | null;
  agentGreeting: string | null;
  persona?: string | null;
  recordCalls: boolean;
  autoReply: boolean;
  googleDrive: {
    connected: boolean;
    accountEmail: string | null;
    folderName: string | null;
    sheetLink: string | null;
  };
  telephony: { infobipConfigured: boolean; phoneNumber: string | null };
  credentials: Record<string, CredentialStatus>;
  channels: WireChannel[];
  webhookUrl: string;
};

type WireChannel = {
  id: ChannelType;
  label: string;
  connected: boolean;
  missing: string[];
  required: string[];
};

type WireCall = {
  id: string;
  tenantId: string;
  channel: 'PHONE' | 'WHATSAPP_CALL' | 'WHATSAPP_MESSAGE' | 'BROWSER';
  direction: 'INBOUND' | 'OUTBOUND';
  status: string;
  counterparty: string;
  fromNumber: string | null;
  startedAt: number;
  answeredAt: number | null;
  endedAt: number | null;
  durationSeconds: number;
  recording: { state: string; mime: string | null; bytes: number | null; available: boolean; error: string | null };
  transcript: string | null;
  summary: string | null;
  topic: string | null;
  handledBy: string;
  error: string | null;
};

type WireMessage = {
  id: string;
  tenantId: string;
  direction: 'INBOUND' | 'OUTBOUND';
  counterparty: string;
  kind: string;
  body: string;
  templateName: string | null;
  status: string;
  error: string | null;
  createdAt: number;
  callId: string | null;
};

// ---------------------------------------------------------------------------
// Conversions
// ---------------------------------------------------------------------------

/** Epoch seconds to the ISO strings every screen formats. */
const iso = (epoch: number | null | undefined): string =>
  epoch ? new Date(epoch * 1000).toISOString() : '';

const CHANNEL_OF: Record<WireCall['channel'], ChannelType> = {
  PHONE: 'sim',
  WHATSAPP_CALL: 'whatsapp_call',
  WHATSAPP_MESSAGE: 'whatsapp_message',
  // A browser leg is still a phone call to everyone looking at the list.
  BROWSER: 'sim',
};

/**
 * The app has three call states; the backend has seven.
 *
 * Anything still moving is `active`, anything that reached a person is
 * `completed`, and the rest is `failed` — a call nobody answered belongs with
 * the failures on a dashboard, not with the conversations.
 */
function callStatus(status: string): Call['status'] {
  if (status === 'QUEUED' || status === 'RINGING' || status === 'IN_PROGRESS') return 'active';
  if (status === 'COMPLETED' || status === 'HANDED_OFF') return 'completed';
  return 'failed';
}

/**
 * `ABSENT` and `FAILED` both become `NONE`: from the screen's point of view
 * there is no audio either way, and the reason is carried separately in
 * `error` so it can be shown as a sentence rather than a state.
 */
function recordingState(state: string): RecordingState {
  if (state === 'READY') return 'READY';
  if (state === 'PENDING') return 'PENDING';
  return 'NONE';
}

export function toCall(wire: WireCall): Call {
  return {
    id: wire.id,
    orgId: wire.tenantId,
    agentId: '',
    channelId: CHANNEL_OF[wire.channel],
    channelType: CHANNEL_OF[wire.channel],
    direction: wire.direction,
    phoneNumber: wire.counterparty,
    fromNumber: wire.direction === 'INBOUND' ? wire.counterparty : wire.fromNumber ?? '',
    toNumber: wire.direction === 'INBOUND' ? wire.fromNumber ?? '' : wire.counterparty,
    status: callStatus(wire.status),
    mode: wire.handledBy === 'agent' ? 'agent' : 'operator',
    startedAt: iso(wire.startedAt),
    answeredAt: wire.answeredAt ? iso(wire.answeredAt) : undefined,
    endedAt: wire.endedAt ? iso(wire.endedAt) : undefined,
    durationSeconds: wire.durationSeconds,
    outcome: wire.status,
    summary: wire.summary ?? '',
    recordingState: recordingState(wire.recording.state),
    recordingScope: wire.recording.available ? 'two_way' : 'none',
    // Left unset on purpose. An <audio> element sends no Authorization header,
    // so a bare URL here would 401 and the player would show controls that
    // silently do nothing. `playbackUrl` below mints a short-lived link.
    recordingUrl: undefined,
    lastEvent: wire.status,
    error: wire.error ?? wire.recording.error ?? undefined,
    knowledgeBaseId: '',
    knowledgeBaseName: '',
    resolvedBy: 'company',
  };
}

function messageStatus(status: string): Message['status'] {
  if (status === 'delivered' || status === 'read' || status === 'failed') return status;
  return 'sent';
}

export function toMessage(wire: WireMessage): Message {
  return {
    id: wire.id,
    orgId: wire.tenantId,
    callId: wire.callId ?? undefined,
    toNumber: wire.counterparty,
    templateId: wire.templateName ?? undefined,
    body: wire.body,
    status: messageStatus(wire.status),
    sentAt: iso(wire.createdAt),
  };
}

function channelStatus(channel: WireChannel): ChannelStatus {
  if (channel.connected) return 'connected';
  // Nothing entered at all reads as disconnected; a half-filled form is
  // pending, which is the more useful distinction on the channels screen.
  return channel.missing.length === channel.required.length ? 'disconnected' : 'pending';
}

export function toChannels(company: WireCompany): Channel[] {
  return company.channels.map((channel, index) => ({
    id: channel.id,
    orgId: company.phoneNumberId,
    type: channel.id,
    label: channel.label,
    isPrimary: index === 0,
    status: channelStatus(channel),
    displayNumber:
      channel.id === 'sim'
        ? company.telephony.phoneNumber ?? undefined
        : company.displayPhoneNumber ?? undefined,
    provider: channel.id === 'sim' ? 'Infobip' : 'Meta',
    errorDetail: channel.missing.length ? `Still needed: ${channel.missing.join(', ')}` : undefined,
    config: {},
  }));
}

/** The credential rows the settings screen lists, one per set field. */
export function toCredentials(company: WireCompany): Credential[] {
  const owner: Record<string, ChannelType> = {
    infobipApiKey: 'sim',
    infobipBaseUrl: 'sim',
    infobipPhoneNumber: 'sim',
    infobipCallsConfigurationId: 'sim',
    metaAppSecret: 'whatsapp_call',
    webhookVerifyToken: 'whatsapp_call',
    accessToken: 'whatsapp_message',
    businessAccountId: 'whatsapp_message',
  };
  return Object.entries(company.credentials)
    .filter(([name, status]) => status.set && owner[name])
    .map(([name, status]) => ({
      id: `${company.phoneNumberId}:${name}`,
      orgId: company.phoneNumberId,
      channelId: owner[name],
      keyName: name,
      lastFour: status.hint,
      setBy: 'the company',
      setAt: '',
    }));
}

/**
 * The company as the app's `Organization`.
 *
 * Several fields have no backend equivalent yet and are filled with settled
 * defaults rather than left undefined, because screens read them directly.
 */
export function toOrganization(company: WireCompany, previous?: Organization): Organization {
  return {
    ...(previous ?? ({} as Organization)),
    id: company.phoneNumberId,
    name: company.name,
    slug: company.phoneNumberId,
    accentColor: previous?.accentColor ?? '#79e2f0',
    plan: previous?.plan ?? 'standard',
    countryCode: previous?.countryCode ?? '',
    defaultLanguage: (company.language?.slice(0, 2) as Organization['defaultLanguage']) ?? 'en',
    timezone: previous?.timezone ?? 'UTC',
    createdAt: previous?.createdAt ?? '',
    trialEndsAt: previous?.trialEndsAt ?? '',
    onboardingStep: company.configured ? 4 : 1,
    status: company.configured ? 'active' : 'setup',
    provisioningMode: 'connected',
    strictnessPreset: previous?.strictnessPreset ?? 'balanced',
    pacePreset: previous?.pacePreset ?? 'natural',
    voiceQualityPreset: previous?.voiceQualityPreset ?? 'standard',
    recordCalls: company.recordCalls,
    recordBothSides: previous?.recordBothSides ?? true,
    retentionDays: previous?.retentionDays ?? 365,
    driveConnected: company.googleDrive.connected,
    driveFolderName: company.googleDrive.folderName ?? '',
    driveAccount: company.googleDrive.accountEmail ?? undefined,
    endCallsAfterMinutes: previous?.endCallsAfterMinutes ?? 15,
    businessHoursStart: previous?.businessHoursStart ?? '09:00',
    businessHoursEnd: previous?.businessHoursEnd ?? '17:00',
  };
}

// ---------------------------------------------------------------------------
// Calls to the API
// ---------------------------------------------------------------------------

export const live = {
  health: () => request<{ status: string; capabilities: Record<string, boolean> }>('/api/health', { anonymous: true }),

  company: () => request<WireCompany>('/api/companies/me'),

  updateCompany: (patch: Record<string, unknown>) =>
    request<WireCompany>('/api/companies/me', { method: 'PATCH', body: patch }),

  calls: async (limit = 200): Promise<Call[]> => {
    const body = await request<{ calls: WireCall[] }>('/api/calls', { query: { limit } });
    return body.calls.map(toCall);
  },

  call: async (callId: string): Promise<Call> => toCall(await request<WireCall>(`/api/calls/${callId}`)),

  /** Calls with audio actually moving through them, not just rows that say so. */
  liveCalls: () =>
    request<{ calls: Record<string, unknown>[]; total: number }>('/api/calls/live'),

  /** Ask someone whether the business may call them on WhatsApp. */
  askCallPermission: (to: string) =>
    request<{ status: string; messageId: string }>('/api/calls/permission', {
      method: 'POST',
      body: { to },
    }),

  handBack: (callId: string) =>
    request<{ handledBy: string }>(`/api/calls/${callId}/hand-back`, { method: 'POST' }),

  callStats: () => request<Record<string, number>>('/api/calls/stats'),

  endCall: async (callId: string): Promise<Call> =>
    toCall(await request<WireCall>(`/api/calls/${callId}/end`, { method: 'POST' })),

  deleteCall: (callId: string) => request<void>(`/api/calls/${callId}`, { method: 'DELETE' }),

  placeCall: async (to: string, channel: 'PHONE' | 'WHATSAPP_CALL'): Promise<Call> =>
    toCall(await request<WireCall>('/api/calls', { method: 'POST', body: { to, channel } })),

  transcript: (callId: string) =>
    request<{ transcript: string | null; summary: string | null; topic: string | null }>(
      `/api/calls/${callId}/transcript`,
    ),

  /** A URL an <audio> element can load: short-lived, and for this call only. */
  playbackUrl: async (callId: string): Promise<string> => {
    const body = await request<{ url: string }>(`/api/calls/${callId}/recording/link`, {
      method: 'POST',
    });
    return `${API_BASE}${body.url}`;
  },

  fetchRecording: (callId: string) =>
    request<{ status: string; detail?: string }>(`/api/calls/${callId}/recording/fetch`, { method: 'POST' }),

  uploadRecording: (callId: string, audio: Blob) =>
    request<{ status: string; bytes: number }>(`/api/calls/${callId}/recording`, {
      method: 'POST',
      raw: audio,
      contentType: audio.type || 'audio/webm',
    }),

  reportRecordingProblem: (callId: string, reason: string, ok = false) =>
    request<{ status: string }>(`/api/calls/${callId}/recording/problem`, {
      method: 'POST',
      body: { ok, reason },
    }),

  downloadRecording: async (callId: string) => {
    const { blob, filename } = await requestBlob(`/api/calls/${callId}/recording`, {
      query: { download: true },
    });
    saveBlob(blob, filename);
  },

  messages: async (limit = 200): Promise<Message[]> => {
    const body = await request<{ messages: WireMessage[] }>('/api/messages', { query: { limit } });
    return body.messages.map(toMessage);
  },

  sendText: async (to: string, body: string, callId?: string): Promise<Message> =>
    toMessage(await request<WireMessage>('/api/messages/text', { method: 'POST', body: { to, body, callId } })),

  sendTemplate: async (
    to: string,
    template: string,
    parameters: string[] = [],
    language = 'en_US',
  ): Promise<Message> =>
    toMessage(
      await request<WireMessage>('/api/messages/template', {
        method: 'POST',
        body: { to, template, parameters, language },
      }),
    ),

  templates: async (orgId: string): Promise<WaTemplate[]> => {
    const body = await request<{
      templates: { name: string; status: string; category: string; language: string }[];
    }>('/api/messages/templates');
    return body.templates.map((template) => ({
      id: template.name,
      orgId,
      name: template.name,
      language: template.language,
      category: template.category,
      status:
        template.status?.toUpperCase() === 'APPROVED'
          ? 'approved'
          : template.status?.toUpperCase() === 'REJECTED'
            ? 'rejected'
            : 'pending',
      bodyPreview: '',
      variables: [],
    }));
  },

  // ---- The company's own material ---------------------------------------

  knowledge: () =>
    request<{ documents: { id: string; name: string; chars: number; updatedAt: number }[];
              totalChars: number; limitChars: number }>('/api/knowledge'),

  /** Upload a document. The raw file is the body; PDFs are parsed server-side. */
  uploadKnowledge: (file: File) =>
    request<{ id: string; name: string; chars: number }>('/api/knowledge', {
      method: 'POST',
      query: { name: file.name },
      raw: file,
      contentType: file.type || 'application/octet-stream',
    }),

  deleteKnowledge: (documentId: string) =>
    request<void>(`/api/knowledge/${encodeURIComponent(documentId)}`, { method: 'DELETE' }),

  /** Exactly what the agent will be given, which is worth seeing before trusting it. */
  knowledgePreview: () =>
    request<{ chars: number; limitChars: number; preview: string; truncated: boolean }>(
      '/api/knowledge/preview',
    ),

  // ---- The operator's own view ------------------------------------------

  /** Every company on the platform. Needs the admin key. */
  companies: () =>
    request<{ companies: WireCompany[] }>('/api/companies'),

  registerCompany: (body: Record<string, unknown>) =>
    request<WireCompany & { apiKey: string; apiKeyNotice: string }>('/api/companies', {
      method: 'POST',
      body,
    }),

  rotateCompanyKey: (phoneNumberId: string) =>
    request<{ apiKey: string }>(
      `/api/companies/${encodeURIComponent(phoneNumberId)}/rotate-key`,
      { method: 'POST' },
    ),

  // ---- Usage, gaps and campaigns ----------------------------------------

  usage: () => request<Record<string, any>>('/api/usage'),

  gaps: () => request<Record<string, any>>('/api/gaps'),

  campaigns: () => request<{ campaigns: Record<string, any>[] }>('/api/campaigns'),

  campaign: (id: string) => request<Record<string, any>>(`/api/campaigns/${id}`),

  createCampaign: (body: Record<string, unknown>) =>
    request<Record<string, any>>('/api/campaigns', { method: 'POST', body }),

  updateCampaign: (id: string, body: Record<string, unknown>) =>
    request<Record<string, any>>(`/api/campaigns/${id}`, { method: 'PATCH', body }),

  startCampaign: (id: string) =>
    request<Record<string, any>>(`/api/campaigns/${id}/start`, { method: 'POST' }),

  pauseCampaign: (id: string) =>
    request<Record<string, any>>(`/api/campaigns/${id}/pause`, { method: 'POST' }),

  deleteCampaign: (id: string) =>
    request<void>(`/api/campaigns/${id}`, { method: 'DELETE' }),

  driveStatus: () =>
    request<{ connected: boolean; accountEmail: string | null; folderName: string | null; oauthConfigured: boolean }>(
      '/api/google/status',
    ),

  /** Returns the Google consent URL for the company to open. */
  connectDrive: (returnTo: string) =>
    request<{ url: string }>('/api/google/connect', { method: 'POST', query: { returnTo } }),

  disconnectDrive: () => request<{ connected: boolean }>('/api/google/disconnect', { method: 'POST' }),

  syncReport: () => request<{ url: string }>('/api/google/sync-report', { method: 'POST' }),

  downloadWorkbook: async () => {
    const { blob, filename } = await requestBlob('/api/exports/calls.xlsx');
    saveBlob(blob, filename);
  },
};
