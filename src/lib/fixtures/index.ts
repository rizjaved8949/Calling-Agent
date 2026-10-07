import type { Store, Call, TranscriptEntry, RagQuery, Guide, KnowledgeBase, KbDocument, KbChunk, UnansweredQuestion } from '../types';
import { defaultPresets } from '../presets';

const now = Date.now();
const date = (days = 0) => new Date(now - days * 86400000).toISOString();
const orgId = 'org-northstar';
const names = ['Aisha', 'Nora', 'Zara'];
const outcomes = ['Resolved', 'Appointment booked', 'Escalated', 'No answer', 'Follow-up needed'];
const questions = ['What are your admission requirements?', 'Can I change my appointment?', 'What is the fee for the BS programme?', 'Is weekend support available?'];
const answers = ['I can help with that. Let me check the latest information.', 'That is covered in the material you have given me.', 'I can arrange a follow-up with our team.'];

/** Four knowledge bases, so a call has something real to choose between. */
const kbSeed: [string, string, string, boolean, number][] = [
  ['kb-admissions', 'Admissions 2026', 'Entry requirements, deadlines, merit lists and the application process.', true, 284],
  ['kb-fees', 'Fees and scholarships', 'Tuition by programme, instalment plans, scholarships and financial aid.', false, 163],
  ['kb-campus', 'Hostel and transport', 'Accommodation, room types, bus routes and campus facilities.', false, 71],
  ['kb-alumni', 'Alumni services', 'Transcripts, degree verification and alumni events.', false, 24],
];
const knowledgeBases: KnowledgeBase[] = kbSeed.map(([id, name, purpose, isDefault, answered], i) => ({
  id, orgId, name, purpose, isDefault,
  status: i === 3 ? 'indexing' : 'ready',
  chunkSize: 800, chunkOverlap: 120, embeddingModel: 'text-embedding-3-small',
  similarityThreshold: 0.6, topK: 6, maxChunksPerSection: 2, maxContextChars: 9000,
  chunkCount: [283, 164, 72, 0][i], lastIndexedAt: i === 3 ? undefined : date(2 + i),
  answeredLast30d: answered,
}));

const docSeed: [string, string, string, number, number][] = [
  ['kb-admissions', 'Admissions handbook.pdf', 'Admissions', 42, 116],
  ['kb-admissions', 'Entry test guide.pdf', 'Entry test', 16, 48],
  ['kb-admissions', 'Merit list policy.docx', 'Merit', 9, 27],
  ['kb-admissions', 'Legacy admissions policy.pdf', 'Policy', 0, 0],
  ['kb-fees', 'Tuition and fees 2026.pdf', 'Fees', 18, 56],
  ['kb-fees', 'Scholarships.pdf', 'Financial aid', 12, 34],
  ['kb-fees', 'Instalment plans.txt', 'Instalments', 3, 11],
  ['kb-campus', 'Hostel handbook.pdf', 'Hostel', 14, 41],
  ['kb-campus', 'Bus routes.docx', 'Transport', 6, 18],
  ['kb-alumni', 'Transcript requests.md', 'Transcripts', 4, 0],
];
const documents: KbDocument[] = docSeed.map(([kbId, filename, , pages, chunks], i) => ({
  id: 'doc-' + (i + 1), kbId, filename,
  mime: filename.endsWith('.pdf') ? 'application/pdf' : 'text/plain',
  bytes: (i + 2) * 143000, pages,
  status: i === 3 ? 'failed' : i === 9 ? 'indexing' : 'ready',
  uploadedBy: 'Samira Khan', chunkCount: chunks,
  error: i === 3 ? 'This file is password protected, so its text could not be read.' : undefined,
}));
const chunks: KbChunk[] = docSeed
  .filter((_, i) => i !== 3 && i !== 9)
  .flatMap(([kbId, filename, section], i) => Array.from({ length: 2 }, (_, j) => ({
    id: 'chunk-' + i + '-' + j, kbId, documentId: 'doc-' + (i + 1), section, title: filename,
    page: j + 1, category: section,
    preview: 'Current information for ' + section.toLowerCase() + '. The full policy explains eligibility, timing and next steps in clear terms.',
    score: 0.83 - i * 0.03 - j * 0.025,
  })));

