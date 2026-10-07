import { initialStore } from '../fixtures';
import type { Store, Organization, Agent, Persona, Voice, KnowledgeBase, KbDocument, Channel, Call, Message, Invitation, Role, WebhookEndpoint, Guide, RoutingRule, Campaign, ChannelType, ProvisioningTask, Preset, KnowledgeResolution } from '../types';

export const USE_MOCK = true;
let store: Store = structuredClone(initialStore);
const listeners = new Set<() => void>();
let pending = 0;
const pendingListeners = new Set<(n:number)=>void>();
const notify = () => listeners.forEach(fn => fn());
const notifyPending = () => pendingListeners.forEach(fn => fn(pending));
export const subscribe = (fn:()=>void) => { listeners.add(fn); return () => {listeners.delete(fn);}; };
export const subscribePending = (fn:(n:number)=>void) => { pendingListeners.add(fn); fn(pending); return () => {pendingListeners.delete(fn);}; };
const clone = <T,>(value:T):T => structuredClone(value);
async function mock<T>(work:()=>T, failureRate=0.01):Promise<T> {
  if (!USE_MOCK) throw new Error('not implemented');
  pending++; notifyPending();
  try {
    await new Promise(resolve => setTimeout(resolve, 150 + Math.round(Math.random()*450)));
    if (Math.random() < failureRate) throw new Error('The connection dropped. Please try again.');
    return clone(work());
  } finally { pending--; notifyPending(); }
}
const update = <T,>(work:()=>T) => mock(() => { const result=work(); notify(); return result; },0);
const id = () => Math.random().toString(36).slice(2,10);
const logAudit = (orgId:string, action:string, targetType:string, targetId:string) => {
  store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action,targetType,targetId,createdAt:new Date().toISOString()});
};
const logPlatform = (action:string, detail:string, orgId?:string) => {
  store.platformAudit.unshift({id:id(),actorEmail:store.profile.email,action,orgId,detail,createdAt:new Date().toISOString()});
};

/**
 * Resolve which knowledge base a call should use. First match wins:
 * an explicit choice, then a routing rule, then the agent's default, then the
 * company default. The reason is recorded so call detail can explain itself.
 */
function resolveKnowledge(orgId:string, agentId:string, explicitKbId?:string, channelType?:ChannelType, toNumber?:string):{kbId:string;kbName:string;resolvedBy:KnowledgeResolution} {
  const named = (kbId:string, resolvedBy:KnowledgeResolution) => ({kbId,kbName:store.knowledgeBases.find(x=>x.id===kbId)?.name||'Default',resolvedBy});
  if (explicitKbId) return named(explicitKbId,'explicit');
  const rules = store.routingRules.filter(x=>x.orgId===orgId&&x.enabled&&!x.isFallback).sort((a,b)=>a.order-b.order);
  for (const rule of rules) {
    const c = rule.condition;
    const hit = (c.kind==='number'&&toNumber===c.value) || (c.kind==='channel'&&channelType===c.value) || (c.kind==='prefix'&&!!toNumber&&toNumber.startsWith(c.value));
    if (hit && rule.outcome.knowledgeBaseIds?.length) return named(rule.outcome.knowledgeBaseIds[0],'rule');
  }
  const agent = store.agents.find(x=>x.id===agentId);
  if (agent?.defaultKnowledgeBaseId) return named(agent.defaultKnowledgeBaseId,'agent');
  const fallback = store.knowledgeBases.find(x=>x.orgId===orgId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===orgId);
  return named(fallback?.id||'','company');
}

