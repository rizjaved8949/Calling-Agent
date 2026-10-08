/**
 * Check the mapping layer against real API responses.
 *
 * `live.ts` is the one place where a mistake is invisible: a wrong field name
 * produces `undefined`, React renders nothing, and the screen looks merely
 * empty rather than broken. So the mappers are run over payloads captured from
 * the running backend and the results are asserted field by field.
 *
 * Uses esbuild (already present, as Vite's own dependency) rather than adding a
 * test runner, so this works with nothing installed.
 *
 *   node scripts/check-mapping.mjs live-payloads.json
 */
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const payloadFile = process.argv[2] ?? 'live-payloads.json';
const payloads = JSON.parse(readFileSync(payloadFile, 'utf8'));

const bundle = 'scripts/.mapping-bundle.mjs';
await build({
  entryPoints: ['src/lib/api/live.ts'],
  bundle: true,
  format: 'esm',
  platform: 'node',
  outfile: bundle,
  logLevel: 'silent',
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify('http://localhost:8000'),
    'import.meta.env': JSON.stringify({ VITE_API_URL: 'http://localhost:8000' }),
  },
  // The browser globals the module touches at import time.
  banner: { js: 'globalThis.localStorage ??= {getItem:()=>null,setItem(){},removeItem(){}};\nglobalThis.window ??= {location:{origin:"http://localhost:5173"}};' },
});

// Resolved from the working directory, not from this file's URL:
// a Windows path through new URL() picks up a drive-letter prefix.
const live = await import(pathToFileURL(resolve(bundle)).href);

let failures = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures++;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${ok ? '' : `\n        expected ${JSON.stringify(expected)}\n        got      ${JSON.stringify(actual)}`}`);
}
function present(label, value) {
  const ok = value !== undefined && value !== null && value !== '';
  if (!ok) failures++;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${ok ? ` = ${JSON.stringify(value)}` : ' is empty'}`);
}

const { company, calls, messages } = payloads;

console.log('\n-- organisation ------------------------------------------------');
const org = live.toOrganization(company);
check('id is the Meta phone number id', org.id, company.phoneNumberId);
present('name', org.name);
check('status reflects configuration', org.status, company.configured ? 'active' : 'setup');
check('recordCalls carries over', org.recordCalls, company.recordCalls);
check('driveConnected carries over', org.driveConnected, company.googleDrive.connected);
present('defaultLanguage', org.defaultLanguage);

console.log('\n-- channels ----------------------------------------------------');
const channels = live.toChannels(company);
check('one row per channel', channels.length, company.channels.length);
for (const channel of channels) {
  const source = company.channels.find((c) => c.id === channel.id);
  check(`${channel.id}: status`, channel.status, source.connected ? 'connected' : (source.missing.length === source.required.length ? 'disconnected' : 'pending'));
  present(`${channel.id}: label`, channel.label);
  present(`${channel.id}: provider`, channel.provider);
}

console.log('\n-- credentials -------------------------------------------------');
const credentials = live.toCredentials(company);
const expectedSet = Object.entries(company.credentials).filter(([name, s]) => s.set && name !== 'phoneNumberId').length;
console.log(`      ${credentials.length} rows for ${expectedSet} set credentials`);
for (const credential of credentials) {
  present(`${credential.keyName}: hint`, credential.lastFour);
  // Only secrets must be abbreviated. A base URL or a WABA id is an
  // identifier the company needs to read back in full to check it.
  if (company.credentials[credential.keyName]?.secret && !credential.lastFour.startsWith('…')) {
    failures++;
    console.log(`FAIL  ${credential.keyName} is a secret but is not abbreviated`);
  }
}

console.log('\n-- calls -------------------------------------------------------');
for (const wire of calls.calls) {
  const call = live.toCall(wire);
  present(`${wire.id.slice(0, 8)}: startedAt is ISO`, call.startedAt);
  const parsed = Date.parse(call.startedAt);
  if (Number.isNaN(parsed)) { failures++; console.log('FAIL  startedAt did not parse'); }
  present(`${wire.id.slice(0, 8)}: phoneNumber`, call.phoneNumber);
  check(`${wire.id.slice(0, 8)}: channelType`, call.channelType, wire.channel === 'PHONE' || wire.channel === 'BROWSER' ? 'sim' : 'whatsapp_call');
  const expectStatus = ['QUEUED', 'RINGING', 'IN_PROGRESS'].includes(wire.status) ? 'active'
    : ['COMPLETED', 'HANDED_OFF'].includes(wire.status) ? 'completed' : 'failed';
  check(`${wire.id.slice(0, 8)}: status ${wire.status}`, call.status, expectStatus);
  if (wire.recording.available) {
    check(`${wire.id.slice(0, 8)}: recordingState`, call.recordingState, 'READY');
    // Must stay unset: an <audio src> cannot send a bearer token, so a URL
    // here would 401 and the player would silently never play. The screen
    // asks for a short-lived link instead.
    check(`${wire.id.slice(0, 8)}: no unauthenticated recordingUrl`, call.recordingUrl, undefined);
  }
}

console.log('\n-- messages ----------------------------------------------------');
for (const wire of messages.messages) {
  const message = live.toMessage(wire);
  present(`${wire.id.slice(0, 8)}: toNumber`, message.toNumber);
  present(`${wire.id.slice(0, 8)}: sentAt is ISO`, message.sentAt);
  if (Number.isNaN(Date.parse(message.sentAt))) { failures++; console.log('FAIL  sentAt did not parse'); }
  check(`${wire.id.slice(0, 8)}: status`, message.status,
    ['delivered', 'read', 'failed'].includes(wire.status) ? wire.status : 'sent');
}

unlinkSync(bundle);
console.log(`\n${failures === 0 ? 'All mappings check out.' : `${failures} mapping failure(s).`}\n`);
process.exit(failures === 0 ? 0 : 1);
