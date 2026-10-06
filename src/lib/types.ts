export type Role = 'owner' | 'admin' | 'operator' | 'viewer';
export type ChannelType = 'sim' | 'whatsapp_call' | 'whatsapp_message';
export type ChannelStatus = 'disconnected' | 'pending' | 'verifying' | 'connected' | 'error';
export type RecordingState = 'RECORDING' | 'PENDING' | 'READY' | 'NONE';
export interface Organization { id:string; name:string; slug:string; logoUrl?:string; accentColor:string; plan:string; countryCode:string; defaultLanguage:'en'|'es'|'fr'; timezone:string; createdAt:string; trialEndsAt:string; onboardingStep:number; operatorPhone?:string; logLevel?:string }
export interface Profile { id:string; fullName:string; email:string; avatarUrl?:string }
export interface Membership { orgId:string; userId:string; role:Role; status:'active'|'pending' }
export interface Invitation { id:string; orgId:string; email:string; role:Role; expiresAt:string; status:'pending'|'accepted'|'revoked' }
export interface Agent { id:string; orgId:string; name:string; avatarUrl?:string; status:'draft'|'live'|'paused'; personaId:string; knowledgeBaseId:string; voiceId:string; channels:ChannelType[]; createdAt:string }
export interface Persona { id:string; orgId:string; agentName:string; gender:string; greeting:string; roleDescription:string; languagePolicy:string; toneNotes:string; forbiddenPhrases:string[]; escalationRules:string; closingBehaviour:string; compiledPrompt:string; promptOverride?:string }
export interface Voice { id:string; orgId:string; voiceName:string; speed:number; language:string; sttModel:string; noiseReduction:boolean; turnDetection:string; vadThreshold:number; vadSilenceMs:number; vadPrefixPaddingMs:number; vadEagerness:string; interruptionEnabled:boolean; greetingDelaySeconds:number }
export interface KnowledgeBase { id:string; orgId:string; name:string; status:'empty'|'indexing'|'ready'|'failed'; chunkSize:number; chunkOverlap:number; embeddingModel:string; similarityThreshold:number; topK:number; maxChunksPerSection:number; maxContextChars:number; chunkCount:number; lastIndexedAt?:string; indexError?:string }
export interface KbDocument { id:string; kbId:string; filename:string; mime:string; bytes:number; pages:number; status:'ready'|'indexing'|'failed'; uploadedBy:string; chunkCount:number; error?:string }
export interface KbChunk { id:string; kbId:string; documentId:string; section:string; title:string; page:number; category:string; preview:string; score:number }
export interface Channel { id:string; orgId:string; type:ChannelType; status:ChannelStatus; displayNumber?:string; provider:string; lastCheckedAt?:string; errorDetail?:string; config:Record<string,string|number|boolean> }
export interface Credential { id:string; orgId:string; channelId:string; keyName:string; lastFour:string; setBy:string; setAt:string }
export interface Call { id:string; orgId:string; agentId:string; channelType:ChannelType; direction:'INBOUND'|'OUTBOUND'; phoneNumber:string; fromNumber:string; toNumber:string; status:'active'|'completed'|'failed'; mode:'agent'|'operator'; startedAt:string; answeredAt?:string; endedAt?:string; durationSeconds:number; outcome:string; summary:string; recordingState:RecordingState; recordingScope:'two_way'|'caller_only'|'agent_only'|'none'; recordingUrl?:string; operatorNumber?:string; lastEvent?:string; error?:string }
export interface TranscriptEntry { id:string; callId:string; role:'agent'|'caller'|'operator'; speaker:string; text:string; timestamp:string }
export interface RagQuery { id:string; callId:string; query:string; topScore:number; threshold:number; hitCount:number; answered:boolean; sections:string[]; timestamp:string }
export interface Message { id:string; orgId:string; callId?:string; toNumber:string; templateId?:string; body:string; status:'sent'|'delivered'|'read'|'failed'; sentAt:string }
export interface WaTemplate { id:string; orgId:string; name:string; language:string; category:string; status:'approved'|'pending'|'rejected'; bodyPreview:string; variables:string[] }
export interface UsageEvent { id:string; orgId:string; kind:'call_minute'|'message'|'storage'; quantity:number; occurredAt:string }
export interface Plan { id:string; name:string; priceMonthly:number; includedMinutes:number; includedMessages:number; agentLimit:number; seatLimit:number; storageMb:number; features:string[] }
export interface ApiKeyMeta { id:string; orgId:string; name:string; lastFour:string; scopes:string[]; createdBy:string; lastUsedAt?:string }
export interface WebhookEndpoint { id:string; orgId:string; url:string; events:string[]; secretLastFour:string; status:'active'|'paused' }
export interface AuditEntry { id:string; orgId:string; actorName:string; action:string; targetType:string; targetId:string; createdAt:string }
export interface Guide { slug:string; title:string; category:string; channel?:ChannelType; bodyMd:string; readingMinutes:number; updatedAt:string }
export interface Session { email:string; name:string; userId:string; orgId:string }
export interface Store { organizations:Organization[]; profile:Profile; profiles:Profile[]; memberships:Membership[]; invitations:Invitation[]; agents:Agent[]; personas:Persona[]; voices:Voice[]; knowledgeBases:KnowledgeBase[]; documents:KbDocument[]; chunks:KbChunk[]; channels:Channel[]; credentials:Credential[]; calls:Call[]; transcripts:TranscriptEntry[]; ragQueries:RagQuery[]; messages:Message[]; templates:WaTemplate[]; usage:UsageEvent[]; plans:Plan[]; apiKeys:ApiKeyMeta[]; webhooks:WebhookEndpoint[]; audit:AuditEntry[]; guides:Guide[]; settings:Record<string,Record<string,string|number|boolean>> }