/** Spread the seeded calls across the knowledge bases, with a plausible reason. */
/** Which of the company's numbers a seeded call came in on. */
const channelForCall = (i: number): [string, 'sim'|'whatsapp_call'] => {
  if (i % 3 === 0) return i % 9 === 0 ? ['ch-fee-line', 'sim'] : ['ch-main-line', 'sim'];
  return i % 8 === 0 ? ['ch-wa-alumni', 'whatsapp_call'] : ['ch-wa-main', 'whatsapp_call'];
};
const kbForCall = (i: number): [string, string, Call['resolvedBy']] => {
  if (i % 5 === 0) return ['kb-fees', 'Fees and scholarships', 'rule'];
  if (i % 7 === 0) return ['kb-campus', 'Hostel and transport', 'rule'];
  if (i % 11 === 0) return ['kb-admissions', 'Admissions 2026', 'explicit'];
  if (i % 3 === 0) return ['kb-admissions', 'Admissions 2026', 'agent'];
  return ['kb-admissions', 'Admissions 2026', 'company'];
};
const calls: Call[] = Array.from({ length: 400 }, (_, i) => {
  const age = (i * 89 / 399) + (i % 3) / 24;
  const start = date(age);
  const duration = 44 + ((i * 73) % 620);
  const state = i % 43 === 0 ? 'NONE' : i % 31 === 0 ? 'PENDING' : 'READY';
  const [knowledgeBaseId, knowledgeBaseName, resolvedBy] = kbForCall(i);
  const [channelId, channelType] = channelForCall(i);
  const failed = i !== 0 && i % 23 === 0;
  return {
    id: 'call-' + String(i + 1).padStart(4, '0'), orgId, agentId: 'agent-' + (i % 3 + 1),
    channelId, channelType,
    direction: i % 5 === 0 ? 'OUTBOUND' : 'INBOUND',
    phoneNumber: '+92 3' + String(100000000 + ((i * 7919) % 899999999)).slice(0, 9),
    fromNumber: '+92 300 123 4567',
    toNumber: channelId === 'ch-fee-line' ? '+92 21 555 0188' : channelId === 'ch-wa-alumni' ? '+92 300 555 0174' : channelId === 'ch-main-line' ? '+92 21 555 0142' : '+92 300 555 0129',
    status: i === 0 ? 'active' : failed ? 'failed' : 'completed',
    mode: i % 11 === 0 ? 'operator' : 'agent', startedAt: start, answeredAt: start,
    endedAt: i === 0 ? undefined : new Date(new Date(start).getTime() + duration * 1000).toISOString(),
    durationSeconds: duration, outcome: outcomes[i % outcomes.length],
    summary: 'Caller asked about ' + questions[i % questions.length].toLowerCase() + ' The agent answered from the material provided and confirmed next steps.',
    recordingState: i === 0 ? 'RECORDING' : state,
    recordingUrl: state === 'READY' && i !== 0 ? '/demo-call.wav' : undefined,
    recordingScope: state === 'NONE' ? 'none' : i % 7 === 0 ? 'caller_only' : 'two_way',
    lastEvent: i === 0 ? 'rag.query' : 'call.updated',
    errorCode: failed ? (i % 46 === 0 ? 'E-CALL-PERM' : 'E-UNREACHABLE') : undefined,
    error: failed ? (i % 46 === 0 ? 'calling_permission_denied (code 138003)' : 'sip 480 temporarily unavailable') : undefined,
    knowledgeBaseId, knowledgeBaseName, resolvedBy,
    campaignId: i % 5 === 0 && i < 60 ? 'camp-1' : undefined,
    assignedToUserId: i % 11 === 0 ? 'user-2' : undefined,
  };
});
const transcripts: TranscriptEntry[] = calls.slice(0, 25).flatMap((call, i) => [
  { id: call.id + '-t1', callId: call.id, role: 'agent', speaker: names[i % 3], text: 'Hello, thank you for calling. How can I help you today?', timestamp: call.startedAt },
  { id: call.id + '-t2', callId: call.id, role: 'caller', speaker: 'Caller', text: questions[i % questions.length], timestamp: new Date(new Date(call.startedAt).getTime() + 5000).toISOString() },
  { id: call.id + '-t3', callId: call.id, role: 'agent', speaker: names[i % 3], text: answers[i % answers.length], timestamp: new Date(new Date(call.startedAt).getTime() + 12000).toISOString() },
]);
const ragQueries: RagQuery[] = calls.slice(0, 25).map((call, i) => ({
  id: call.id + '-rag', callId: call.id, query: questions[i % questions.length],
  topScore: i % 5 === 0 ? 0.55 : 0.76 + (i % 10) / 100, threshold: 0.6,
  hitCount: i % 5 === 0 ? 0 : 3, answered: i % 5 !== 0,
  sections: ['Admissions', 'Entry test'],
  documentName: i % 5 === 0 ? undefined : 'Admissions handbook.pdf', page: 12 + (i % 9),
  timestamp: call.startedAt,
}));

