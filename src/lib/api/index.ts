import { initialStore } from '../fixtures';
import type { Store, Organization, Agent, Persona, Voice, KnowledgeBase, KbDocument, Channel, Credential, Call, Message, Invitation, Role, WebhookEndpoint, Guide } from '../types';

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
    if (Math.random() < failureRate) throw new Error('The simulated connection failed. Please retry.');
    return clone(work());
  } finally { pending--; notifyPending(); }
}
const update = <T,>(work:()=>T) => mock(() => { const result=work(); notify(); return result; },0);
const id = () => Math.random().toString(36).slice(2,10);
export const api = {
  getOrganizations: () => mock(()=>store.organizations),
  getOrganization: (orgId:string) => mock(()=>store.organizations.find(x=>x.id===orgId)),
  updateOrganization: (orgId:string, patch:Partial<Organization>) => update(()=> { const row=store.organizations.find(x=>x.id===orgId)!; Object.assign(row,patch); return row; }),
  getProfile: () => mock(()=>store.profile),
  getMemberships: (orgId:string) => mock(()=>store.memberships.filter(x=>x.orgId===orgId)),
  getInvitations: (orgId:string) => mock(()=>store.invitations.filter(x=>x.orgId===orgId)),
  invite: (orgId:string,email:string,role:Role) => update(()=> { const row:Invitation={id:id(),orgId,email,role,expiresAt:new Date(Date.now()+7*86400000).toISOString(),status:'pending'}; store.invitations.unshift(row); store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action:'Invited '+email,targetType:'Invitation',targetId:row.id,createdAt:new Date().toISOString()}); return row; }),
  revokeInvitation: (invId:string) => update(()=> { const row=store.invitations.find(x=>x.id===invId)!; row.status='revoked'; return row; }),
  resendInvitation: (invId:string) => update(()=> { const row=store.invitations.find(x=>x.id===invId)!; row.expiresAt=new Date(Date.now()+7*86400000).toISOString(); return row; }),
  changeMemberRole: (orgId:string,userId:string,role:Role) => update(()=> { const row=store.memberships.find(x=>x.orgId===orgId&&x.userId===userId)!; row.role=role; store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action:'Changed member role to '+role,targetType:'Membership',targetId:userId,createdAt:new Date().toISOString()}); return row; }),
  removeMember: (orgId:string,userId:string) => update(()=> { store.memberships=store.memberships.filter(x=>!(x.orgId===orgId&&x.userId===userId)); store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action:'Removed team member',targetType:'Membership',targetId:userId,createdAt:new Date().toISOString()}); return true; }),
  getAgents: (orgId:string) => mock(()=>store.agents.filter(x=>x.orgId===orgId)),
  getAgent: (agentId:string) => mock(()=>store.agents.find(x=>x.id===agentId)),
  createAgent: (orgId:string,name:string) => update(()=> { const row:Agent={id:'agent-'+id(),orgId,name,status:'draft',personaId:'persona-'+id(),knowledgeBaseId:store.knowledgeBases.find(x=>x.orgId===orgId)?.id||'',voiceId:'voice-'+id(),channels:[],createdAt:new Date().toISOString()}; store.agents.push(row); store.personas.push({id:row.personaId,orgId,agentName:name,gender:'neutral',greeting:'Hello, how can I help?',roleDescription:'Help callers with accurate information.',languagePolicy:'Match the caller language.',toneNotes:'Clear and friendly.',forbiddenPhrases:[],escalationRules:'Transfer complex requests.',closingBehaviour:'Confirm next steps.',compiledPrompt:''}); store.voices.push({id:row.voiceId,orgId,voiceName:'Alloy',speed:0.95,language:'en-US',sttModel:'whisper-1',noiseReduction:true,turnDetection:'server_vad',vadThreshold:0.5,vadSilenceMs:550,vadPrefixPaddingMs:300,vadEagerness:'auto',interruptionEnabled:true,greetingDelaySeconds:0.4}); return row; }),
  updateAgent: (agentId:string,patch:Partial<Agent>) => update(()=> { const row=store.agents.find(x=>x.id===agentId)!; Object.assign(row,patch); return row; }),
  getPersona: (id:string) => mock(()=>store.personas.find(x=>x.id===id)),
  updatePersona: (id:string,patch:Partial<Persona>) => update(()=> { const row=store.personas.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),
  getVoice: (id:string) => mock(()=>store.voices.find(x=>x.id===id)),
  updateVoice: (id:string,patch:Partial<Voice>) => update(()=> { const row=store.voices.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),
  getKnowledgeBase: (orgId:string) => mock(()=>store.knowledgeBases.find(x=>x.orgId===orgId)),
  updateKnowledgeBase: (id:string,patch:Partial<KnowledgeBase>) => update(()=> { const row=store.knowledgeBases.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),
  getDocuments: (kbId:string) => mock(()=>store.documents.filter(x=>x.kbId===kbId)),
  uploadDocuments: (kbId:string,files:{name:string;size:number;type:string}[]) => update(()=> { const rows:KbDocument[]=files.map(file=>({id:'doc-'+id(),kbId,filename:file.name,mime:file.type,bytes:file.size,pages:Math.max(1,Math.round(file.size/18000)),status:'indexing',uploadedBy:store.profile.fullName,chunkCount:0})); store.documents.unshift(...rows); setTimeout(()=> { rows.forEach(row=>{row.status='ready';row.chunkCount=Math.max(1,Math.round(row.bytes/6000));}); const kb=store.knowledgeBases.find(x=>x.id===kbId); if(kb){kb.status='ready';kb.chunkCount=store.documents.filter(x=>x.kbId===kbId).reduce((n,x)=>n+x.chunkCount,0);kb.lastIndexedAt=new Date().toISOString();} notify(); },1800); return rows; }),
  deleteDocument: (id:string) => update(()=> { store.documents=store.documents.filter(x=>x.id!==id); return true; }),
  reindex: (id:string) => update(()=> { const kb=store.knowledgeBases.find(x=>x.id===id)!; kb.status='indexing'; setTimeout(()=>{kb.status='ready';kb.lastIndexedAt=new Date().toISOString();notify();},2000); return kb; }),
  searchKnowledge: (kbId:string,query:string) => mock(()=> { const words=query.toLowerCase().split(/\W+/).filter(Boolean); return store.chunks.filter(x=>x.kbId===kbId).map((x,i)=>({...x,score:Math.min(0.96,Math.max(0.44,x.score+(words.some(w=>x.section.toLowerCase().includes(w)||x.preview.toLowerCase().includes(w))?0.09:-0.16)-(i%3)*0.015))})).sort((a,b)=>b.score-a.score).slice(0,8); }),
  getChannels: (orgId:string) => mock(()=>store.channels.filter(x=>x.orgId===orgId)),
  updateChannel: (id:string,patch:Partial<Channel>) => update(()=> { const row=store.channels.find(x=>x.id===id)!; Object.assign(row,patch); return row; }),
  testChannel: (id:string) => update(()=> { const row=store.channels.find(x=>x.id===id)!; row.status='verifying'; setTimeout(()=>{row.status=row.errorDetail?'error':'connected';row.lastCheckedAt=new Date().toISOString();notify();},1400); return row; }),
  getCredentials: (channelId:string) => mock(()=>store.credentials.filter(x=>x.channelId===channelId)),
  setCredential: (orgId:string,channelId:string,keyName:string,lastFour:string) => update(()=> { let row=store.credentials.find(x=>x.channelId===channelId&&x.keyName===keyName); if(row){Object.assign(row,{lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()});} else {row={id:'cred-'+id(),orgId,channelId,keyName,lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()};store.credentials.push(row);} store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action:'Replaced '+keyName,targetType:'Credential',targetId:row.id,createdAt:new Date().toISOString()}); return row; }),
  getCalls: (orgId:string) => mock(()=>store.calls.filter(x=>x.orgId===orgId)),
  getCall: (callId:string) => mock(()=>store.calls.find(x=>x.id===callId)),
  getTranscript: (callId:string) => mock(()=>store.transcripts.filter(x=>x.callId===callId)),
  getRagQueries: (callId:string) => mock(()=>store.ragQueries.filter(x=>x.callId===callId)),
  endCall: (id:string) => update(()=> { const row=store.calls.find(x=>x.id===id)!; row.status='completed';row.endedAt=new Date().toISOString();row.recordingState='PENDING';return row; }),
  takeOverCall: (id:string) => update(()=> { const row=store.calls.find(x=>x.id===id)!; row.mode='operator';return row; }),
  createTestCall: (orgId:string,agentId:string,phoneNumber:string) => update(()=> { const row:Call={id:'call-'+id(),orgId,agentId,channelType:'sim',direction:'OUTBOUND',phoneNumber,fromNumber:'+92 21 555 0142',toNumber:phoneNumber,status:'active',mode:'agent',startedAt:new Date().toISOString(),durationSeconds:0,outcome:'Test call',summary:'Test call in progress.',recordingState:'RECORDING',recordingScope:'two_way'};store.calls.unshift(row);setTimeout(()=>{row.status='completed';row.endedAt=new Date().toISOString();row.durationSeconds=18;row.recordingState='PENDING';row.summary='Scripted test call completed successfully.';notify();},6500);return row; }),
  getMessages: (orgId:string) => mock(()=>store.messages.filter(x=>x.orgId===orgId)),
  sendMessage: (orgId:string,toNumber:string,body:string,templateId?:string) => update(()=> { const row:Message={id:'msg-'+id(),orgId,toNumber,body,templateId,status:'sent',sentAt:new Date().toISOString()};store.messages.unshift(row);return row; }),
  getTemplates: (orgId:string) => mock(()=>store.templates.filter(x=>x.orgId===orgId)),
  getUsage: (orgId:string) => mock(()=>store.usage.filter(x=>x.orgId===orgId)),
  getPlans: () => mock(()=>store.plans),
  changePlan: (orgId:string,plan:string) => update(()=> {const row=store.organizations.find(x=>x.id===orgId)!;row.plan=plan;return row;}),
  getApiKeys: (orgId:string) => mock(()=>store.apiKeys.filter(x=>x.orgId===orgId)),
  getWebhooks: (orgId:string) => mock(()=>store.webhooks.filter(x=>x.orgId===orgId)),
  createWebhook: (orgId:string,url:string,events:string[]) => update(()=> { const row:WebhookEndpoint={id:'hook-'+id(),orgId,url,events,secretLastFour:id().slice(-4),status:'active'};store.webhooks.push(row);return row; }),
  getAudit: (orgId:string) => mock(()=>store.audit.filter(x=>x.orgId===orgId)),
  deleteOrganization: (orgId:string) => update(()=> {const kbIds=store.knowledgeBases.filter(x=>x.orgId===orgId).map(x=>x.id);store.organizations=store.organizations.filter(x=>x.id!==orgId);store.memberships=store.memberships.filter(x=>x.orgId!==orgId);store.agents=store.agents.filter(x=>x.orgId!==orgId);store.personas=store.personas.filter(x=>x.orgId!==orgId);store.voices=store.voices.filter(x=>x.orgId!==orgId);store.knowledgeBases=store.knowledgeBases.filter(x=>x.orgId!==orgId);store.documents=store.documents.filter(x=>!kbIds.includes(x.kbId));store.chunks=store.chunks.filter(x=>!kbIds.includes(x.kbId));store.channels=store.channels.filter(x=>x.orgId!==orgId);store.credentials=store.credentials.filter(x=>x.orgId!==orgId);store.calls=store.calls.filter(x=>x.orgId!==orgId);store.messages=store.messages.filter(x=>x.orgId!==orgId);store.templates=store.templates.filter(x=>x.orgId!==orgId);store.usage=store.usage.filter(x=>x.orgId!==orgId);store.apiKeys=store.apiKeys.filter(x=>x.orgId!==orgId);store.webhooks=store.webhooks.filter(x=>x.orgId!==orgId);store.audit=store.audit.filter(x=>x.orgId!==orgId);delete store.settings[orgId];return true;}),
  getGuides: () => mock(()=>store.guides),
  getGuide: (slug:string) => mock(()=>store.guides.find(x=>x.slug===slug) as Guide|undefined),
  startLiveSimulation: () => { if (!USE_MOCK) throw new Error('not implemented'); const timer=setInterval(()=>{const call=store.calls.find(x=>x.status==='active');if(!call)return;const count=store.transcripts.filter(x=>x.callId===call.id).length;const lines=['Hello, thank you for calling. How may I help?','I would like to know about admissions.','Let me check the current admissions guide.','Applications are open until the end of the month.'];store.transcripts.push({id:'live-'+id(),callId:call.id,role:count%2===0?'agent':'caller',speaker:count%2===0?'Agent':'Caller',text:lines[count%lines.length],timestamp:new Date().toISOString()});if(count%3===2)store.ragQueries.push({id:'rag-'+id(),callId:call.id,query:'admissions deadline',topScore:0.81,threshold:0.6,hitCount:3,answered:true,sections:['Admissions'],timestamp:new Date().toISOString()});notify();},3500);return ()=>clearInterval(timer); },
  updateSetting: (orgId:string,key:string,value:string|number|boolean) => update(()=> {store.settings[orgId] ||= {};store.settings[orgId][key]=value;store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action:'Updated '+key,targetType:'Setting',targetId:key,createdAt:new Date().toISOString()});return value;}),
  snapshot: () => clone(store)
};





