/** Which door a session came in by. Platform is ours; company is the customer's. */
export type Portal = 'platform' | 'company';
export type PlatformRole = 'superadmin' | 'support';
/** Company roles. Staff replaced the earlier operator and viewer roles. */
export type Role = 'owner' | 'admin' | 'staff';
export type ChannelType = 'sim' | 'whatsapp_call' | 'whatsapp_message';
export type ChannelStatus = 'disconnected' | 'pending' | 'verifying' | 'connected' | 'error';
export type RecordingState = 'RECORDING' | 'PENDING' | 'READY' | 'NONE';
export type Language = 'en' | 'ur' | 'es' | 'fr';
/** How a company's channels were set up. Managed means we did it for them. */
export type ProvisioningMode = 'managed' | 'connected';
export type PresetKind = 'strictness' | 'pace' | 'voiceQuality';
export interface Organization { id:string; name:string; slug:string; logoUrl?:string; accentColor:string; plan:string; countryCode:string; defaultLanguage:Language; timezone:string; createdAt:string; trialEndsAt:string; onboardingStep:number; operatorPhone?:string; logLevel?:string; status:'setup'|'active'|'suspended'; provisioningMode:ProvisioningMode; strictnessPreset:string; pacePreset:string; voiceQualityPreset:string; recordCalls:boolean; recordBothSides:boolean; retentionDays:number; endCallsAfterMinutes:number; businessHoursStart:string; businessHoursEnd:string }
export interface Profile { id:string; fullName:string; email:string; avatarUrl?:string; platformRole?:PlatformRole }
export interface Membership { orgId:string; userId:string; role:Role; status:'active'|'pending' }
export interface Invitation { id:string; orgId:string; email:string; role:Role; expiresAt:string; status:'pending'|'accepted'|'revoked' }
export interface Agent { id:string; orgId:string; name:string; avatarUrl?:string; status:'draft'|'live'|'paused'; personaId:string; defaultKnowledgeBaseId:string; knowledgeBaseIds:string[]; voiceId:string; channelIds:string[]; createdAt:string }
export interface Persona { id:string; orgId:string; agentName:string; gender:string; greeting:string; roleDescription:string; languagePolicy:string; toneNotes:string; forbiddenPhrases:string[]; escalationRules:string; closingBehaviour:string; compiledPrompt:string; promptOverride?:string }
/** Technical voice values. Company users never see these; presets drive them. */
export interface Voice { id:string; orgId:string; voiceName:string; speed:number; language:string; sttModel:string; noiseReduction:boolean; turnDetection:string; vadThreshold:number; vadSilenceMs:number; vadPrefixPaddingMs:number; vadEagerness:string; interruptionEnabled:boolean; greetingDelaySeconds:number }
/** A named choice. `label` and `description` are company-facing; `values` never are. */
export interface Preset { id:string; kind:PresetKind; label:string; description:string; consequence:string; values:Record<string,number|string>; isDefault?:boolean }
export interface KnowledgeBase { id:string; orgId:string; name:string; purpose:string; isDefault:boolean; status:'empty'|'indexing'|'ready'|'failed'; chunkSize:number; chunkOverlap:number; embeddingModel:string; similarityThreshold:number; topK:number; maxChunksPerSection:number; maxContextChars:number; chunkCount:number; lastIndexedAt?:string; indexError?:string; answeredLast30d:number }
export interface KbDocument { id:string; kbId:string; filename:string; mime:string; bytes:number; pages:number; status:'ready'|'indexing'|'failed'; uploadedBy:string; chunkCount:number; error?:string }
export interface KbChunk { id:string; kbId:string; documentId:string; section:string; title:string; page:number; category:string; preview:string; score:number }
/** One number. A company may hold several of each kind, each with its own purpose. */
export interface Channel { id:string; orgId:string; type:ChannelType; label:string; isPrimary:boolean; status:ChannelStatus; displayNumber?:string; provider:string; lastCheckedAt?:string; errorDetail?:string; errorCode?:string; config:Record<string,string|number|boolean> }
export interface Credential { id:string; orgId:string; channelId:string; keyName:string; lastFour:string; setBy:string; setAt:string }
/** Which rule or default decided the knowledge base a call used. */
export type KnowledgeResolution = 'explicit' | 'rule' | 'agent' | 'company';
export interface Call { id:string; orgId:string; agentId:string; channelId:string; channelType:ChannelType; direction:'INBOUND'|'OUTBOUND'; phoneNumber:string; fromNumber:string; toNumber:string; status:'active'|'completed'|'failed'; mode:'agent'|'operator'; startedAt:string; answeredAt?:string; endedAt?:string; durationSeconds:number; outcome:string; summary:string; recordingState:RecordingState; recordingScope:'two_way'|'caller_only'|'agent_only'|'none'; recordingUrl?:string; operatorNumber?:string; lastEvent?:string; error?:string; errorCode?:string; knowledgeBaseId:string; knowledgeBaseName:string; resolvedBy:KnowledgeResolution; campaignId?:string; assignedToUserId?:string }
export interface TranscriptEntry { id:string; callId:string; role:'agent'|'caller'|'operator'; speaker:string; text:string; timestamp:string }
/** Retrieval telemetry. Scores are platform-only; companies see the plain version. */
export interface RagQuery { id:string; callId:string; query:string; topScore:number; threshold:number; hitCount:number; answered:boolean; sections:string[]; documentName?:string; page?:number; timestamp:string }
/** A rule deciding the agent and knowledge base for an inbound call. */
export type RuleConditionKind = 'number' | 'channel' | 'prefix' | 'contactList' | 'hours';
export interface RoutingRule { id:string; orgId:string; name:string; order:number; enabled:boolean; isFallback:boolean; condition:{ kind:RuleConditionKind; value:string }; outcome:{ agentId?:string; knowledgeBaseIds?:string[]; assignToUserId?:string; playMessage?:string }; matchCount30d:number }
export interface Campaign { id:string; orgId:string; name:string; agentId:string; fromChannelId:string; knowledgeBaseIds:string[]; status:'draft'|'running'|'paused'|'done'; total:number; attempted:number; connected:number; unanswered:number; windowStart:string; windowEnd:string; maxAttempts:number; retryAfterMinutes:number; createdAt:string }
export interface CampaignContact { id:string; campaignId:string; number:string; name?:string; attempts:number; lastOutcome?:string; callIds:string[] }
/** A question the agent declined, so the company can close the gap. */
export interface UnansweredQuestion { id:string; orgId:string; question:string; askedCount:number; lastAskedAt:string; knowledgeBaseId:string; exampleCallIds:string[]; status:'open'|'answered'|'ignored' }
export interface Message { id:string; orgId:string; callId?:string; toNumber:string; templateId?:string; body:string; status:'sent'|'delivered'|'read'|'failed'; sentAt:string }
export interface WaTemplate { id:string; orgId:string; name:string; language:string; category:string; status:'approved'|'pending'|'rejected'; bodyPreview:string; variables:string[] }
export interface UsageEvent { id:string; orgId:string; kind:'call_minute'|'message'|'storage'; quantity:number; occurredAt:string }
export interface Plan { id:string; name:string; priceMonthly:number; includedMinutes:number; includedMessages:number; agentLimit:number; seatLimit:number; storageMb:number; features:string[] }
export interface ApiKeyMeta { id:string; orgId:string; name:string; lastFour:string; scopes:string[]; createdBy:string; lastUsedAt?:string }
export interface WebhookEndpoint { id:string; orgId:string; url:string; events:string[]; secretLastFour:string; status:'active'|'paused' }
export interface AuditEntry { id:string; orgId:string; actorName:string; action:string; targetType:string; targetId:string; createdAt:string }
export interface Guide { slug:string; title:string; category:string; channel?:ChannelType; bodyMd:string; readingMinutes:number; updatedAt:string }
/** Platform-side records. Never reachable from a company session. */
export interface ProvisioningTask { id:string; orgId:string; channelId:string; kind:ChannelType; state:'requested'|'setting_up'|'testing'|'active'|'blocked'; note:string; updatedAt:string }
export interface PlatformAudit { id:string; actorEmail:string; action:string; orgId?:string; detail:string; createdAt:string }
export interface Session { email:string; name:string; userId:string; orgId:string; portal:Portal; platformRole?:PlatformRole; impersonating?:{ orgId:string; startedAt:string } }
export interface Store { organizations:Organization[]; profile:Profile; profiles:Profile[]; memberships:Membership[]; invitations:Invitation[]; agents:Agent[]; personas:Persona[]; voices:Voice[]; presets:Preset[]; knowledgeBases:KnowledgeBase[]; documents:KbDocument[]; chunks:KbChunk[]; channels:Channel[]; credentials:Credential[]; calls:Call[]; transcripts:TranscriptEntry[]; ragQueries:RagQuery[]; routingRules:RoutingRule[]; campaigns:Campaign[]; campaignContacts:CampaignContact[]; unanswered:UnansweredQuestion[]; messages:Message[]; templates:WaTemplate[]; usage:UsageEvent[]; plans:Plan[]; apiKeys:ApiKeyMeta[]; webhooks:WebhookEndpoint[]; audit:AuditEntry[]; guides:Guide[]; provisioning:ProvisioningTask[]; platformAudit:PlatformAudit[]; settings:Record<string,Record<string,string|number|boolean>> }
