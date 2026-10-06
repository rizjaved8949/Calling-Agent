import type { Store, Call, TranscriptEntry, RagQuery, Guide } from '../types';

const now = Date.now();
const date = (days = 0) => new Date(now - days * 86400000).toISOString();
const orgId = 'org-northstar';
const kbId = 'kb-main';
const names = ['Aisha', 'Nora', 'Zara'];
const outcomes = ['Resolved', 'Appointment booked', 'Escalated', 'No answer', 'Follow-up needed'];
const questions = ['What are your admission requirements?', 'Can I change my appointment?', 'What is the pricing?', 'Is weekend support available?'];
const answers = ['I can help with that. Let me check the latest information.', 'Your request is covered in the knowledge base.', 'I can arrange a follow-up with our team.'];
const docs = [
  ['Admissions handbook.pdf', 'Admissions', 42, 116],
  ['Tuition and fees 2026.pdf', 'Fees', 18, 56],
  ['Scholarships.pdf', 'Financial aid', 12, 34],
  ['Campus visits.docx', 'Visits', 9, 22],
  ['Student support.md', 'Support', 4, 16],
  ['Academic calendar.pdf', 'Calendar', 8, 25],
  ['FAQ.txt', 'Frequently asked questions', 3, 14],
  ['Legacy policy.pdf', 'Policy', 0, 0],
] as const;
const calls: Call[] = Array.from({ length: 400 }, (_, i) => {
  const age = (i * 89 / 399) + (i % 3) / 24;
  const start = date(age);
  const duration = 44 + ((i * 73) % 620);
  const state = i % 43 === 0 ? 'NONE' : i % 31 === 0 ? 'PENDING' : 'READY';
  return {
    id: 'call-' + String(i + 1).padStart(4, '0'), orgId, agentId: 'agent-' + (i % 3 + 1),
    channelType: i % 3 === 0 ? 'sim' : 'whatsapp_call',
    direction: i % 5 === 0 ? 'OUTBOUND' : 'INBOUND',
    phoneNumber: '+92 3' + String(100000000 + ((i * 7919) % 899999999)).slice(0, 9),
    fromNumber: '+92 300 123 4567', toNumber: '+92 21 555 0142',
    status: i === 0 ? 'active' : i % 23 === 0 ? 'failed' : 'completed',
    mode: i % 11 === 0 ? 'operator' : 'agent', startedAt: start, answeredAt: start,
    endedAt: i === 0 ? undefined : new Date(new Date(start).getTime() + duration * 1000).toISOString(),
    durationSeconds: duration, outcome: outcomes[i % outcomes.length],
    summary: 'Caller asked about ' + questions[i % questions.length].toLowerCase() + ' The agent provided a grounded answer and confirmed next steps.',
    recordingState: i === 0 ? 'RECORDING' : state, recordingUrl: state === 'READY' && i !== 0 ? '/demo-call.wav' : undefined, recordingScope: state === 'NONE' ? 'none' : i % 7 === 0 ? 'caller_only' : 'two_way',
    lastEvent: i === 0 ? 'rag.query' : 'call.updated'
  };
});
const transcripts: TranscriptEntry[] = calls.slice(0, 25).flatMap((call, i) => [
  { id: call.id + '-t1', callId: call.id, role: 'agent', speaker: names[i % 3], text: 'Hello, thank you for calling. How can I help you today?', timestamp: call.startedAt },
  { id: call.id + '-t2', callId: call.id, role: 'caller', speaker: 'Caller', text: questions[i % questions.length], timestamp: new Date(new Date(call.startedAt).getTime()+5000).toISOString() },
  { id: call.id + '-t3', callId: call.id, role: 'agent', speaker: names[i % 3], text: answers[i % answers.length], timestamp: new Date(new Date(call.startedAt).getTime()+12000).toISOString() },
]);
const ragQueries: RagQuery[] = calls.slice(0, 25).map((call, i) => ({
  id: call.id + '-rag', callId: call.id, query: questions[i % questions.length],
  topScore: i % 5 === 0 ? 0.55 : 0.76 + (i % 10) / 100, threshold: 0.6,
  hitCount: i % 5 === 0 ? 0 : 3, answered: i % 5 !== 0, sections: ['Admissions', 'FAQ'],
  timestamp: call.startedAt
}));
const guideInfo = [
  ['getting-started', 'Getting started in 30 minutes', 'Start here', 'Create your organisation, name your agent, upload the material it can answer from, then connect a channel. Keep all calling capabilities off until the readiness checklist is green.'],
  ['whatsapp-credentials', 'Get WhatsApp Business Calling credentials', 'Channels', 'In Meta Business Manager, create a business app, add WhatsApp, and copy the app ID, app secret, business account ID and phone number ID. Use a long-lived access token. The app secret verifies every webhook signature.'],
  ['verify-number', 'Register and verify your WhatsApp number', 'Channels', 'Add a business phone number in E.164 format. Complete the provider verification flow before attempting a test call.'],
  ['phone-sip', 'Get a phone number and SIP trunk', 'Channels', 'Create an application with your telephony provider. Assign a number and SIP trunk, then enter the application ID and calls configuration ID in VoxOps.'],
  ['webhooks', 'Point webhooks at VoxOps', 'Channels', 'Copy the webhook URL from your channel page into the provider dashboard. Use the verify token you chose. Confirm the provider reports a successful challenge.'],
  ['knowledge-writing', 'Write a knowledge base an agent can answer from', 'Knowledge', 'Use clear headings, one topic per section, and current facts. Remove conflicting or outdated policy. Upload PDF, DOCX, TXT or MD files, then test the questions callers actually ask.'],
  ['threshold', 'Tune the similarity threshold', 'Knowledge', 'Start at 0.60. Genuine matches usually score 0.70 or more. Raise the threshold if the agent answers unrelated questions; lower it if relevant results are being rejected.'],
  ['persona', 'Write a natural phone persona', 'Agents', 'Keep the greeting short and easy to hear. State the agent role, when it should escalate, and phrases it must avoid. Listen to a test call before going live.'],
  ['templates', 'Get message templates approved', 'Messages', 'Submit templates in the provider dashboard with a clear purpose and example variables. Until a template is approved, the messaging tool remains unavailable to the agent.'],
  ['recording', 'Recording, retention and consent', 'Settings', 'Decide whether to record both sides and how long to keep recordings. Provide consent language that fits your organisation and local requirements before enabling recording.'],
  ['troubleshooting', 'Troubleshoot channel errors', 'Support', 'Check the exact provider error on the channel card. Confirm credentials, number verification, webhook token and media network settings. TURN is often needed behind strict NAT.'],
  ['go-live', 'Pre-launch checklist', 'Start here', 'Confirm the persona, knowledge base, channel credentials and capability flags. Run test search and a test call, inspect retrieval events, then enable the agent.']
] as const;
const guides: Guide[] = guideInfo.map(([slug,title,category,bodyMd]) => ({ slug,title,category,bodyMd,readingMinutes:3 + (slug.length % 4),updatedAt:date(8) }));
export const initialStore: Store = {
  organizations: [
    { id:orgId,name:'Northstar University',slug:'northstar',accentColor:'oklch(0.58 0.13 245)',plan:'Growth',countryCode:'+92',defaultLanguage:'en',timezone:'Asia/Karachi',createdAt:date(85),trialEndsAt:date(-7),onboardingStep:6 },
    { id:'org-harbor',name:'Harbor Health',slug:'harbor',accentColor:'oklch(0.56 0.11 165)',plan:'Starter',countryCode:'+92',defaultLanguage:'en',timezone:'Asia/Karachi',createdAt:date(24),trialEndsAt:date(-6),onboardingStep:2 }
  ],
  profile:{ id:'user-1',fullName:'Samira Khan',email:'samira@example.com' },
  profiles:[{id:'user-1',fullName:'Samira Khan',email:'samira@example.com'},{id:'user-2',fullName:'Omar Farooq',email:'omar@northstar.edu'},{id:'user-3',fullName:'Layla Aziz',email:'layla@northstar.edu'}],
  memberships:[{orgId,userId:'user-1',role:'owner',status:'active'},{orgId,userId:'user-2',role:'operator',status:'active'},{orgId,userId:'user-3',role:'viewer',status:'active'},{orgId:'org-harbor',userId:'user-1',role:'admin',status:'active'}],
  invitations:[{id:'inv-1',orgId,email:'ops@northstar.edu',role:'operator',expiresAt:date(-4),status:'pending'}],
  agents:[
    {id:'agent-1',orgId,name:'Aisha Admissions',status:'live',personaId:'persona-1',knowledgeBaseId:kbId,voiceId:'voice-1',channels:['sim','whatsapp_call'],createdAt:date(65)},
    {id:'agent-2',orgId,name:'Nora Support',status:'paused',personaId:'persona-2',knowledgeBaseId:kbId,voiceId:'voice-2',channels:['whatsapp_call'],createdAt:date(43)},
    {id:'agent-3',orgId,name:'Zara Outreach',status:'draft',personaId:'persona-3',knowledgeBaseId:kbId,voiceId:'voice-3',channels:['sim'],createdAt:date(12)}
  ],
  personas:names.map((name,i)=>({id:'persona-'+(i+1),orgId,agentName:name,gender:'female',greeting:'Hello, this is '+name+'. How may I help?',roleDescription:'Help callers with admissions and student services using approved knowledge.',languagePolicy:'Reply clearly in English and follow the approved knowledge.',toneNotes:'Warm, concise and clear.',forbiddenPhrases:['I guarantee admission'],escalationRules:'Escalate financial disputes and complaints to a human operator.',closingBehaviour:'Summarise next steps and thank the caller.',compiledPrompt:''})),
  voices:names.map((_,i)=>({id:'voice-'+(i+1),orgId,voiceName:i===0?'Alloy':'Shimmer',speed:0.95,language:'en-US',sttModel:'whisper-1',noiseReduction:true,turnDetection:'server_vad',vadThreshold:0.5,vadSilenceMs:550,vadPrefixPaddingMs:300,vadEagerness:'auto',interruptionEnabled:true,greetingDelaySeconds:0.4})),
  knowledgeBases:[{id:kbId,orgId,name:'Northstar knowledge',status:'ready',chunkSize:800,chunkOverlap:120,embeddingModel:'text-embedding-3-small',similarityThreshold:0.6,topK:5,maxChunksPerSection:2,maxContextChars:8000,chunkCount:283,lastIndexedAt:date(2)}],
  documents:docs.map(([filename,section,pages,chunks],i)=>({id:'doc-'+(i+1),kbId,filename,mime:filename.endsWith('.pdf')?'application/pdf':'text/plain',bytes:(i+2)*143000,pages,status:i===7?'failed':'ready',uploadedBy:'Samira Khan',chunkCount:chunks,error:i===7?'Encrypted PDF: password required to extract text.':undefined})),
  chunks:docs.slice(0,7).flatMap(([filename,section],i)=>Array.from({length:2},(_,j)=>({id:'chunk-'+i+'-'+j,kbId,documentId:'doc-'+(i+1),section,title:filename,page:j+1,category:section,preview:'Current information for '+section.toLowerCase()+'. The full policy explains eligibility, timing and next steps in clear terms.',score:0.83-i*0.045-j*0.025}))),
  channels:[
    {id:'channel-phone',orgId,type:'sim',status:'connected',displayNumber:'+92 21 555 0142',provider:'Infobip',lastCheckedAt:date(0.04),config:{maxCallDurationSeconds:900,callConnectTimeoutSeconds:30,wrapUpWarningSeconds:30,hangupGraceSeconds:10}},
    {id:'channel-wa-call',orgId,type:'whatsapp_call',status:'connected',displayNumber:'+92 300 555 0129',provider:'Meta',lastCheckedAt:date(0.1),config:{callingEnabled:true,callingAutoAccept:true,preAcceptCalls:false,mediaRelayEnabled:false,outboundEnabled:false,operatorCallingEnabled:false,recordTwoWay:false,recordAgentAudio:false,mediaSampleRate:24000}},
    {id:'channel-wa-msg',orgId,type:'whatsapp_message',status:'error',displayNumber:'+92 300 555 0129',provider:'Meta',lastCheckedAt:date(0.3),errorDetail:'Template namespace not verified (code 132001).',config:{inboundEnabled:false,topicMaxChars:180,detailsMaxChars:600}},
    {id:'channel-harbor-phone',orgId:'org-harbor',type:'sim',status:'disconnected',provider:'Infobip',config:{}},
    {id:'channel-harbor-call',orgId:'org-harbor',type:'whatsapp_call',status:'pending',provider:'Meta',config:{}},
    {id:'channel-harbor-message',orgId:'org-harbor',type:'whatsapp_message',status:'verifying',provider:'Meta',config:{}}
  ],
  credentials:[{id:'cred-1',orgId,channelId:'channel-phone',keyName:'apiKey',lastFour:'7f3a',setBy:'Samira Khan',setAt:date(9)}],
  calls,transcripts,ragQueries,
  messages:Array.from({length:27},(_,i)=>({id:'msg-'+i,orgId,toNumber:calls[i].phoneNumber,templateId:i%2===0?'tpl-1':undefined,body:i%2===0?'Your appointment is confirmed for tomorrow.':'Thank you for contacting Northstar University.',status:i%9===0?'failed':i%3===0?'read':'delivered',sentAt:date(i/3)})),
  templates:[
    {id:'tpl-1',orgId,name:'appointment_reminder',language:'en',category:'Utility',status:'approved',bodyPreview:'Your appointment is confirmed for {{1}}.',variables:['date']},
    {id:'tpl-2',orgId,name:'application_update',language:'en',category:'Utility',status:'pending',bodyPreview:'Your application {{1}} has an update.',variables:['reference']},
    {id:'tpl-3',orgId,name:'promotion',language:'en',category:'Marketing',status:'rejected',bodyPreview:'Discover our new programmes.',variables:[]}
  ],
  usage:Array.from({length:30},(_,i)=>({id:'usage-'+i,orgId,kind:i%5===0?'message':'call_minute',quantity:i%5===0?33:28+(i%7)*9,occurredAt:date(i)})),
  plans:[
    {id:'starter',name:'Starter',priceMonthly:49,includedMinutes:500,includedMessages:300,agentLimit:1,seatLimit:2,storageMb:500,features:['Phone or WhatsApp','Knowledge base']},
    {id:'growth',name:'Growth',priceMonthly:149,includedMinutes:2000,includedMessages:2000,agentLimit:5,seatLimit:10,storageMb:5000,features:['All channels','Live monitoring','Human takeover']},
    {id:'scale',name:'Scale',priceMonthly:399,includedMinutes:10000,includedMessages:10000,agentLimit:25,seatLimit:50,storageMb:25000,features:['Advanced controls','Priority support','Audit log']}
  ],
  apiKeys:[{id:'key-1',orgId,name:'Reporting',lastFour:'d18e',scopes:['calls:read','usage:read'],createdBy:'Samira Khan',lastUsedAt:date(1)}],
  webhooks:[{id:'hook-1',orgId,url:'https://example.org/voxops/events',events:['call.completed'],secretLastFour:'72ac',status:'active'}],
  audit:[{id:'audit-1',orgId,actorName:'Samira Khan',action:'Updated phone channel',targetType:'Channel',targetId:'channel-phone',createdAt:date(9)}],
  guides,
  settings:{'org-northstar':{realtimeModel:'gpt-realtime',realtimeMaxOutputTokens:1024,realtimeReasoningEffort:'medium',llmModel:'gpt-4.1',llmTemperature:0.4,llmMaxTokens:1024,summaryEnabled:true,summaryModel:'gpt-4.1-mini',recordCalls:false,recordingType:'two_way',recordAgentAudio:false,recordFromMediaStream:false,recordBrowserClientSide:false,recordingGraceSeconds:5,recordingTailWaitSeconds:10,transcodeRecordings:false,transcodeTimeoutSeconds:120,recordingFilePrefix:'voxops-',transcriptStoreEnabled:true,retentionDays:30,archiveTarget:'',signedUrlLifetimeMinutes:15,driveClientId:'',driveFolderId:'',driveSyncEnabled:false,driveSyncDelaySeconds:30,spreadsheetExportFilename:'voxops-calls'},'org-harbor':{}}
};