/** The questions the agent could not answer, which drive the improvement loop. */
const unansweredSeed: [string, number, string][] = [
  ['Do you accept A-level equivalence from Cambridge?', 18, 'kb-admissions'],
  ['Is there a fee instalment plan for the BS programme?', 14, 'kb-fees'],
  ['What is the hostel fee for a shared room?', 11, 'kb-campus'],
  ['Can I apply after the merit list is published?', 9, 'kb-admissions'],
  ['Do you offer a sibling discount?', 7, 'kb-fees'],
  ['Is there a shuttle from Johar Town?', 6, 'kb-campus'],
  ['How long does a transcript request take?', 5, 'kb-alumni'],
  ['Do you have evening classes for working students?', 4, 'kb-admissions'],
  ['Is the entry test online or on campus?', 3, 'kb-admissions'],
  ['Can my parent collect my degree on my behalf?', 2, 'kb-alumni'],
];
const unanswered: UnansweredQuestion[] = unansweredSeed.map(([question, askedCount, knowledgeBaseId], i) => ({
  id: 'unans-' + (i + 1), orgId, question, askedCount, lastAskedAt: date(i * 0.7),
  knowledgeBaseId, exampleCallIds: [calls[i * 3].id, calls[i * 3 + 1].id],
  status: i === 8 ? 'answered' : i === 9 ? 'ignored' : 'open',
}));

const guideInfo = [
  ['getting-started', 'Getting started in 30 minutes', 'Start here', 'Name your agent, write the greeting it will say when it picks up, and upload the material it should answer from. We set your number up for you in the background, so you can do all of this while you wait.'],
  ['knowledge-writing', 'Write material your agent can answer from', 'Knowledge', 'Use clear headings and one topic per section. Remove anything out of date, because the agent trusts whatever you give it. Upload PDF, Word, text or Markdown files, then use Ask your documents to try the questions your callers actually ask.'],
  ['multiple-knowledge', 'Use more than one knowledge base', 'Knowledge', 'Keep separate material separate. Admissions, fees and hostel questions each work better in their own knowledge base, because the agent then searches a smaller and more relevant set. Use routing rules to decide which one a call uses.'],
  ['routing', 'Send calls to the right agent and material', 'Routing', 'Rules are read top to bottom and the first match wins. A rule can match the number dialled, the channel, where the caller is calling from, or the time of day. The last row catches everything else, so no call is ever left without an answer.'],
  ['strictness', 'Choose how strict your agent should be', 'Knowledge', 'Only our documents keeps the agent tightly on your material and makes it offer to find out when it is unsure. Balanced suits most companies. Flexible is for when your material is still thin and you would rather the agent tried.'],
  ['persona', 'Write a greeting and persona that sound human', 'Agents', 'Keep the greeting short and easy to hear on a phone line. Say who the agent is, when it should hand over to a person, and anything it must never say. Listen to a test call before you go live.'],
  ['campaigns', 'Run an outbound calling campaign', 'Outbound', 'Upload your list, choose the agent and the material it should use, and set the hours you are willing to call within. Start small, listen to the first few calls, then widen the window.'],
  ['unanswered', 'Close the gaps your callers find', 'Knowledge', 'Every time the agent cannot answer, the question is captured. Work down that list and add the answers. This is the fastest way to make the agent better, and it costs nothing but the writing.'],
  ['staff', 'Add your team and take calls yourself', 'Team', 'Invite your colleagues as staff. They get their own queue, can take over a live call from the agent, and only ever see their own calls.'],
  ['recording', 'Recording, retention and consent', 'Settings', 'Decide whether to record, whether to record both sides, and how long to keep recordings. Make sure your greeting tells callers they are being recorded if that is required where you operate.'],
  ['numbers', 'Your numbers and what the statuses mean', 'Channels', 'Requested means we have your request. Being set up means we are doing the work. Testing means we are placing test calls. Active means it is live and answering. You do not need to do anything while this runs.'],
  ['go-live', 'Before you go live', 'Start here', 'Check the greeting, upload your material, try ten real questions in Ask your documents, place a test call, and listen to it end to end. Then turn the agent on.'],
] as const;
const guides: Guide[] = guideInfo.map(([slug, title, category, bodyMd]) => ({ slug, title, category, bodyMd, readingMinutes: 3 + (slug.length % 4), updatedAt: date(8) }));

