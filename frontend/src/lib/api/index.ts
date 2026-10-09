import { initialStore } from '../fixtures';
import { LIVE, hasAdminKey, hasAuthToken } from './http';
import { live, toChannels, toCredentials, toOrganization } from './live';
import type { Store, Organization, Agent, Persona, Voice, KnowledgeBase, KbDocument, Channel, Call, Message, Invitation, Role, WebhookEndpoint, Guide, CallSetup, Campaign, ChannelType, ProvisioningTask, Preset, KnowledgeResolution } from '../types';

/**
 * When VITE_API_URL is set this module talks to the Python backend for
 * everything the backend owns — the company and its credentials, calls,
 * recordings, messages and Google Drive. The remaining slices still come from
 * the fixtures, and the screens that read them are gated behind `isLive`
 * so nothing presents invented data as real. See `live.ts` for the mapping.
 */
export const USE_MOCK = !LIVE;
export { LIVE, hasAuthToken, setAuthToken, hasAdminKey, setAdminKey,
         getViewingCompany, setViewingCompany } from './http';
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

/**
 * A real request, counted in the same pending tally the mock uses so the
 * global activity indicator keeps working. No artificial latency and no
 * injected failures: those exist to make the prototype feel like a network,
 * and this *is* one.
 */
/**
 * A method the backend has no equivalent for.
 *
 * Against the fixtures these mutate the in-memory store and look like they
 * worked. Against the real API they would do nothing at all, silently, which
 * is worse than a button that says why it is unavailable — so in a live build
 * they refuse out loud and the screen shows the reason.
 */
function notSupported(what: string): Promise<never> {
  return Promise.reject(new Error(what));
}

async function real<T>(work:()=>Promise<T>):Promise<T> {
  pending++; notifyPending();
  try { return await work(); } finally { pending--; notifyPending(); }
}

/**
 * Pull the company, its channels and its credential state into the store.
 *
 * The backend has one tenant per number and the app has an organisation with
 * channels hanging off it, so this is where one becomes the other.
 */
export async function hydrateCompany(): Promise<void> {
  if (!LIVE || !hasAuthToken()) return;
  const company = await live.company();
  const orgId = company.phoneNumberId;
  const previous = store.organizations.find(x => x.id === orgId);
  // Replaced, not merged: leaving the fixture companies in the list would let
  // a screen resolve one of them and show a stranger's name beside real calls.
  store.organizations = [toOrganization(company, previous)];
  store.channels = toChannels(company);
  store.credentials = toCredentials(company);
  notify();
}

export async function hydrateCalls(): Promise<void> {
  if (!LIVE || !hasAuthToken()) return;
  store.calls = await live.calls();
  notify();
}

export async function hydrateMessages(): Promise<void> {
  if (!LIVE || !hasAuthToken()) return;
  store.messages = await live.messages();
  notify();
}

/** Everything, for the initial load. */
export async function hydrate(): Promise<void> {
  if (!LIVE || !hasAuthToken()) return;
  await hydrateCompany();
  // Settled, not all: a company with no WhatsApp credentials yet still gets
  // its dashboard, with the parts that need Meta simply empty.
  await Promise.allSettled([hydrateCalls(), hydrateMessages()]);
}
const id = () => Math.random().toString(36).slice(2,10);
const logAudit = (orgId:string, action:string, targetType:string, targetId:string) => {
  store.audit.unshift({id:id(),orgId,actorName:store.profile.fullName,action,targetType,targetId,createdAt:new Date().toISOString()});
};
const logPlatform = (action:string, detail:string, orgId?:string) => {
  store.platformAudit.unshift({id:id(),actorEmail:store.profile.email,action,orgId,detail,createdAt:new Date().toISOString()});
};

/**
 * Which knowledge a call uses. There is now one answer, not three: the call
 * setup for that number and direction. The agent and company defaults remain
 * only as a safety net for a number with no setup yet.
 */
function resolveKnowledge(orgId:string, agentId:string, setupId?:string, channelId?:string):{kbId:string;kbName:string;resolvedBy:KnowledgeResolution} {
  const named = (kbId:string, resolvedBy:KnowledgeResolution) => ({kbId,kbName:store.knowledgeBases.find(x=>x.id===kbId)?.name||'Default',resolvedBy});
  const setup = setupId
    ? store.callSetups.find(x=>x.id===setupId)
    : store.callSetups.find(x=>x.orgId===orgId&&x.channelId===channelId&&x.direction==='INBOUND'&&x.enabled);
  if (setup?.knowledgeBaseIds.length) return named(setup.knowledgeBaseIds[0], setupId?'explicit':'setup');
  const agent = store.agents.find(x=>x.id===agentId);
  if (agent?.defaultKnowledgeBaseId) return named(agent.defaultKnowledgeBaseId,'agent');
  const fallback = store.knowledgeBases.find(x=>x.orgId===orgId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===orgId);
  return named(fallback?.id||'','company');
}

