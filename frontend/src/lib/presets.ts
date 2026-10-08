import type { Preset, PresetKind } from './types';

/**
 * The layer that lets a company choose behaviour without ever seeing a number.
 *
 * A company picks "Balanced" or "Let callers finish". The values behind those
 * labels — thresholds, voice-activity timings, model names — are visible and
 * editable only in the platform portal. Nothing in this file's `values` may be
 * rendered on a company screen; see `BANNED_TERMS` for the vocabulary that gives
 * it away.
 */
export const defaultPresets: Preset[] = [
  {
    id: 'strict', kind: 'strictness', label: 'Only our documents',
    description: 'The agent answers strictly from what you have uploaded. If it is not in your documents, it says it will find out and pass the question on.',
    consequence: 'Fewest wrong answers. Expect more "I will find that out for you".',
    values: { similarityThreshold: 0.68, topK: 4, maxContextChars: 6000 },
  },
  {
    id: 'balanced', kind: 'strictness', label: 'Balanced', isDefault: true,
    description: 'The agent answers from your documents and handles ordinary conversation naturally.',
    consequence: 'The usual choice. Accurate on your material, comfortable everywhere else.',
    values: { similarityThreshold: 0.6, topK: 6, maxContextChars: 9000 },
  },
  {
    id: 'flexible', kind: 'strictness', label: 'Flexible',
    description: 'The agent answers more freely and tries harder to help from partial matches. Use this only while your documents are still thin.',
    consequence: 'Fewer refusals, but a higher chance of an answer your documents do not fully support.',
    values: { similarityThreshold: 0.52, topK: 8, maxContextChars: 12000 },
  },
  {
    id: 'patient', kind: 'pace', label: 'Let callers finish',
    description: 'Waits longer before replying. Best for older callers, poor lines, or people reading out a number.',
    consequence: 'Calls run slightly longer, and the agent interrupts far less.',
    values: { vadSilenceMs: 900, vadThreshold: 0.55, vadEagerness: 'low', vadPrefixPaddingMs: 400, turnDetection: 'server_vad' },
  },
  {
    id: 'natural', kind: 'pace', label: 'Natural', isDefault: true,
    description: 'Replies the way a person would in ordinary conversation.',
    consequence: 'The usual choice for most lines.',
    values: { vadSilenceMs: 600, vadThreshold: 0.5, vadEagerness: 'medium', vadPrefixPaddingMs: 300, turnDetection: 'server_vad' },
  },
  {
    id: 'quick', kind: 'pace', label: 'Quick replies',
    description: 'Replies fast. Best for short, transactional calls.',
    consequence: 'Snappy, but the agent may start speaking before a slow caller has finished.',
    values: { vadSilenceMs: 380, vadThreshold: 0.45, vadEagerness: 'high', vadPrefixPaddingMs: 200, turnDetection: 'server_vad' },
  },
  {
    id: 'standard', kind: 'voiceQuality', label: 'Standard',
    description: 'Natural speech at a lower cost per minute.',
    consequence: 'Included on every plan.',
    values: { realtimeModel: 'gpt-realtime-mini', sttModel: 'whisper-1', realtimeReasoningEffort: 'low' },
  },
  {
    id: 'premium', kind: 'voiceQuality', label: 'Premium', isDefault: true,
    description: 'The most natural voice, and the best understanding of accents and noisy lines.',
    consequence: 'Included on Growth and Scale.',
    values: { realtimeModel: 'gpt-realtime', sttModel: 'gpt-4o-transcribe', realtimeReasoningEffort: 'medium' },
  },
];

export const presetKindLabel: Record<PresetKind, string> = {
  strictness: 'Answer strictness',
  pace: 'Conversation pace',
  voiceQuality: 'Voice quality',
};

export const presetKindHelp: Record<PresetKind, string> = {
  strictness: 'How closely the agent sticks to your uploaded documents.',
  pace: 'How long the agent waits before it replies.',
  voiceQuality: 'How natural the agent sounds, and how well it copes with a noisy line.',
};

export const presetsOfKind = (all: Preset[], kind: PresetKind) => all.filter(x => x.kind === kind);
export const findPreset = (all: Preset[], id: string) => all.find(x => x.id === id);

/**
 * Raw provider failures translated into something a company can act on.
 * The original text stays on the record and is shown only in the platform portal,
 * beside the same reference code the company was given.
 */
export interface FriendlyError { code: string; message: string; action?: string; actionTo?: string }
const errorMap: Record<string, FriendlyError> = {
  'E-CALL-PERM': { code: 'E-CALL-PERM', message: 'This customer has not allowed WhatsApp calls yet.', action: 'Send a message instead', actionTo: '/app/messages' },
  'E-NUM-APPROVAL': { code: 'E-NUM-APPROVAL', message: 'Your WhatsApp number is not approved for calls yet. We have been notified and are completing it for you.' },
  'E-UNREACHABLE': { code: 'E-UNREACHABLE', message: 'The call could not connect. The number may be switched off or unreachable.', action: 'Try again' },
  'E-KB-INDEXING': { code: 'E-KB-INDEXING', message: 'Your documents are still being prepared. Calls will use the previous version until this finishes.', action: 'View documents', actionTo: '/app/knowledge' },
  'E-NOT-ACTIVE': { code: 'E-NOT-ACTIVE', message: 'This number is not active yet. Setup is being completed for you.', action: 'See setup status', actionTo: '/app/channels' },
  'E-TEMPLATE': { code: 'E-TEMPLATE', message: 'This message format has not been approved yet, so the agent cannot offer it.', action: 'View message formats', actionTo: '/app/messages' },
};
const fallbackError: FriendlyError = { code: 'E-GENERAL', message: 'Something did not work. Our team has been notified and is looking at it.' };
export const friendlyError = (code?: string): FriendlyError => (code && errorMap[code]) || fallbackError;

/**
 * Vocabulary that must never reach a company or staff screen. Used by the
 * development-only audit in `auditCompanyCopy`.
 */
export const BANNED_TERMS = [
  'OpenAI', 'Gemini', 'Meta', 'Infobip', 'Graph API', 'WebRTC', 'SIP', 'trunk', 'RTP', 'STUN', 'TURN',
  'webhook', 'endpoint', 'API key', 'access token', 'app secret', 'bearer', 'HMAC', 'embedding',
  'vector', 'chunk', 'cosine', 'similarity', 'threshold', 'top-K', 'topK', 'temperature', 'VAD',
  'sample rate', 'codec', 'PCM', 'realtime model', 'LLM', 'RAG', 'latency', 'millisecond',
];

/** Scans rendered company-facing text for banned vocabulary. Dev aid only. */
export function auditCompanyCopy(text: string): string[] {
  const lower = text.toLowerCase();
  return BANNED_TERMS.filter(term => lower.includes(term.toLowerCase()));
}
