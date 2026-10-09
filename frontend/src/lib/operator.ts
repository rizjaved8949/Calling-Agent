/**
 * Taking a live call over from the browser.
 *
 * The call is already running on the server with the agent on it. This opens a
 * second socket, which makes the server stop the agent and route the caller to
 * whoever is at this microphone instead.
 *
 * Three things make this fiddly, and all three are handled here so the screen
 * does not have to think about them:
 *
 * * **The microphone is not 16 kHz.** A browser captures at whatever the
 *   device runs at — usually 44.1 or 48 kHz — and the call is 16 kHz. An
 *   AudioContext is asked for 16 kHz, and what it actually gives is checked
 *   rather than assumed, because browsers are free to ignore the request.
 * * **Audio is float, the wire is int16.** Converting needs clamping: a value
 *   slightly over 1.0 wraps to a loud crack rather than clipping quietly.
 * * **A websocket cannot send an Authorization header**, so the token is in
 *   the URL — short-lived and scoped to this one call.
 */
import {API_BASE} from './api/http';

/** What the call runs at. Everything here converts to and from this. */
const CALL_RATE = 16_000;
/** 20 ms, the frame size the server paces against. */
const FRAME_SAMPLES = CALL_RATE / 50;

export type OperatorCall = {
  /** Stop speaking and give the call back to the agent. */
  leave: () => void;
  /** True once the microphone is open and audio is going out. */
  readonly live: boolean;
};

function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    // Clamped before scaling: a sample over 1.0 would otherwise wrap from
    // loudest-positive to loudest-negative, which is heard as a crack.
    const sample = Math.max(-1, Math.min(1, input[i]));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

/** Nearest-neighbour resample. Good enough for speech at these ratios. */
function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i += 1) out[i] = input[Math.floor(i * ratio)];
  return out;
}

export async function joinCallAsOperator(
  callId: string,
  token: string,
  onEvent?: (event: {type: string; detail?: string}) => void,
): Promise<OperatorCall> {
  const say = (type: string, detail?: string) => onEvent?.({type, detail});

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
    });
  } catch (cause) {
    // The three causes have different fixes, and the browser distinguishes
    // them, so the message should too.
    const name = (cause as {name?: string})?.name ?? '';
    throw new Error(
      name === 'NotAllowedError' ? 'Microphone access was refused. Allow it for this site and try again.'
      : name === 'NotFoundError' ? 'No microphone was found.'
      : name === 'NotReadableError' ? 'The microphone is already in use by another application.'
      : 'The microphone could not be opened.',
    );
  }

  const base = API_BASE.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
  const socket = new WebSocket(
    `${base}/api/media/operator/${encodeURIComponent(callId)}?token=${encodeURIComponent(token)}`,
  );
  socket.binaryType = 'arraybuffer';

  const context = new AudioContext({sampleRate: CALL_RATE});
  const source = context.createMediaStreamSource(stream);
  // ScriptProcessor is deprecated but universally available; an AudioWorklet
  // needs a separate module file and buys nothing at this buffer size.
  const processor = context.createScriptProcessor(2048, 1, 1);
  const playback = context.createGain();
  playback.connect(context.destination);

  let open = false;
  let outgoing = new Float32Array(0);
  let nextPlayAt = 0;

  processor.onaudioprocess = event => {
    if (!open) return;
    const chunk = resample(event.inputBuffer.getChannelData(0), context.sampleRate, CALL_RATE);
    const joined = new Float32Array(outgoing.length + chunk.length);
    joined.set(outgoing);
    joined.set(chunk, outgoing.length);
    outgoing = joined;

    // Sent in whole 20 ms frames: a partial frame is a runt the server has to
    // buffer anyway, and sending them doubles the message count for nothing.
    while (outgoing.length >= FRAME_SAMPLES) {
      socket.send(floatToPcm16(outgoing.subarray(0, FRAME_SAMPLES)).buffer);
      outgoing = outgoing.slice(FRAME_SAMPLES);
    }
  };

  source.connect(processor);
  processor.connect(context.destination);

  socket.onopen = () => {open = true; say('joined')};
  socket.onerror = () => say('error', 'The connection to the call failed.');
  socket.onclose = () => {open = false; say('left')};

  socket.onmessage = event => {
    if (typeof event.data === 'string') {
      try {
        const body = JSON.parse(event.data);
        if (body.type === 'handover') say('handover', body.transcript);
      } catch {
        /* a text frame that is not ours */
      }
      return;
    }
    // The caller's voice. Scheduled back-to-back rather than played on
    // arrival, so network jitter does not become audible gaps.
    const pcm = new Int16Array(event.data as ArrayBuffer);
    const buffer = context.createBuffer(1, pcm.length, CALL_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 0x8000;
    const node = context.createBufferSource();
    node.buffer = buffer;
    node.connect(playback);
    const now = context.currentTime;
    nextPlayAt = Math.max(nextPlayAt, now + 0.05);
    node.start(nextPlayAt);
    nextPlayAt += buffer.duration;
  };

  return {
    get live() {return open},
    leave() {
      open = false;
      try {socket.close()} catch {/* already closing */}
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach(track => track.stop());
      void context.close();
    },
  };
}