export const api = {
  // ---- Organisations and people ------------------------------------------
  getOrganizations: () => mock(()=>store.organizations),
  getOrganization: (orgId:string) => LIVE
    ? real(async()=>{ await hydrateCompany(); return clone(store.organizations.find(x=>x.id===orgId)); })
    : mock(()=>store.organizations.find(x=>x.id===orgId)),
  updateOrganization: (orgId:string, patch:Partial<Organization>) => LIVE
    ? real(async()=>{
        // Only the fields the backend owns are sent; the rest of the app's
        // Organization has no counterpart there yet and would be rejected.
        const body:Record<string,unknown>={};
        if(patch.name!==undefined) body.name=patch.name;
        if(patch.recordCalls!==undefined) body.recordCalls=patch.recordCalls;
        if(patch.defaultLanguage!==undefined) body.language=patch.defaultLanguage;
        if(patch.countryCode!==undefined) body.defaultCountryCode=patch.countryCode;
        if(Object.keys(body).length) await live.updateCompany(body);
        await hydrateCompany();
        return clone(store.organizations.find(x=>x.id===orgId)!);
      })
    : update(()=> { const row=store.organizations.find(x=>x.id===orgId)!; Object.assign(row,patch); return row; }),
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
  createAgent: (orgId:string,name:string) => LIVE
    ? notSupported('You have one agent per number. Open the Agent screen to set it up.')
    : update(()=> {
    const defaultKb=store.knowledgeBases.find(x=>x.orgId===orgId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===orgId);
    const row:Agent={id:'agent-'+id(),orgId,name,status:'draft',personaId:'persona-'+id(),defaultKnowledgeBaseId:defaultKb?.id||'',knowledgeBaseIds:defaultKb?[defaultKb.id]:[],voiceId:'voice-'+id(),channelIds:[],createdAt:new Date().toISOString()};
    store.agents.push(row);
    store.personas.push({id:row.personaId,orgId,agentName:name,gender:'neutral',greeting:'Hello, how can I help?',roleDescription:'Help callers with accurate information.',languagePolicy:'Match the caller language.',toneNotes:'Clear and friendly.',forbiddenPhrases:[],escalationRules:'Hand complex requests to a person.',closingBehaviour:'Confirm next steps.',compiledPrompt:''});
    store.voices.push({id:row.voiceId,orgId,voiceName:'Alloy',speed:0.95,language:'ur-PK',sttModel:'gpt-4o-transcribe',noiseReduction:true,turnDetection:'server_vad',vadThreshold:0.5,vadSilenceMs:600,vadPrefixPaddingMs:300,vadEagerness:'medium',interruptionEnabled:true,greetingDelaySeconds:0.4});
    logAudit(orgId,'Created the agent '+name,'Agent',row.id); return row; }),
  updateAgent: (agentId:string,patch:Partial<Agent>) => update(()=> { const row=store.agents.find(x=>x.id===agentId)!; Object.assign(row,patch); return row; }),
  deleteAgent: (agentId:string) => update(()=> {
    const row=store.agents.find(x=>x.id===agentId); if(!row) return false;
    store.agents=store.agents.filter(x=>x.id!==agentId);
    store.personas=store.personas.filter(x=>x.id!==row.personaId);
    store.voices=store.voices.filter(x=>x.id!==row.voiceId);
    store.callSetups=store.callSetups.filter(r=>r.agentId!==agentId);
    logAudit(row.orgId,'Deleted the agent '+row.name,'Agent',agentId); return true; }),
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
  createKnowledgeBase: (orgId:string,name:string,purpose:string) => LIVE
    ? notSupported('Your documents all go in one place. Open the Knowledge screen to add one.')
    : update(()=> {
    const row:KnowledgeBase={id:'kb-'+id(),orgId,name,purpose,isDefault:!store.knowledgeBases.some(x=>x.orgId===orgId),status:'empty',chunkSize:800,chunkOverlap:120,embeddingModel:'text-embedding-3-small',similarityThreshold:0.6,topK:6,maxChunksPerSection:2,maxContextChars:9000,chunkCount:0,answeredLast30d:0};
    store.knowledgeBases.push(row); logAudit(orgId,'Created the knowledge base '+name,'KnowledgeBase',row.id); return row; }),
  updateKnowledgeBase: (kbId:string,patch:Partial<KnowledgeBase>) => update(()=> { const row=store.knowledgeBases.find(x=>x.id===kbId)!; Object.assign(row,patch); return row; }),
  setDefaultKnowledgeBase: (orgId:string,kbId:string) => update(()=> { store.knowledgeBases.filter(x=>x.orgId===orgId).forEach(x=>{x.isDefault=x.id===kbId}); return true; }),
  deleteKnowledgeBase: (kbId:string) => update(()=> {
    const row=store.knowledgeBases.find(x=>x.id===kbId); if(!row) return false;
    const fallback=store.knowledgeBases.find(x=>x.orgId===row.orgId&&x.id!==kbId&&x.isDefault)||store.knowledgeBases.find(x=>x.orgId===row.orgId&&x.id!==kbId);
    store.agents.filter(a=>a.orgId===row.orgId).forEach(a=>{ if(a.defaultKnowledgeBaseId===kbId)a.defaultKnowledgeBaseId=fallback?.id||''; a.knowledgeBaseIds=a.knowledgeBaseIds.filter(x=>x!==kbId); });
    store.callSetups.filter(r=>r.orgId===row.orgId).forEach(r=>{ r.knowledgeBaseIds=r.knowledgeBaseIds.filter(x=>x!==kbId); });
    store.knowledgeBases=store.knowledgeBases.filter(x=>x.id!==kbId);
    store.documents=store.documents.filter(x=>x.kbId!==kbId);
    store.chunks=store.chunks.filter(x=>x.kbId!==kbId);
    logAudit(row.orgId,'Deleted the knowledge base '+row.name,'KnowledgeBase',kbId); return true; }),
  /** Which agents and rules depend on a base, so deletion can warn properly. */
  knowledgeUsage: (kbId:string) => mock(()=>({
    agents: store.agents.filter(a=>a.defaultKnowledgeBaseId===kbId||a.knowledgeBaseIds.includes(kbId)).map(a=>a.name),
    rules: store.callSetups.filter(r=>r.knowledgeBaseIds.includes(kbId)).map(r=>r.name),
    campaigns: store.campaigns.filter(c=>{const sp=store.callSetups.find(x=>x.id===c.setupId);return !!sp&&sp.knowledgeBaseIds.includes(kbId)}).map(c=>c.name),
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

  // ---- Call setups --------------------------------------------------------
  getCallSetups: (orgId:string) => mock(()=>store.callSetups.filter(x=>x.orgId===orgId)),
  getCallSetup: (setupId:string) => mock(()=>store.callSetups.find(x=>x.id===setupId)),
  /** A number may hold one incoming setup, and any number of outgoing ones. */
  saveCallSetup: (setup:CallSetup) => update(()=> {
    if (setup.direction==='INBOUND') {
      const clash=store.callSetups.find(x=>x.channelId===setup.channelId&&x.direction==='INBOUND'&&x.id!==setup.id);
      if (clash) throw new Error('This number already has an incoming setup. Edit that one instead.');
    }
    const existing=store.callSetups.find(x=>x.id===setup.id);
    if (existing) Object.assign(existing,setup); else store.callSetups.push(setup);
    logAudit(setup.orgId,'Saved the call setup '+setup.name,'CallSetup',setup.id);
    return setup; }),
  deleteCallSetup: (setupId:string) => update(()=> {
    const row=store.callSetups.find(x=>x.id===setupId); if(!row) return false;
    store.callSetups=store.callSetups.filter(x=>x.id!==setupId);
    store.campaigns=store.campaigns.filter(c=>c.setupId!==setupId);
    logAudit(row.orgId,'Deleted the call setup '+row.name,'CallSetup',setupId); return true; }),
  /** Whether a number can still take an incoming setup. */
  canAddInbound: (channelId:string) => mock(()=>!store.callSetups.some(x=>x.channelId===channelId&&x.direction==='INBOUND')),

  // ---- Channels -----------------------------------------------------------
  getChannels: (orgId:string) => LIVE
    ? real(async()=>{ await hydrateCompany(); return clone(store.channels.filter(x=>x.orgId===orgId)); })
    : mock(()=>store.channels.filter(x=>x.orgId===orgId)),
  updateChannel: (channelId:string,patch:Partial<Channel>) => update(()=> { const row=store.channels.find(x=>x.id===channelId)!; Object.assign(row,patch); return row; }),
  testChannel: (channelId:string) => update(()=> { const row=store.channels.find(x=>x.id===channelId)!; row.status='verifying'; setTimeout(()=>{row.status=row.errorDetail?'error':'connected';row.lastCheckedAt=new Date().toISOString();notify();},1400); return row; }),
  /** Request another number. It arrives as a setup task on our side. */
  addChannel: (orgId:string,type:ChannelType,label:string) => LIVE
    ? notSupported('Your numbers come from the accounts you connect, so there is nothing to add here. Open Settings to connect WhatsApp or a phone line.')
    : update(()=> {
    const row:Channel={id:'ch-'+id(),orgId,type,label,isPrimary:!store.channels.some(c=>c.orgId===orgId&&c.type===type),status:'disconnected',provider:type==='sim'?'Infobip':'Meta',config:{}};
    store.channels.push(row);
    store.provisioning.unshift({id:'prov-'+id(),orgId,channelId:row.id,kind:type,state:'requested',note:'',updatedAt:new Date().toISOString()});
    logAudit(orgId,'Requested another number: '+label,'Channel',row.id);
    return row; }),
  renameChannel: (channelId:string,label:string) => LIVE
    ? notSupported('Channel names come from the provider and cannot be changed here.')
    : update(()=> { const row=store.channels.find(x=>x.id===channelId)!; row.label=label; return row; }),
  /** The three things a company decides about a number. */
  setChannelCapability: (channelId:string,key:'inboundEnabled'|'outboundEnabled'|'operatorEnabled',on:boolean) => LIVE
    ? notSupported('What a channel can do follows from the credentials you have entered for it.')
    : update(()=> {
    const row=store.channels.find(x=>x.id===channelId)!; row.config={...row.config,[key]:on};
    logAudit(row.orgId,(on?'Enabled ':'Disabled ')+key.replace('Enabled','')+' on '+row.label,'Channel',channelId); return row; }),
  setPrimaryChannel: (orgId:string,channelId:string) => LIVE
    ? notSupported('There is no primary number to choose: each channel is used for what it is connected to.')
    : update(()=> {
    const target=store.channels.find(x=>x.id===channelId)!;
    store.channels.filter(c=>c.orgId===orgId&&c.type===target.type).forEach(c=>{c.isPrimary=c.id===channelId});
    return target; }),
  removeChannel: (channelId:string) => LIVE
    ? notSupported('A number is removed by clearing its credentials in Settings.')
    : update(()=> {
    const row=store.channels.find(x=>x.id===channelId); if(!row) return false;
    store.agents.filter(a=>a.orgId===row.orgId).forEach(a=>{a.channelIds=a.channelIds.filter(x=>x!==channelId)});
    store.callSetups=store.callSetups.filter(r=>r.channelId!==channelId);
    store.channels=store.channels.filter(x=>x.id!==channelId);
    store.provisioning=store.provisioning.filter(x=>x.channelId!==channelId);
    logAudit(row.orgId,'Removed the number '+row.label,'Channel',channelId); return true; }),
  /** Which agents, rules and campaigns depend on a number. */
  channelUsage: (channelId:string) => LIVE
    ? notSupported('Per-channel usage is not measured yet.')
    : mock(()=>({
    agents: store.agents.filter(a=>a.channelIds.includes(channelId)).map(a=>a.name),
    rules: store.callSetups.filter(r=>r.channelId===channelId).map(r=>r.name),
    campaigns: store.campaigns.filter(c=>{const sp=store.callSetups.find(x=>x.id===c.setupId);return sp?.channelId===channelId}).map(c=>c.name),
  })),
  getCredentials: (channelId:string) => LIVE
    ? real(async()=>{ await hydrateCompany(); return clone(store.credentials.filter(x=>x.channelId===channelId)); })
    : mock(()=>store.credentials.filter(x=>x.channelId===channelId)),
  deleteCredential: (credId:string) => LIVE
    ? real(async()=>{
        // The id carries which field it is: "<phoneNumberId>:<keyName>".
        // An empty string is how the API is told to clear a credential,
        // which is a different instruction from leaving it unmentioned.
        const keyName=credId.split(':').slice(1).join(':');
        if(!keyName) return false;
        await live.updateCompany({[keyName]:''});
        await hydrateCompany();
        return true;
      })
    : update(()=> {
    const row=store.credentials.find(x=>x.id===credId); if(!row) return false;
    store.credentials=store.credentials.filter(x=>x.id!==credId);
    logAudit(row.orgId,'Removed '+row.keyName,'Credential',credId); return true; }),
  /**
   * Store one credential. `value` is the real secret, not a fragment of it:
   * the server is where it gets sealed, and the mock keeps only the last four
   * because a prototype has nowhere safe to put the rest.
   */
  setCredential: (orgId:string,channelId:string,keyName:string,value:string) => LIVE
    ? real(async()=>{ await live.updateCompany({[keyName]:value}); await hydrateCompany();
        return clone(store.credentials.find(x=>x.channelId===channelId&&x.keyName===keyName)); })
    : update(()=> {
    const lastFour=value.slice(-4);
    let row=store.credentials.find(x=>x.channelId===channelId&&x.keyName===keyName);
    if(row){Object.assign(row,{lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()});}
    else {row={id:'cred-'+id(),orgId,channelId,keyName,lastFour,setBy:store.profile.fullName,setAt:new Date().toISOString()};store.credentials.push(row);}
    logPlatform('Replaced credential',keyName+' for channel '+channelId,orgId); return row; }),

  // ---- Calls --------------------------------------------------------------
  getCalls: (orgId:string) => LIVE
    ? real(async()=>{ await hydrateCalls(); return clone(store.calls.filter(x=>x.orgId===orgId)); })
    : mock(()=>store.calls.filter(x=>x.orgId===orgId)),
  getCall: (callId:string) => LIVE
    ? real(async()=>live.call(callId))
    : mock(()=>store.calls.find(x=>x.id===callId)),
  /**
   * The backend keeps a transcript as one block of text, not the turn-by-turn
   * rows this app renders, because nothing in this build produces turns yet.
   * It is returned as a single entry rather than parsed into speakers: an
   * invented split would read as though we knew who said what.
   */
  getTranscript: (callId:string) => LIVE
    ? real(async()=>{
        const body=await live.transcript(callId);
        if(!body.transcript) return [];
        return [{id:callId+'-transcript',callId,role:'agent' as const,speaker:'Transcript',
                 text:body.transcript,timestamp:''}];
      })
    : mock(()=>store.transcripts.filter(x=>x.callId===callId)),
  getRagQueries: (callId:string) => mock(()=>store.ragQueries.filter(x=>x.callId===callId)),
  endCall: (callId:string) => LIVE
    ? real(async()=>{ const row=await live.endCall(callId); await hydrateCalls(); return row; })
    : update(()=> { const row=store.calls.find(x=>x.id===callId)!; row.status='completed';row.endedAt=new Date().toISOString();row.recordingState='PENDING';return row; }),
  takeOverCall: (callId:string,userId?:string) => update(()=> { const row=store.calls.find(x=>x.id===callId)!; row.mode='operator'; if(userId)row.assignedToUserId=userId; return row; }),
  /** Place a call. `knowledgeBaseId` is the explicit per-call choice. */
  createTestCall: (orgId:string,agentId:string,phoneNumber:string,knowledgeBaseId?:string,fromChannelId?:string) => update(()=> {
    const agent=store.agents.find(a=>a.id===agentId);
    const channel=store.channels.find(c=>c.id===(fromChannelId||agent?.channelIds[0]))||store.channels.find(c=>c.orgId===orgId&&c.isPrimary)||store.channels.find(c=>c.orgId===orgId)!;
    const resolved=resolveKnowledge(orgId,agentId,knowledgeBaseId,channel.id);
    const row:Call={id:'call-'+id(),orgId,agentId,channelId:channel.id,channelType:channel.type,direction:'OUTBOUND',phoneNumber,fromNumber:channel.displayNumber||'',toNumber:phoneNumber,status:'active',mode:'agent',startedAt:new Date().toISOString(),durationSeconds:0,outcome:'Test call',summary:'Test call in progress.',recordingState:'RECORDING',recordingScope:'two_way',knowledgeBaseId:resolved.kbId,knowledgeBaseName:resolved.kbName,resolvedBy:resolved.resolvedBy};
    store.calls.unshift(row);
    setTimeout(()=>{row.status='completed';row.endedAt=new Date().toISOString();row.durationSeconds=18;row.recordingState='PENDING';row.summary='The test call completed. It went out from '+(channel.label||'your number')+' and the agent answered from '+resolved.kbName+'.';notify();},6500);
    return row; }),

  // ---- Outbound campaigns -------------------------------------------------
  getCampaigns: (orgId:string) => LIVE
    ? real(async()=>(await live.campaigns()).campaigns)
    : mock(()=>store.campaigns.filter(x=>x.orgId===orgId)),
  getCampaign: (campaignId:string) => LIVE
    ? real(()=>live.campaign(campaignId))
    : mock(()=>store.campaigns.find(x=>x.id===campaignId)),
  getCampaignContacts: (campaignId:string) => mock(()=>store.campaignContacts.filter(x=>x.campaignId===campaignId)),
  createCampaign: (orgId:string,patch:Partial<Campaign>) => update(()=> {
    const row:Campaign={id:'camp-'+id(),orgId,name:patch.name||'Untitled campaign',setupId:patch.setupId||store.callSetups.find(x=>x.orgId===orgId&&x.direction==='OUTBOUND')?.id||'',status:'draft',total:patch.total||0,attempted:0,connected:0,unanswered:0,windowStart:patch.windowStart||'09:00',windowEnd:patch.windowEnd||'17:00',maxAttempts:patch.maxAttempts||2,retryAfterMinutes:patch.retryAfterMinutes||240,createdAt:new Date().toISOString()};
    store.campaigns.unshift(row); logAudit(orgId,'Created the campaign '+row.name,'Campaign',row.id); return row; }),
  updateCampaign: (campaignId:string,patch:Partial<Campaign>) => update(()=> { const row=store.campaigns.find(x=>x.id===campaignId)!; Object.assign(row,patch); return row; }),
  deleteCampaign: (campaignId:string) => update(()=> {
    const row=store.campaigns.find(x=>x.id===campaignId); if(!row) return false;
    store.campaigns=store.campaigns.filter(x=>x.id!==campaignId);
    store.campaignContacts=store.campaignContacts.filter(x=>x.campaignId!==campaignId);
    logAudit(row.orgId,'Deleted the campaign '+row.name,'Campaign',campaignId); return true; }),
  setCampaignStatus: (campaignId:string,status:Campaign['status']) => update(()=> { const row=store.campaigns.find(x=>x.id===campaignId)!; row.status=status; logAudit(row.orgId,'Set the campaign '+row.name+' to '+status,'Campaign',row.id); return row; }),

  // ---- The improvement loop ----------------------------------------------
  getUnanswered: (orgId:string) => LIVE
    ? real(async()=>(await live.gaps()).gaps)
    : mock(()=>store.unanswered.filter(x=>x.orgId===orgId)),
  resolveUnanswered: (questionId:string,status:'answered'|'ignored'|'open') => update(()=> { const row=store.unanswered.find(x=>x.id===questionId)!; row.status=status; return row; }),

  // ---- Messaging ----------------------------------------------------------
  getMessages: (orgId:string) => LIVE
    ? real(async()=>{ await hydrateMessages(); return clone(store.messages.filter(x=>x.orgId===orgId)); })
    : mock(()=>store.messages.filter(x=>x.orgId===orgId)),
  sendMessage: (orgId:string,toNumber:string,body:string,templateId?:string) => LIVE
    ? real(async()=>{
        // A template is the only thing that reaches someone who has not
        // written in the last 24 hours, so the two are different endpoints
        // rather than one with a flag.
        const row=templateId ? await live.sendTemplate(toNumber,templateId) : await live.sendText(toNumber,body);
        await hydrateMessages();
        return row;
      })
    : update(()=> { const row:Message={id:'msg-'+id(),orgId,toNumber,body,templateId,status:'sent',sentAt:new Date().toISOString()};store.messages.unshift(row);return row; }),
  /** Read straight from Meta each time: approval status changes on their schedule. */
  getTemplates: (orgId:string) => LIVE
    ? real(async()=>{ const rows=await live.templates(orgId); store.templates=rows; notify(); return clone(rows); })
    : mock(()=>store.templates.filter(x=>x.orgId===orgId)),

  // ---- Billing and admin --------------------------------------------------
  /** Recordings are copied to the company's own Drive folder. */
  /**
   * Connecting Drive is an OAuth round trip, so this cannot complete here: it
   * returns the Google consent URL and the caller sends the browser there.
   * The company approves in their own Google account and comes back to
   * `?drive=connected`.
   */
  connectDrive: (orgId:string,account:string,folderName:string):Promise<Organization|{redirectTo:string}|undefined> => LIVE
    ? real(async()=>{ const {url}=await live.connectDrive(window.location.href); return {redirectTo:url}; })
    : update(()=> {
    const row=store.organizations.find(x=>x.id===orgId)!;
    Object.assign(row,{driveConnected:true,driveAccount:account,driveFolderName:folderName});
    logAudit(orgId,'Connected Google Drive for recordings','Organization',orgId); return row; }),
  disconnectDrive: (orgId:string) => LIVE
    ? real(async()=>{ await live.disconnectDrive(); await hydrateCompany();
        return clone(store.organizations.find(x=>x.id===orgId)!); })
    : update(()=> {
    const row=store.organizations.find(x=>x.id===orgId)!;
    Object.assign(row,{driveConnected:false,driveAccount:undefined});
    logAudit(orgId,'Disconnected Google Drive','Organization',orgId); return row; }),
  /** A person places the call themselves, from one of the company's numbers. */
  createOperatorCall: (orgId:string,channelId:string,phoneNumber:string,userId:string) => update(()=> {
    const channel=store.channels.find(c=>c.id===channelId)!;
    const row:Call={id:'call-'+id(),orgId,agentId:'',channelId,channelType:channel.type,direction:'OUTBOUND',phoneNumber,fromNumber:channel.displayNumber||'',toNumber:phoneNumber,status:'active',mode:'operator',startedAt:new Date().toISOString(),durationSeconds:0,outcome:'In progress',summary:'A person is on this call.',recordingState:'RECORDING',recordingScope:'two_way',knowledgeBaseId:'',knowledgeBaseName:'Not used \u2014 a person handled this',resolvedBy:'explicit',assignedToUserId:userId};
    store.calls.unshift(row);
    logAudit(orgId,'Placed a call from '+channel.label,'Call',row.id);
    return row; }),
  /** Counted from the call and message logs, not from a separate meter. */
  getUsage: (orgId:string) => LIVE
    ? real(()=>live.usage())
    : mock(()=>store.usage.filter(x=>x.orgId===orgId)),
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
  /**
   * Every company, for the operator's portal. Only real with an admin key:
   * a company's own key cannot list other companies, which is the point.
   */
  getPlatformCompanies: () => LIVE
    ? real(async()=>{
        if(!hasAdminKey()) return [];
        const {companies}=await live.companies();
        return companies.map(c=>({
          org: toOrganization(c),
          minutesThisPeriod: 0,
          callCount: 0,
          lastCallAt: undefined,
          health: (c.configured?'ok':'warning') as 'ok'|'warning'|'error',
          openIssues: c.configured?0:1,
        }));
      })
    : mock(()=>store.organizations.map(org=>{
    const calls=store.calls.filter(c=>c.orgId===org.id);
    const failing=store.channels.filter(c=>c.orgId===org.id&&c.status==='error').length;
    const blocked=store.provisioning.filter(p=>p.orgId===org.id&&p.state==='blocked').length;
    return { org, minutesThisPeriod: Math.round(calls.reduce((n,c)=>n+c.durationSeconds,0)/60),
      callCount: calls.length, lastCallAt: calls[0]?.startedAt,
      health: (failing+blocked)>0?(blocked>0?'error':'warning'):'ok' as 'ok'|'warning'|'error',
      openIssues: failing+blocked };
  })),
  /** Register a company. The API key it returns is shown once and never again. */
  registerCompany: (body:Record<string,unknown>) => LIVE
    ? real(()=>live.registerCompany(body))
    : notSupported('Registering a company needs the live API.'),
  rotateCompanyKey: (phoneNumberId:string) => LIVE
    ? real(()=>live.rotateCompanyKey(phoneNumberId))
    : notSupported('Rotating a key needs the live API.'),
  createCompany: (name:string,countryCode:string) => LIVE
    ? notSupported('Use Register a company, which needs the WhatsApp phone number id.')
    : update(()=> {
    const org:Organization={id:'org-'+id(),name,slug:name.toLowerCase().replace(/\W+/g,'-'),accentColor:'oklch(0.58 0.13 245)',plan:'Starter',countryCode,defaultLanguage:'ur',timezone:'Asia/Karachi',createdAt:new Date().toISOString(),trialEndsAt:new Date(Date.now()+14*86400000).toISOString(),onboardingStep:0,status:'setup',provisioningMode:'managed',strictnessPreset:'balanced',pacePreset:'natural',voiceQualityPreset:'standard',recordCalls:false,recordBothSides:false,retentionDays:30,driveConnected:false,driveFolderName:'Call recordings',endCallsAfterMinutes:15,businessHoursStart:'09:00',businessHoursEnd:'17:00'};
    store.organizations.push(org); store.settings[org.id]={};
    logPlatform('Created company',name,org.id); return org; }),
  setCompanyStatus: (orgId:string,status:Organization['status']) => LIVE
    ? notSupported('Suspending a company is not available yet.')
    : update(()=> { const row=store.organizations.find(x=>x.id===orgId)!; row.status=status; logPlatform('Set company status',row.name+' is now '+status,orgId); return row; }),
  getProvisioning: (orgId?:string) => mock(()=>orgId?store.provisioning.filter(x=>x.orgId===orgId):store.provisioning),
  updateProvisioning: (taskId:string,patch:Partial<ProvisioningTask>) => update(()=> {
    const row=store.provisioning.find(x=>x.id===taskId)!; Object.assign(row,patch,{updatedAt:new Date().toISOString()});
    if(patch.state==='active'){ const ch=store.channels.find(c=>c.id===row.channelId); if(ch){ch.status='connected';ch.lastCheckedAt=new Date().toISOString();} }
    logPlatform('Updated setup',row.kind+' is now '+row.state,row.orgId); return row; }),
  assignNumber: (orgId:string,channelId:string,number:string) => update(()=> {
    const ch=store.channels.find(c=>c.id===channelId); if(ch){ch.displayNumber=number;ch.status='verifying';}
    logPlatform('Assigned number',number+' to '+(ch?.label||channelId),orgId); return ch; }),
  getPlatformAudit: () => mock(()=>store.platformAudit),
  startImpersonation: (orgId:string) => LIVE
    ? notSupported('Viewing a company as them is not available yet.')
    : update(()=> { logPlatform('Started impersonation','Opened '+(store.organizations.find(x=>x.id===orgId)?.name||orgId)+' read-only',orgId); return true; }),
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
  deleteOrganization: (orgId:string) => LIVE
    ? notSupported('Closing an account is done by the operator, not from here.')
    : update(()=> {
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
    store.callSetups=store.callSetups.filter(x=>x.orgId!==orgId);
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
  // ---- Knowledge ----------------------------------------------------------
  // Only real against the backend: the fixtures model knowledge as several
  // bases with chunks and scores, which is a different shape from the one
  // document list the API actually has.
  getKnowledgeDocuments: () => LIVE
    ? real(()=>live.knowledge())
    : mock(()=>({documents:[],totalChars:0,limitChars:120000})),
  uploadKnowledgeDocument: (file:File) => LIVE
    ? real(()=>live.uploadKnowledge(file))
    : mock(()=>({id:'doc',name:file.name,chars:0})),
  deleteKnowledgeDocument: (documentId:string) => LIVE
    ? real(()=>live.deleteKnowledge(documentId))
    : mock(()=>undefined),
  previewKnowledge: () => LIVE
    ? real(()=>live.knowledgePreview())
    : mock(()=>({chars:0,limitChars:120000,preview:'',truncated:false})),

  /** The agent's own settings: who it is, how it answers, what it records. */
  getAgentSettings: () => LIVE
    ? real(async()=>{ const c=await live.company(); return {
        persona:c.persona??'', greeting:c.agentGreeting??'', language:c.language??'',
        voice:c.ttsVoice??'', autoReply:c.autoReply, recordCalls:c.recordCalls,
        webhookUrl:c.webhookUrl, configured:c.configured }; })
    : mock(()=>({persona:'',greeting:'',language:'',voice:'',autoReply:false,
                 recordCalls:true,webhookUrl:'',configured:false})),
  updateAgentSettings: (patch:Record<string,unknown>) => LIVE
    ? real(async()=>{ await live.updateCompany(patch); await hydrateCompany(); return true; })
    : update(()=>true),

  /**
   * Place a call. WhatsApp needs the person's permission first; a phone line
   * needs the company's own carrier credentials.
   */
  placeCall: (to:string,channel:'PHONE'|'WHATSAPP_CALL') => LIVE
    ? real(async()=>{ const c=await live.placeCall(to,channel); await hydrateCalls(); return c; })
    : notSupported('Placing a call needs the live API.'),
  askCallPermission: (to:string) => LIVE
    ? real(()=>live.askCallPermission(to))
    : notSupported('Asking for call permission needs the live API.'),

  /** Questions the agent had to defer, which is what to upload next. */
  getGaps: () => LIVE ? real(()=>live.gaps()) : mock(()=>null),
  getCampaignDetail: (id:string) => LIVE
    ? real(()=>live.campaign(id))
    : mock(()=>store.campaigns.find(c=>c.id===id)),
  createCampaignLive: (body:Record<string,unknown>) => LIVE
    ? real(()=>live.createCampaign(body))
    : notSupported('Creating a campaign needs the live API.'),
  updateCampaignLive: (id:string,body:Record<string,unknown>) => LIVE
    ? real(()=>live.updateCampaign(id,body))
    : notSupported('Editing a campaign needs the live API.'),
  startCampaign: (id:string) => LIVE
    ? real(()=>live.startCampaign(id))
    : notSupported('Starting a campaign needs the live API.'),
  pauseCampaign: (id:string) => LIVE
    ? real(()=>live.pauseCampaign(id))
    : notSupported('Pausing a campaign needs the live API.'),
  deleteCampaignLive: (id:string) => LIVE
    ? real(()=>live.deleteCampaign(id))
    : notSupported('Deleting a campaign needs the live API.'),

  /** Live calls, for the screen that lets a person take one over. */
  getLiveCalls: () => LIVE
    ? real(()=>live.liveCalls())
    : mock(()=>({calls:[],total:0})),
  handBackCall: (callId:string) => LIVE
    ? real(()=>live.handBack(callId))
    : mock(()=>({handledBy:'agent'})),

  /**
   * A URL that will actually play. Mock builds already hold a playable one on
   * the call; live builds mint a short-lived token, because a media element
   * cannot present a bearer token.
   */
  playbackUrl: (callId:string) => LIVE
    ? real(()=>live.playbackUrl(callId))
    : mock(()=>store.calls.find(x=>x.id===callId)?.recordingUrl||''),
  /** Pull the carrier's copy of a recording into the company's own storage. */
  fetchRecording: (callId:string) => LIVE
    ? real(async()=>{ const result=await live.fetchRecording(callId); await hydrateCalls(); return result; })
    : mock(()=>({status:'stored'})),
  downloadRecording: (callId:string) => LIVE
    ? real(()=>live.downloadRecording(callId))
    : mock(()=>undefined),
  uploadRecording: (callId:string,audio:Blob) => LIVE
    ? real(async()=>{ const r=await live.uploadRecording(callId,audio); await hydrateCalls(); return r; })
    : mock(()=>({status:'stored',bytes:0})),
  reportRecordingProblem: (callId:string,reason:string,ok=false) => LIVE
    ? real(()=>live.reportRecordingProblem(callId,reason,ok))
    : mock(()=>({status:'noted'})),
  downloadCallsWorkbook: () => LIVE
    ? real(()=>live.downloadWorkbook())
    : mock(()=>undefined),
  /** Refresh the Google Sheet copy of the workbook and return its link. */
  syncDriveReport: () => LIVE
    ? real(()=>live.syncReport())
    : mock(()=>({url:''})),
  snapshot: () => clone(store)
};
