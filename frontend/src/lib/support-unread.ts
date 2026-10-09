/**
 * How many support messages are waiting, for the badge in the sidebar.
 *
 * A live chat nobody notices is a slow chat. The thread screen knows its own
 * unread count, but by the time you are looking at it the question is already
 * answered — the count has to be somewhere you see without going there.
 *
 * One socket for the whole app, opened once by the shell and shared through a
 * tiny store rather than a context, so a nav badge does not drag a provider
 * and a re-render of every page behind it. The company watches its own thread;
 * the operator watches the lobby, which reports every company.
 */
import { supportApi, SupportSocket, type SocketEvent } from './api/support';

type Listener = (count: number) => void;

let count = 0;
let listeners: Listener[] = [];
let socket: SupportSocket | null = null;
let started = false;
/** Set while the chat screen is open, so it is not also counted as unread. */
let openThread: string | null = null;

function announce(): void {
  for (const listener of listeners) listener(count);
}

export function unreadCount(): number {
  return count;
}

export function subscribe(listener: Listener): () => void {
  listeners.push(listener);
  listener(count);
  return () => {
    listeners = listeners.filter(other => other !== listener);
  };
}

/** Called by the chat screen: what is on screen is not waiting. */
export function markThreadOpen(tenantId: string | null): void {
  openThread = tenantId;
  if (tenantId !== null) {
    count = 0;
    announce();
  } else {
    void refresh();
  }
}

async function refresh(): Promise<void> {
  try {
    if (operator) {
      const { threads } = await supportApi.threads();
      count = threads.reduce((total, row) =>
        total + (row.tenantId === openThread ? 0 : row.unreadForOperator), 0);
    } else {
      if (openThread !== null) return;
      const { thread } = await supportApi.conversation();
      count = thread.unreadForCompany;
    }
    announce();
  } catch {
    // A failed count is a missing badge, which is the right failure: it must
    // never be the reason a page does not load.
  }
}

let operator = false;

export function start(asOperator: boolean): void {
  if (started) return;
  started = true;
  operator = asOperator;
  void refresh();

  socket = new SupportSocket(
    async () => (await supportApi.ticket(asOperator ? '' : undefined)).ticket,
    (event: SocketEvent) => {
      if (event.type === 'message') {
        const mine = asOperator ? 'operator' : 'company';
        // Your own message is not a notification, and neither is one in the
        // thread you are reading.
        if (event.message.author === mine) return;
        if (openThread !== null && (openThread === '' || openThread === event.tenantId)) return;
        count += 1;
        announce();
      } else if (event.type === 'read' || event.type === 'deleted' || event.type === 'resync') {
        void refresh();
      }
    },
    () => undefined,
  );
  void socket.open();
}

export function stop(): void {
  socket?.close();
  socket = null;
  started = false;
  count = 0;
  listeners = [];
  openThread = null;
}
