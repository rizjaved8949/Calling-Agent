/**
 * Agents, knowledge bases, call setups, and the team — the four things a
 * company configures beyond its own credentials.
 *
 * A thin, direct client rather than routed through the big fixture/live
 * facade in `index.ts`: these screens only exist once there is a backend to
 * talk to (a fixture store has no honest way to model "this number answers
 * from this base"), so there is no mock branch to keep in step with.
 */
import { request } from './http';

export type KnowledgeBase = {
  id: string;
  name: string;
  purpose: string;
  isDefault: boolean;
  documentCount: number;
  createdAt: number;
};

export type KbDocument = {
  id: string;
  name: string;
  chars: number;
  knowledgeBaseId: string;
  updatedAt?: number;
};

export type AgentStatus = 'draft' | 'live' | 'paused';

export type Agent = {
  id: string;
  name: string;
  status: AgentStatus;
  greeting: string;
  roleDescription: string;
  language: string;
  toneNotes: string;
  escalationRules: string;
  ttsVoice: string;
  knowledgeBaseId: string;
  createdAt: number;
};

export type Channel = 'PHONE' | 'WHATSAPP_CALL' | 'WHATSAPP_MESSAGE';
export type Direction = 'INBOUND' | 'OUTBOUND';

export type CallSetup = {
  id: string;
  name: string;
  channel: Channel;
  direction: Direction;
  agentId: string;
  knowledgeBaseId: string;
  enabled: boolean;
  createdAt: number;
};

export type TeamMember = {
  userId: string;
  email: string;
  name: string;
  role: 'owner' | 'staff';
  isYou: boolean;
};

export type TeamInvite = { token: string; email: string; role: 'owner' | 'staff' };

// ---- Knowledge bases --------------------------------------------------

export const workspace = {
  knowledgeBases: () =>
    request<{ knowledgeBases: KnowledgeBase[]; unfiledCount: number }>('/api/knowledge-bases'),
  createKnowledgeBase: (name: string, purpose: string, isDefault: boolean) =>
    request<KnowledgeBase>('/api/knowledge-bases', {
      method: 'POST', body: { name, purpose, isDefault },
    }),
  updateKnowledgeBase: (id: string, patch: Partial<Pick<KnowledgeBase, 'name' | 'purpose' | 'isDefault'>>) =>
    request<KnowledgeBase>(`/api/knowledge-bases/${id}`, { method: 'PATCH', body: patch }),
  deleteKnowledgeBase: (id: string) =>
    request<void>(`/api/knowledge-bases/${id}`, { method: 'DELETE' }),

  documents: (knowledgeBaseId?: string) =>
    request<{ documents: KbDocument[]; totalChars: number; limitChars: number }>('/api/knowledge', {
      query: knowledgeBaseId !== undefined ? { knowledgeBaseId } : undefined,
    }),
  uploadDocument: (file: File, knowledgeBaseId: string) =>
    file.arrayBuffer().then(buf =>
      request<KbDocument>('/api/knowledge', {
        method: 'POST',
        raw: buf,
        contentType: file.type || 'application/octet-stream',
        query: { name: file.name, knowledgeBaseId },
      }),
    ),
  deleteDocument: (documentId: string) =>
    request<void>(`/api/knowledge/${documentId}`, { method: 'DELETE' }),

  // ---- Agents ------------------------------------------------------------

  agents: () => request<{ agents: Agent[] }>('/api/agents'),
  agent: (id: string) => request<Agent>(`/api/agents/${id}`),
  createAgent: (payload: Partial<Agent> & { name: string }) =>
    request<Agent>('/api/agents', { method: 'POST', body: payload }),
  updateAgent: (id: string, patch: Partial<Agent>) =>
    request<Agent>(`/api/agents/${id}`, { method: 'PATCH', body: patch }),
  deleteAgent: (id: string) => request<void>(`/api/agents/${id}`, { method: 'DELETE' }),

  // ---- Call setups ---------------------------------------------------------

  callSetups: () => request<{ callSetups: CallSetup[] }>('/api/call-setups'),
  createCallSetup: (payload: Omit<CallSetup, 'id' | 'createdAt'>) =>
    request<CallSetup>('/api/call-setups', { method: 'POST', body: payload }),
  updateCallSetup: (id: string, patch: Partial<CallSetup>) =>
    request<CallSetup>(`/api/call-setups/${id}`, { method: 'PATCH', body: patch }),
  deleteCallSetup: (id: string) => request<void>(`/api/call-setups/${id}`, { method: 'DELETE' }),

  // ---- Team ----------------------------------------------------------------

  team: () => request<{ members: TeamMember[]; invites: TeamInvite[] }>('/api/team'),
  invite: (email: string, role: 'staff') =>
    request<TeamInvite>('/api/team/invite', { method: 'POST', body: { email, role } }),
  revokeInvite: (token: string) => request<void>(`/api/team/invite/${token}`, { method: 'DELETE' }),
  removeMember: (userId: string) => request<void>(`/api/team/${userId}`, { method: 'DELETE' }),
};
