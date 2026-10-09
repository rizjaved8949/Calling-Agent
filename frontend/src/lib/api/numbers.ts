/**
 * Numbers, and calls placed from a chosen number.
 *
 * A company connects each number with its own provider credentials, verifies
 * it, then gives it an inbound and/or outbound agent. Calls — the agent's or
 * an employee's from the dialer — go out on one of these.
 */
import { request } from './http';

export type NumberKind = 'sim' | 'whatsapp';
export type NumberMode = 'inbound' | 'outbound' | 'both';
export type NumberStatus = 'pending' | 'verified' | 'failed';

export type PhoneNumber = {
  id: string;
  kind: NumberKind;
  label: string;
  phoneNumber: string;
  mode: NumberMode;
  status: NumberStatus;
  statusDetail: string | null;
  verifiedAt: number | null;
  inboundAgentId: string | null;
  outboundAgentId: string | null;
  autoReply: boolean;
  /** WhatsApp only: who writes the replies, and what they answer from. */
  messageAgentId: string | null;
  messageKnowledgeBaseId: string | null;
  usePlatformCredentials: boolean;
  createdAt: number;
  webhookUrl: string | null;
  credentials: {
    metaPhoneNumberId: string | null;
    wabaId: string | null;
    accessToken: string | null;
    appSecret: string | null;
    verifyToken: string | null;
    infobipApiKey: string | null;
    infobipBaseUrl: string | null;
    infobipCallsConfigurationId: string | null;
  };
};

export type NumberCredentials = {
  metaPhoneNumberId?: string;
  wabaId?: string;
  accessToken?: string;
  appSecret?: string;
  verifyToken?: string;
  infobipApiKey?: string;
  infobipBaseUrl?: string;
  infobipCallsConfigurationId?: string;
};

export type NumberCreate = NumberCredentials & {
  kind: NumberKind;
  label?: string;
  phoneNumber: string;
  mode: NumberMode;
  usePlatformCredentials?: boolean;
};

export type NumberPatch = NumberCredentials & {
  label?: string;
  phoneNumber?: string;
  mode?: NumberMode;
  inboundAgentId?: string;
  outboundAgentId?: string;
  autoReply?: boolean;
  messageAgentId?: string;
  messageKnowledgeBaseId?: string;
  usePlatformCredentials?: boolean;
};

export type CallSession = {
  callId: string;
  status: string;
  live: boolean;
  error: string | null;
  operatorToken: string | null;
  transcript: string | null;
};

export type PlacedCall = { id: string; status: string; lineId: string | null; error: string | null };

export const numbers = {
  list: () =>
    request<{ numbers: PhoneNumber[]; platformCredentialsAllowed: boolean; whatsappWebhookUrl: string | null }>(
      '/api/numbers',
    ),
  create: (payload: NumberCreate) => request<PhoneNumber>('/api/numbers', { method: 'POST', body: payload }),
  update: (id: string, patch: NumberPatch) =>
    request<PhoneNumber>(`/api/numbers/${id}`, { method: 'PATCH', body: patch }),
  verify: (id: string) => request<PhoneNumber>(`/api/numbers/${id}/verify`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/api/numbers/${id}`, { method: 'DELETE' }),

  /** Place a call. `human` puts the employee on the line instead of the agent. */
  placeCall: (payload: {
    to: string;
    channel: 'PHONE' | 'WHATSAPP_CALL';
    lineId?: string;
    agentId?: string;
    human?: boolean;
    placedBy?: string;
  }) =>
    request<PlacedCall>('/api/calls', {
      method: 'POST',
      body: {
        to: payload.to,
        channel: payload.channel,
        lineId: payload.lineId ?? '',
        agentId: payload.agentId ?? '',
        human: payload.human ?? false,
        metadata: payload.placedBy ? { placedBy: payload.placedBy } : {},
      },
    }),
  callSession: (callId: string) => request<CallSession>(`/api/calls/${callId}/session`),
  endCall: (callId: string) => request<unknown>(`/api/calls/${callId}/end`, { method: 'POST' }),
  askPermission: (to: string, lineId: string) =>
    request<unknown>('/api/calls/permission', { method: 'POST', body: { to, lineId } }),
};

/** "+92 300 1112222 · Support line" — how a number is named in pickers. */
export function numberName(n: PhoneNumber): string {
  return n.label && n.label !== n.phoneNumber ? `${n.label} (${n.phoneNumber})` : n.phoneNumber;
}

export function canCallOut(n: PhoneNumber): boolean {
  return n.status === 'verified' && (n.mode === 'outbound' || n.mode === 'both');
}