export const api = {
  // ---- Organisations and people ------------------------------------------
  getOrganizations: () => mock(()=>store.organizations),
  getOrganization: (orgId:string) => mock(()=>store.organizations.find(x=>x.id===orgId)),
  updateOrganization: (orgId:string, patch:Partial<Organization>) => update(()=> { const row=store.organizations.find(x=>x.id===orgId)!; Object.assign(row,patch); return row; }),
  getProfile: () => mock(()=>store.profile),
  setProfile: (userId:string) => update(()=> { const row=store.profiles.find(x=>x.id===userId); if(row) store.profile=row; return store.profile; }),
  getMemberships: (orgId:string) => mock(()=>store.memberships.filter(x=>x.orgId===orgId)),
  getInvitations: (orgId:string) => mock(()=>store.invitations.filter(x=>x.orgId===orgId)),
  invite: (orgId:string,email:string,role:Role) => update(()=> { const row:Invitation={id:id(),orgId,email,role,expiresAt:new Date(Date.now()+7*86400000).toISOString(),status:'pending'}; store.invitations.unshift(row); logAudit(orgId,'Invited '+email,'Invitation',row.id); return row; }),
  revokeInvitation: (invId:string) => update(()=> { const row=store.invitations.find(x=>x.id===invId)!; row.status='revoked'; return row; }),
  resendInvitation: (invId:string) => update(()=> { const row=store.invitations.find(x=>x.id===invId)!; row.expiresAt=new Date(Date.now()+7*86400000).toISOString(); return row; }),
  changeMemberRole: (orgId:string,userId:string,role:Role) => update(()=> { const row=store.memberships.find(x=>x.orgId===orgId&&x.userId===userId)!; row.role=role; logAudit(orgId,'Changed a team member to '+role,'Membership',userId); return row; }),
  removeMember: (orgId:string,userId:string) => update(()=> { store.memberships=store.memberships.filter(x=>!(x.orgId===orgId&&x.userId===userId)); logAudit(orgId,'Removed a team member','Membership',userId); return true; }),

  // ---- Agents -------------------------------------------------------------
  getAgents: (orgId:string) => mock(()=>store.agents.filter(x=>x.orgId===orgId)),
  getAgent: (agentId:string) => mock(()=>store.agents.find(x=>x.id===agentId)),
  createAgent: (orgId:string,name:string) => update(()=> {
    const defaultKb=store.knowledgeBases.find(x=>x.orgId===orgId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===orgId);
    const row:Agent={id:'agent-'+id(),orgId,name,status:'draft',personaId:'persona-'+id(),defaultKnowledgeBaseId:defaultKb?.id||'',knowledgeBaseIds:defaultKb?[defaultKb.id]:[],voiceId:'voice-'+id(),channels:[],createdAt:new Date().toISOString()};
    store.agents.push(row);
    store.personas.push({id:row.personaId,orgId,agentName:name,gender:'neutral',greeting:'Hello, how can I help?',roleDescription:'Help callers with accurate information.',languagePolicy:'Match the caller language.',toneNotes:'Clear and friendly.',forbiddenPhrases:[],escalationRules:'Hand complex requests to a person.',closingBehaviour:'Confirm next steps.',compiledPrompt:''});
    store.voices.push({id:row.voiceId,orgId,voiceName:'Alloy',speed:0.95,language:'ur-PK',sttModel:'gpt-4o-transcribe',noiseReduction:true,turnDetection:'server_vad',vadThreshold:0.5,vadSilenceMs:600,vadPrefixPaddingMs:300,vadEagerness:'medium',interruptionEnabled:true,greetingDelaySeconds:0.4});
    logAudit(orgId,'Created the agent '+name,'Agent',row.id); return row; }),
  updateAgent: (agentId:string,patch:Partial<Agent>) => update(()=> { const row=store.agents.find(x=>x.id===agentId)!; Object.assign(row,patch); return row; }),
  getPersona: (id:string) => mock(()=>store.personas.find(x=>x.id===id)),
  updatePersona: (id:string,patch:Partial<Persona>) => update(()=> { const row=store.personas.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),
  getVoice: (id:string) => mock(()=>store.voices.find(x=>x.id===id)),
  updateVoice: (id:string,patch:Partial<Voice>) => update(()=> { const row=store.voices.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),

  // ---- Presets: the plain-language layer over technical values -------------
  getPresets: () => mock(()=>store.presets),
  savePreset: (presetId:string,patch:Partial<Preset>) => update(()=> { const row=store.presets.find(x=>x.id===presetId)!; Object.assign(row,patch); logPlatform('Edited preset',row.label+' values changed'); return row; }),
  countCompaniesOnPreset: (presetId:string) => mock(()=>store.organizations.filter(o=>o.strictnessPreset===presetId||o.pacePreset===presetId||o.voiceQualityPreset===presetId).length),

  // ---- Knowledge: many bases per company ----------------------------------
  getKnowledgeBases: (orgId:string) => mock(()=>store.knowledgeBases.filter(x=>x.orgId===orgId)),
  getKnowledgeBase: (kbId:string) => mock(()=>store.knowledgeBases.find(x=>x.id===kbId)),
  createKnowledgeBase: (orgId:string,name:string,purpose:string) => update(()=> {
    const row:KnowledgeBase={id:'kb-'+id(),orgId,name,purpose,isDefault:!store.knowledgeBases.some(x=>x.orgId===orgId),status:'empty',chunkSize:800,chunkOverlap:120,embeddingModel:'text-embedding-3-small',similarityThreshold:0.6,topK:6,maxChunksPerSection:2,maxContextChars:9000,chunkCount:0,answeredLast30d:0};
    store.knowledgeBases.push(row); logAudit(orgId,'Created the knowledge base '+name,'KnowledgeBase',row.id); return row; }),
  updateKnowledgeBase: (kbId:string,patch:Partial<KnowledgeBase>) => update(()=> { const row=store.knowledgeBases.find(x=>x.id===kbId)!; Object.assign(row,patch); return row; }),
  setDefaultKnowledgeBase: (orgId:string,kbId:string) => update(()=> { store.knowledgeBases.filter(x=>x.orgId===orgId).forEach(x=>{x.isDefault=x.id===kbId}); return true; }),
  deleteKnowledgeBase: (kbId:string) => update(()=> {
    const row=store.knowledgeBases.find(x=>x.id===kbId); if(!row) return false;
    const fallback=store.knowledgeBases.find(x=>x.orgId===row.orgId&&x.id!==kbId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===row.orgId&&x.id!==kbId);
    store.agents.filter(a=>a.orgId===row.orgId).forEach(a=>{ if(a.defaultKnowledgeBaseId===kbId)a.defaultKnowledgeBaseId=fallback?.id||''; a.knowledgeBaseIds=a.knowledgeBaseIds.filter(x=>x!==kbId); });
    store.routingRules.filter(r=>r.orgId===row.orgId).forEach(r=>{ if(r.outcome.knowledgeBaseIds) r.outcome.knowledgeBaseIds=r.outcome.knowledgeBaseIds.filter(x=>x!==kbId); });
    store.knowledgeBases=store.knowledgeBases.filter(x=>x.id!==kbId);
    store.documents=store.documents.filter(x=>x.kbId!==kbId);
    store.chunks=store.chunks.filter(x=>x.kbId!==kbId);
    logAudit(row.orgId,'Deleted the knowledge base '+row.name,'KnowledgeBase',kbId); return true; }),
  /** Which agents and rules depend on a base, so deletion can warn properly. */
  knowledgeUsage: (kbId:string) => mock(()=>({
    agents: store.agents.filter(a=>a.defaultKnowledgeBaseId===kbId||a.knowledgeBaseIds.includes(kbId)).map(a=>a.name),
    rules: store.routingRules.filter(r=>r.outcome.knowledgeBaseIds?.includes(kbId)).map(r=>r.name),
    campaigns: store.campaigns.filter(c=>c.knowledgeBaseIds.includes(kbId)).map(c=>c.name),
  })),
  getDocuments: (kbId:string) => mock(()=>store.documents.filter(x=>x.kbId===kbId)),
  uploadDocuments: (kbId:string,files:{name:string;size:number;type:string}[]) => update(()=> {
    const rows:KbDocument[]=files.map(file=>({id:'doc-'+id(),kbId,filename:file.name,mime:file.type,bytes:file.size,pages:Math.max(1,Math.round(file.size/18000)),status:'indexing',uploadedBy:store.profile.fullName,chunkCount:0}));
    store.documents.unshift(...rows);
    const kb=store.knowledgeBases.find(x=>x.id===kbId); if(kb)kb.status='indexing';
    setTimeout(()=> { rows.forEach(row=>{row.status='ready';row.chunkCount=Math.max(1,Math.round(row.bytes/6000));}); if(kb){kb.status='ready';kb.chunkCount=store.documents.filter(x=>x.kbId===kbId).reduce((n,x)=>n+x.chunkCount,0);kb.lastIndexedAt=new Date().toISOString();} notify(); },1800);
    return rows; }),
  deleteDocument: (docId:string) => update(()=> { store.documents=store.documents.filter(x=>x.id!==docId); return true; }),
  reindex: (kbId:string) => update(()=> { const kb=store.knowledgeBases.find(x=>x.id===kbId)!; kb.status='indexing'; setTimeout(()=>{kb.status='ready';kb.lastIndexedAt=new Date().toISOString();notify();},2000); return kb; }),
  searchKnowledge: (kbId:string,query:string) => mock(()=> { const words=query.toLowerCase().split(/\W+/).filter(Boolean); return store.chunks.filter(x=>x.kbId===kbId).map((x,i)=>({...x,score:Math.min(0.96,Math.max(0.44,x.score+(words.some(w=>x.section.toLowerCase().includes(w)||x.preview.toLowerCase().includes(w))?0.09:-0.16)-(i%3)*0.015))})).sort((a,b)=>b.score-a.score).slice(0,8); }),

  // ---- Routing ------------------------------------------------------------
  getRoutingRules: (orgId:string) => mock(()=>store.routingRules.filter(x=>x.orgId===orgId).sort((a,b)=>a.order-b.order)),
  saveRoutingRule: (rule:RoutingRule) => update(()=> { const existing=store.routingRules.find(x=>x.id===rule.id); if(existing)Object.assign(existing,rule); else store.routingRules.push(rule); logAudit(rule.orgId,'Saved the routing rule '+rule.name,'RoutingRule',rule.id); return rule; }),
  deleteRoutingRule: (ruleId:string) => update(()=> { store.routingRules=store.routingRules.filter(x=>x.id!==ruleId||x.isFallback); return true; }),
  reorderRoutingRules: (orgId:string,orderedIds:string[]) => update(()=> { orderedIds.forEach((rid,i)=>{const row=store.routingRules.find(x=>x.id===rid); if(row&&!row.isFallback)row.order=i;}); return store.routingRules.filter(x=>x.orgId===orgId).sort((a,b)=>a.order-b.order); }),
  /** Dry run: which rule would win, and which material the call would use. */
  testRoute: (orgId:string,number:string,channelType:ChannelType,outsideHours:boolean) => mock(()=> {
    const rules=store.routingRules.filter(x=>x.orgId===orgId&&x.enabled).sort((a,b)=>a.order-b.order);
    for (const rule of rules) {
      const c=rule.condition;
      const hit = rule.isFallback || (c.kind==='number'&&number===c.value) || (c.kind==='channel'&&channelType===c.value) || (c.kind==='prefix'&&number.startsWith(c.value)) || (c.kind==='hours'&&((c.value==='outside')===outsideHours));
      if (hit) {
        const kbIds=rule.outcome.knowledgeBaseIds||[];
        return { rule, knowledgeBaseNames: kbIds.map(k=>store.knowledgeBases.find(x=>x.id===k)?.name||'Unknown'),
          agentName: store.agents.find(a=>a.id===rule.outcome.agentId)?.name,
          assignedTo: store.profiles.find(p=>p.id===rule.outcome.assignToUserId)?.fullName };
      }
    }
    return null; }),

  // ---- Channels -----------------------------------------------------------
  getChannels: (orgId:string) => mock(()=>store.channels.filter(x=>x.orgId===orgId)),
  updateChannel: (channelId:string,patch:Partial<Channel>) => update(()=> { const row=store.channels.find(x=>x.id===channelId)!; Object.assign(row,patch); return row; }),
  testChannel: (channelId:string) => update(()=> { const row=store.channels.find(x=>x.id===channelId)!; row.status='verifying'; setTimeout(()=>{row.status=row.errorDetail?'error':'connected';row.lastCheckedAt=new Date().toISOString();notify();},1400); return row; }),
  getCredentials: (channelId:string) => mock(()=>store.credentials.filter(x=>x.channelId===channelId)),
  setCredential: (orgId:string,channelId:string,keyName:string,lastFour:string) => update(()=> {
    let row=store.credentials.find(x=>x.channelId===channelId&&x.keyName===keyName);
    if(row){Object.assign(row,{lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()});}
    else {row={id:'cred-'+id(),orgId,channelId,keyName,lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()};store.credentials.push(row);}
    logPlatform('Replaced credential',keyName+' for channel '+channelId,orgId); return row; }),

  // ---- Calls --------------------------------------------------------------
  getCalls: (orgId:string) => mock(()=>store.calls.filter(x=>x.orgId===orgId)),
  getCall: (callId:string) => mock(()=>store.calls.find(x=>x.id===callId)),
  getTranscript: (callId:string) => mock(()=>store.transcripts.filter(x=>x.callId===callId)),
  getRagQueries: (callId:string) => mock(()=>store.ragQueries.filter(x=>x.callId===callId)),
  endCall: (callId:string) => update(()=> { const row=store.calls.find(x=>x.id===callId)!; row.status='completed';row.endedAt=new Date().toISOString();row.recordingState='PENDING';return row; }),
  takeOverCall: (callId:string,userId?:string) => update(()=> { const row=store.calls.find(x=>x.id===callId)!; row.mode='operator'; if(userId)row.assignedToUserId=userId; return row; }),
  /** Place a call. `knowledgeBaseId` is the explicit per-call choice. */
  createTestCall: (orgId:string,agentId:string,phoneNumber:string,knowledgeBaseId?:string) => update(()=> {
    const resolved=resolveKnowledge(orgId,agentId,knowledgeBaseId,'sim',phoneNumber);
    const row:Call={id:'call-'+id(),orgId,agentId,channelType:'sim',direction:'OUTBOUND',phoneNumber,fromNumber:'+92 21 555 0142',toNumber:phoneNumber,status:'active',mode:'agent',startedAt:new Date().toISOString(),durationSeconds:0,outcome:'Test call',summary:'Test call in progress.',recordingState:'RECORDING',recordingScope:'two_way',knowledgeBaseId:resolved.kbId,knowledgeBaseName:resolved.kbName,resolvedBy:resolved.resolvedBy};
    store.calls.unshift(row);
    setTimeout(()=>{row.status='completed';row.endedAt=new Date().toISOString();row.durationSeconds=18;row.recordingState='PENDING';row.summary='The test call completed. The agent answered from '+resolved.kbName+'.';notify();},6500);
    return row; }),

  // ---- Outbound campaigns -------------------------------------------------
  getCampaigns: (orgId:string) => mock(()=>store.campaigns.filter(x=>x.orgId===orgId)),
  getCampaign: (campaignId:string) => mock(()=>store.campaigns.find(x=>x.id===campaignId)),
  getCampaignContacts: (campaignId:string) => mock(()=>store.campaignContacts.filter(x=>x.campaignId===campaignId)),
  createCampaign: (orgId:string,patch:Partial<Campaign>) => update(()=> {
    const row:Campaign={id:'camp-'+id(),orgId,name:patch.name||'Untitled campaign',agentId:patch.agentId||store.agents.find(a=>a.orgId===orgId)?.id||'',knowledgeBaseIds:patch.knowledgeBaseIds||[],status:'draft',total:patch.total||0,attempted:0,connected:0,unanswered:0,windowStart:patch.windowStart||'09:00',windowEnd:patch.windowEnd||'17:00',maxAttempts:patch.maxAttempts||2,retryAfterMinutes:patch.retryAfterMinutes||240,createdAt:new Date().toISOString()};
    store.campaigns.unshift(row); logAudit(orgId,'Created the campaign '+row.name,'Campaign',row.id); return row; }),
  updateCampaign: (campaignId:string,patch:Partial<Campaign>) => update(()=> { const row=store.campaigns.find(x=>x.id===campaignId)!; Object.assign(row,patch); return row; }),
  setCampaignStatus: (campaignId:string,status:Campaign['status']) => update(()=> { const row=store.campaigns.find(x=>x.id===campaignId)!; row.status=status; logAudit(row.orgId,'Set the campaign '+row.name+' to '+status,'Campaign',row.id); return row; }),

  // ---- The improvement loop ----------------------------------------------
  getUnanswered: (orgId:string) => mock(()=>store.unanswered.filter(x=>x.orgId===orgId)),
  resolveUnanswered: (questionId:string,status:'answered'|'ignored'|'open') => update(()=> { const row=store.unanswered.find(x=>x.id===questionId)!; row.status=status; return row; }),

  // ---- Messaging ----------------------------------------------------------
  getMessages: (orgId:string) => mock(()=>store.messages.filter(x=>x.orgId===orgId)),
  sendMessage: (orgId:string,toNumber:string,body:string,templateId?:string) => update(()=> { const row:Message={id:'msg-'+id(),orgId,toNumber,body,templateId,status:'sent',sentAt:new Date().toISOString()};store.messages.unshift(row);return row; }),
  getTemplates: (orgId:string) => mock(()=>store.templates.filter(x=>x.orgId===orgId)),

  // ---- Billing and admin --------------------------------------------------
  getUsage: (orgId:string) => mock(()=>store.usage.filter(x=>x.orgId===orgId)),
  getPlans: () => mock(()=>store.plans),
  changePlan: (orgId:string,plan:string) => update(()=> {const row=store.organizations.find(x=>x.id===orgId)!;row.plan=plan;return row;}),
  getApiKeys: (orgId:string) => mock(()=>store.apiKeys.filter(x=>x.orgId===orgId)),
  getWebhooks: (orgId:string) => mock(()=>store.webhooks.filter(x=>x.orgId===orgId)),
  createWebhook: (orgId:string,url:string,events:string[]) => update(()=> { const row:WebhookEndpoint={id:'hook-'+id(),orgId,url,events,secretLastFour:id().slice(-4),status:'active'};store.webhooks.push(row);return row; }),
  getAudit: (orgId:string) => mock(()=>store.audit.filter(x=>x.orgId===orgId)),
  getGuides: () => mock(()=>store.guides),
  getGuide: (slug:string) => mock(()=>store.guides.find(x=>x.slug===slug) as Guide|undefined),
  updateSetting: (orgId:string,key:string,value:string|number|boolean) => update(()=> {store.settings[orgId] ||= {};store.settings[orgId][key]=value;logAudit(orgId,'Changed a setting','Setting',key);return value;}),

  // ---- Platform portal ----------------------------------------------------
  getPlatformCompanies: () => mock(()=>store.organizations.map(org=>{
    const calls=store.calls.filter(c=>c.orgId===org.id);
    const failing=store.channels.filter(c=>c.orgId===org.id&&c.status==='error').length;
    const blocked=store.provisioning.filter(p=>p.orgId===org.id&&p.state==='blocked').length;
    return { org, minutesThisPeriod: Math.round(calls.reduce((n,c)=>n+c.durationSeconds,0)/60),
      callCount: calls.length, lastCallAt: calls[0]?.startedAt,
      health: (failing+blocked)>0?(blocked>0?'error':'warning'):'ok' as 'ok'|'warning'|'error',
      openIssues: failing+blocked };
  })),
  createCompany: (name:string,countryCode:string) => update(()=> {
    const org:Organization={id:'org-'+id(),name,slug:name.toLowerCase().replace(/\W+/g,'-'),accentColor:'oklch(0.58 0.13 245)',plan:'Starter',countryCode,defaultLanguage:'ur',timezone:'Asia/Karachi',createdAt:new Date().toISOString(),trialEndsAt:new Date(Date.now()+14*86400000).toISOString(),onboardingStep:0,status:'setup',provisioningMode:'managed',strictnessPreset:'balanced',pacePreset:'natural',voiceQualityPreset:'standard',recordCalls:false,recordBothSides:false,retentionDays:30,endCallsAfterMinutes:15,businessHoursStart:'09:00',businessHoursEnd:'17:00'};
    store.organizations.push(org); store.settings[org.id]={};
    logPlatform('Created company',name,org.id); return org; }),
  setCompanyStatus: (orgId:string,status:Organization['status']) => update(()=> { const row=store.organizations.find(x=>x.id===orgId)!; row.status=status; logPlatform('Set company status',row.name+' is now '+status,orgId); return row; }),
  getProvisioning: (orgId?:string) => mock(()=>orgId?store.provisioning.filter(x=>x.orgId===orgId):store.provisioning),
  updateProvisioning: (taskId:string,patch:Partial<ProvisioningTask>) => update(()=> {
    const row=store.provisioning.find(x=>x.id===taskId)!; Object.assign(row,patch,{updatedAt:new Date().toISOString()});
    if(patch.state==='active'){ const ch=store.channels.find(c=>c.orgId===row.orgId&&c.type===row.kind); if(ch){ch.status='connected';ch.lastCheckedAt=new Date().toISOString();} }
    logPlatform('Updated setup',row.kind+' is now '+row.state,row.orgId); return row; }),
  assignNumber: (orgId:string,kind:ChannelType,number:string) => update(()=> {
    const ch=store.channels.find(c=>c.orgId===orgId&&c.type===kind); if(ch){ch.displayNumber=number;ch.status='verifying';}
    logPlatform('Assigned number',number+' to '+kind,orgId); return ch; }),
  getPlatformAudit: () => mock(()=>store.platformAudit),
  startImpersonation: (orgId:string) => update(()=> { logPlatform('Started impersonation','Opened '+(store.organizations.find(x=>x.id===orgId)?.name||orgId)+' read-only',orgId); return true; }),
  endImpersonation: (orgId:string) => update(()=> { logPlatform('Ended impersonation','Closed read-only session',orgId); return true; }),

  // ---- Live simulation ----------------------------------------------------
  startLiveSimulation: () => {
    if (!USE_MOCK) throw new Error('not implemented');
    const timer=setInterval(()=>{
      const call=store.calls.find(x=>x.status==='active'); if(!call)return;
      const count=store.transcripts.filter(x=>x.callId===call.id).length;
      const lines=['Assalam o alaikum, main aap ki kya madad kar sakti hoon?','I would like to know about admissions.','Let me check the admissions material for you.','Applications are open until the end of this month.'];
      store.transcripts.push({id:'live-'+id(),callId:call.id,role:count%2===0?'agent':'caller',speaker:count%2===0?'Agent':'Caller',text:lines[count%lines.length],timestamp:new Date().toISOString()});
      if(count%3===2)store.ragQueries.push({id:'rag-'+id(),callId:call.id,query:'admissions deadline',topScore:0.81,threshold:0.6,hitCount:3,answered:true,sections:['Admissions'],documentName:'Admissions handbook.pdf',page:14,timestamp:new Date().toISOString()});
      notify();
    },3500);
    return ()=>clearInterval(timer);
  },
  deleteOrganization: (orgId:string) => update(()=> {
    const kbIds=store.knowledgeBases.filter(x=>x.orgId===orgId).map(x=>x.id);
    store.organizations=store.organizations.filter(x=>x.id!==orgId);
    store.memberships=store.memberships.filter(x=>x.orgId!==orgId);
    store.agents=store.agents.filter(x=>x.orgId!==orgId);
    store.personas=store.personas.filter(x=>x.orgId!==orgId);
    store.voices=store.voices.filter(x=>x.orgId!==orgId);
    store.knowledgeBases=store.knowledgeBases.filter(x=>x.orgId!==orgId);
    store.documents=store.documents.filter(x=>!kbIds.includes(x.kbId));
    store.chunks=store.chunks.filter(x=>!kbIds.includes(x.kbId));
    store.channels=store.channels.filter(x=>x.orgId!==orgId);
    store.credentials=store.credentials.filter(x=>x.orgId!==orgId);
    store.calls=store.calls.filter(x=>x.orgId!==orgId);
    store.routingRules=store.routingRules.filter(x=>x.orgId!==orgId);
    store.campaigns=store.campaigns.filter(x=>x.orgId!==orgId);
    store.unanswered=store.unanswered.filter(x=>x.orgId!==orgId);
    store.messages=store.messages.filter(x=>x.orgId!==orgId);
    store.templates=store.templates.filter(x=>x.orgId!==orgId);
    store.usage=store.usage.filter(x=>x.orgId!==orgId);
    store.apiKeys=store.apiKeys.filter(x=>x.orgId!==orgId);
    store.webhooks=store.webhooks.filter(x=>x.orgId!==orgId);
    store.audit=store.audit.filter(x=>x.orgId!==orgId);
    store.provisioning=store.provisioning.filter(x=>x.orgId!==orgId);
    delete store.settings[orgId];
    return true;
  }),
  snapshot: () => clone(store)
};