export const initialStore: Store = {
  organizations: [
    { id: orgId, name: 'Northstar University', slug: 'northstar', accentColor: 'oklch(0.58 0.13 245)', plan: 'Growth', countryCode: '+92', defaultLanguage: 'ur', timezone: 'Asia/Karachi', createdAt: date(85), trialEndsAt: date(-7), onboardingStep: 6, status: 'active', provisioningMode: 'managed', strictnessPreset: 'balanced', pacePreset: 'natural', voiceQualityPreset: 'premium', recordCalls: true, recordBothSides: true, retentionDays: 90, endCallsAfterMinutes: 15, businessHoursStart: '09:00', businessHoursEnd: '17:00' },
    { id: 'org-harbor', name: 'Harbor Health', slug: 'harbor', accentColor: 'oklch(0.56 0.11 165)', plan: 'Starter', countryCode: '+92', defaultLanguage: 'en', timezone: 'Asia/Karachi', createdAt: date(24), trialEndsAt: date(-6), onboardingStep: 2, status: 'setup', provisioningMode: 'managed', strictnessPreset: 'strict', pacePreset: 'patient', voiceQualityPreset: 'standard', recordCalls: false, recordBothSides: false, retentionDays: 30, endCallsAfterMinutes: 10, businessHoursStart: '08:00', businessHoursEnd: '20:00' },
  ],
  profile: { id: 'user-1', fullName: 'Samira Khan', email: 'samira@northstar.edu' },
  profiles: [
    { id: 'user-1', fullName: 'Samira Khan', email: 'samira@northstar.edu' },
    { id: 'user-2', fullName: 'Omar Farooq', email: 'omar@northstar.edu' },
    { id: 'user-3', fullName: 'Layla Aziz', email: 'layla@northstar.edu' },
    { id: 'user-platform', fullName: 'Platform Operations', email: 'ops@platform.internal', platformRole: 'superadmin' },
  ],
  memberships: [
    { orgId, userId: 'user-1', role: 'owner', status: 'active' },
    { orgId, userId: 'user-2', role: 'staff', status: 'active' },
    { orgId, userId: 'user-3', role: 'staff', status: 'active' },
    { orgId: 'org-harbor', userId: 'user-1', role: 'admin', status: 'active' },
  ],
  invitations: [{ id: 'inv-1', orgId, email: 'ops@northstar.edu', role: 'staff', expiresAt: date(-4), status: 'pending' }],
  agents: [
    { id: 'agent-1', orgId, name: 'Aisha Admissions', status: 'live', personaId: 'persona-1', defaultKnowledgeBaseId: 'kb-admissions', knowledgeBaseIds: ['kb-admissions', 'kb-fees'], voiceId: 'voice-1', channelIds: ['ch-main-line', 'ch-wa-main'], createdAt: date(65) },
    { id: 'agent-2', orgId, name: 'Nora Support', status: 'paused', personaId: 'persona-2', defaultKnowledgeBaseId: 'kb-campus', knowledgeBaseIds: ['kb-campus', 'kb-admissions'], voiceId: 'voice-2', channelIds: ['ch-hostel-line', 'ch-wa-alumni'], createdAt: date(43) },
    { id: 'agent-3', orgId, name: 'Zara Outreach', status: 'draft', personaId: 'persona-3', defaultKnowledgeBaseId: 'kb-fees', knowledgeBaseIds: ['kb-fees'], voiceId: 'voice-3', channelIds: ['ch-fee-line'], createdAt: date(12) },
  ],
  personas: names.map((name, i) => ({ id: 'persona-' + (i + 1), orgId, agentName: name, gender: 'female', greeting: 'Assalam o alaikum, main ' + name + ' bol rahi hoon. Main aap ki kya madad kar sakti hoon?', roleDescription: 'Help callers with admissions and student services using the material provided.', languagePolicy: 'Reply in whatever language the caller uses, Urdu or English.', toneNotes: 'Warm, concise and clear.', forbiddenPhrases: ['I guarantee admission'], escalationRules: 'Hand fee disputes and complaints to a person.', closingBehaviour: 'Summarise next steps and thank the caller.', compiledPrompt: '' })),
  voices: names.map((_, i) => ({ id: 'voice-' + (i + 1), orgId, voiceName: i === 0 ? 'Alloy' : 'Shimmer', speed: 0.95, language: 'ur-PK', sttModel: 'gpt-4o-transcribe', noiseReduction: true, turnDetection: 'server_vad', vadThreshold: 0.5, vadSilenceMs: 600, vadPrefixPaddingMs: 300, vadEagerness: 'medium', interruptionEnabled: true, greetingDelaySeconds: 0.4 })),
  presets: defaultPresets,
  knowledgeBases, documents, chunks,
  channels: [
    { id: 'ch-main-line', orgId, type: 'sim', label: 'Main admissions line', isPrimary: true, status: 'connected', displayNumber: '+92 21 555 0142', provider: 'Infobip', lastCheckedAt: date(0.04), config: { maxCallDurationSeconds: 900, callConnectTimeoutSeconds: 30, wrapUpWarningSeconds: 30, hangupGraceSeconds: 10 } },
    { id: 'ch-fee-line', orgId, type: 'sim', label: 'Fee office line', isPrimary: false, status: 'connected', displayNumber: '+92 21 555 0188', provider: 'Infobip', lastCheckedAt: date(0.2), config: { maxCallDurationSeconds: 900, callConnectTimeoutSeconds: 30 } },
    { id: 'ch-hostel-line', orgId, type: 'sim', label: 'Hostel and transport', isPrimary: false, status: 'verifying', displayNumber: '+92 21 555 0193', provider: 'Infobip', lastCheckedAt: date(0.5), config: {} },
    { id: 'ch-wa-main', orgId, type: 'whatsapp_call', label: 'Admissions WhatsApp', isPrimary: true, status: 'connected', displayNumber: '+92 300 555 0129', provider: 'Meta', lastCheckedAt: date(0.1), config: { callingEnabled: true, callingAutoAccept: true, preAcceptCalls: false, mediaRelayEnabled: true, outboundEnabled: true, operatorCallingEnabled: true, recordTwoWay: true, recordAgentAudio: true, mediaSampleRate: 24000 } },
    { id: 'ch-wa-alumni', orgId, type: 'whatsapp_call', label: 'Alumni WhatsApp', isPrimary: false, status: 'pending', displayNumber: '+92 300 555 0174', provider: 'Meta', lastCheckedAt: date(1.2), config: { callingEnabled: false } },
    { id: 'ch-wa-msg', orgId, type: 'whatsapp_message', label: 'Admissions messages', isPrimary: true, status: 'error', displayNumber: '+92 300 555 0129', provider: 'Meta', lastCheckedAt: date(0.3), errorCode: 'E-TEMPLATE', errorDetail: 'Template namespace not verified (code 132001).', config: { inboundEnabled: false, topicMaxChars: 180, detailsMaxChars: 600 } },
    { id: 'ch-harbor-line', orgId: 'org-harbor', type: 'sim', label: 'Main line', isPrimary: true, status: 'pending', provider: 'Infobip', config: {} },
    { id: 'ch-harbor-wa', orgId: 'org-harbor', type: 'whatsapp_call', label: 'Patient WhatsApp', isPrimary: true, status: 'verifying', provider: 'Meta', config: {} },
    { id: 'ch-harbor-msg', orgId: 'org-harbor', type: 'whatsapp_message', label: 'Appointment reminders', isPrimary: true, status: 'disconnected', provider: 'Meta', config: {} },
  ],
  credentials: [{ id: 'cred-1', orgId, channelId: 'ch-main-line', keyName: 'apiKey', lastFour: '7f3a', setBy: 'Platform Operations', setAt: date(9) }],
  calls, transcripts, ragQueries,
  routingRules: [
    { id: 'rule-1', orgId, name: 'Fee office line', order: 0, enabled: true, isFallback: false, condition: { kind: 'number', value: 'ch-fee-line' }, outcome: { agentId: 'agent-1', knowledgeBaseIds: ['kb-fees', 'kb-admissions'] }, matchCount30d: 212 },
    { id: 'rule-2', orgId, name: 'Alumni WhatsApp goes to Nora', order: 1, enabled: true, isFallback: false, condition: { kind: 'number', value: 'ch-wa-alumni' }, outcome: { agentId: 'agent-2', knowledgeBaseIds: ['kb-campus'] }, matchCount30d: 148 },
    { id: 'rule-3', orgId, name: 'Out of hours to a person', order: 2, enabled: false, isFallback: false, condition: { kind: 'hours', value: 'outside' }, outcome: { assignToUserId: 'user-2' }, matchCount30d: 0 },
    { id: 'rule-fallback', orgId, name: 'Everything else', order: 99, enabled: true, isFallback: true, condition: { kind: 'channel', value: 'any' }, outcome: { agentId: 'agent-1', knowledgeBaseIds: ['kb-admissions'] }, matchCount30d: 496 },
  ],
  campaigns: [
    { id: 'camp-1', orgId, name: 'Merit list follow-up', agentId: 'agent-1', fromChannelId: 'ch-main-line', knowledgeBaseIds: ['kb-admissions', 'kb-fees'], status: 'running', total: 420, attempted: 268, connected: 173, unanswered: 95, windowStart: '10:00', windowEnd: '18:00', maxAttempts: 3, retryAfterMinutes: 240, createdAt: date(6) },
    { id: 'camp-2', orgId, name: 'Scholarship reminder', agentId: 'agent-3', fromChannelId: 'ch-fee-line', knowledgeBaseIds: ['kb-fees'], status: 'paused', total: 180, attempted: 54, connected: 31, unanswered: 23, windowStart: '11:00', windowEnd: '17:00', maxAttempts: 2, retryAfterMinutes: 1440, createdAt: date(14) },
    { id: 'camp-3', orgId, name: 'Open day invites', agentId: 'agent-1', fromChannelId: 'ch-wa-main', knowledgeBaseIds: ['kb-admissions'], status: 'draft', total: 96, attempted: 0, connected: 0, unanswered: 0, windowStart: '09:00', windowEnd: '17:00', maxAttempts: 2, retryAfterMinutes: 720, createdAt: date(1) },
  ],
  campaignContacts: Array.from({ length: 24 }, (_, i) => ({ id: 'cc-' + i, campaignId: i < 16 ? 'camp-1' : 'camp-2', number: calls[i + 40].phoneNumber, name: ['Hamza', 'Fatima', 'Bilal', 'Ayesha'][i % 4] + ' ' + ['Ali', 'Khan', 'Sheikh'][i % 3], attempts: i % 3, lastOutcome: i % 4 === 0 ? 'No answer' : 'Connected', callIds: [] })),
  unanswered,
  messages: Array.from({ length: 27 }, (_, i) => ({ id: 'msg-' + i, orgId, toNumber: calls[i].phoneNumber, templateId: i % 2 === 0 ? 'tpl-1' : undefined, body: i % 2 === 0 ? 'Your appointment is confirmed for tomorrow.' : 'Thank you for contacting Northstar University.', status: i % 9 === 0 ? 'failed' : i % 3 === 0 ? 'read' : 'delivered', sentAt: date(i / 3) })),
  templates: [
    { id: 'tpl-1', orgId, name: 'Appointment reminder', language: 'en', category: 'Utility', status: 'approved', bodyPreview: 'Your appointment is confirmed for {{1}}.', variables: ['date'] },
    { id: 'tpl-2', orgId, name: 'Application update', language: 'en', category: 'Utility', status: 'pending', bodyPreview: 'Your application {{1}} has an update.', variables: ['reference'] },
    { id: 'tpl-3', orgId, name: 'Programme promotion', language: 'en', category: 'Marketing', status: 'rejected', bodyPreview: 'Discover our new programmes.', variables: [] },
  ],
  usage: Array.from({ length: 30 }, (_, i) => ({ id: 'usage-' + i, orgId, kind: i % 5 === 0 ? 'message' : 'call_minute', quantity: i % 5 === 0 ? 33 : 28 + (i % 7) * 9, occurredAt: date(i) })),
  plans: [
    { id: 'starter', name: 'Starter', priceMonthly: 49, includedMinutes: 500, includedMessages: 300, agentLimit: 1, seatLimit: 2, storageMb: 500, features: ['Phone or WhatsApp', 'One knowledge base', 'Standard voice'] },
    { id: 'growth', name: 'Growth', priceMonthly: 149, includedMinutes: 2000, includedMessages: 2000, agentLimit: 5, seatLimit: 10, storageMb: 5000, features: ['All channels', 'Unlimited knowledge bases', 'Premium voice', 'Live monitoring', 'Staff takeover'] },
    { id: 'scale', name: 'Scale', priceMonthly: 399, includedMinutes: 10000, includedMessages: 10000, agentLimit: 25, seatLimit: 50, storageMb: 25000, features: ['Everything in Growth', 'Outbound campaigns', 'Priority support'] },
  ],
  apiKeys: [{ id: 'key-1', orgId, name: 'Reporting', lastFour: 'd18e', scopes: ['calls:read', 'usage:read'], createdBy: 'Samira Khan', lastUsedAt: date(1) }],
  webhooks: [{ id: 'hook-1', orgId, url: 'https://example.org/events', events: ['call.completed'], secretLastFour: '72ac', status: 'active' }],
  audit: [{ id: 'audit-1', orgId, actorName: 'Samira Khan', action: 'Changed answer strictness to Balanced', targetType: 'Setting', targetId: 'strictnessPreset', createdAt: date(9) }],
  guides,
  provisioning: [
    { id: 'prov-1', orgId: 'org-harbor', channelId: 'ch-harbor-line', kind: 'sim', state: 'setting_up', note: 'Number reserved with carrier, awaiting trunk assignment.', updatedAt: date(1) },
    { id: 'prov-2', orgId: 'org-harbor', channelId: 'ch-harbor-wa', kind: 'whatsapp_call', state: 'testing', note: 'Placing test calls; media connecting cleanly.', updatedAt: date(0.4) },
    { id: 'prov-3', orgId: 'org-harbor', channelId: 'ch-harbor-msg', kind: 'whatsapp_message', state: 'requested', note: '', updatedAt: date(3) },
    { id: 'prov-4', orgId, channelId: 'ch-wa-msg', kind: 'whatsapp_message', state: 'blocked', note: 'Template namespace not verified on the business account.', updatedAt: date(0.3) },
  ],
  platformAudit: [
    { id: 'pa-1', actorEmail: 'ops@platform.internal', action: 'Assigned number', orgId: 'org-harbor', detail: 'Reserved +92 42 111 000 222 for Harbor Health.', createdAt: date(1) },
    { id: 'pa-2', actorEmail: 'ops@platform.internal', action: 'Replaced credential', orgId, detail: 'Rotated phone channel API key.', createdAt: date(9) },
  ],
  settings: {
    'org-northstar': { llmModel: 'gpt-4.1', llmTemperature: 0.4, llmMaxTokens: 1024, summaryEnabled: true, summaryModel: 'gpt-4.1-mini', recordingType: 'two_way', recordFromMediaStream: true, recordBrowserClientSide: false, recordingGraceSeconds: 5, recordingTailWaitSeconds: 10, transcodeRecordings: true, transcodeTimeoutSeconds: 120, recordingFilePrefix: 'call-', transcriptStoreEnabled: true, archiveTarget: '', signedUrlLifetimeMinutes: 15, driveSyncEnabled: false, driveSyncDelaySeconds: 30, spreadsheetExportFilename: 'calls' },
    'org-harbor': {},
  },
};
