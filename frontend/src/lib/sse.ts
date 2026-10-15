/**
 * Shared EventSource connections. Several islands on one page can subscribe to
 * the same URL; the socket opens on first subscriber and closes on the last.
 * EventSource reconnects by itself (server sends ``retry:``) and resends
 * Last-Event-ID, so the stream resumes without gaps.
 */
type Handler = (data: unknown) => void;
type Channel = {
  source: EventSource;
  handlers: Map<string, Set<Handler>>;
  status: Set<(open: boolean) => void>;
  refs: number;
};

const channels = new Map<string, Channel>();

function open(url: string): Channel {
  const existing = channels.get(url);
  if (existing) return existing;
  const source = new EventSource(url, { withCredentials: true });
  const channel: Channel = { source, handlers: new Map(), status: new Set(), refs: 0 };
  source.onopen = () => channel.status.forEach((fn) => fn(true));
  source.onerror = () => channel.status.forEach((fn) => fn(source.readyState === EventSource.OPEN));
  channels.set(url, channel);
  return channel;
}

export function subscribe(
  url: string,
  events: Record<string, Handler>,
  onStatus?: (open: boolean) => void,
): () => void {
  const channel = open(url);
  channel.refs += 1;
  const listeners: [string, EventListener][] = [];
  for (const [name, fn] of Object.entries(events)) {
    let set = channel.handlers.get(name);
    if (!set) {
      set = new Set();
      channel.handlers.set(name, set);
      const dispatch = (ev: Event) => {
        let data: unknown = undefined;
        try {
          data = JSON.parse((ev as MessageEvent<string>).data);
        } catch {
          return;
        }
        channel.handlers.get(name)?.forEach((h) => h(data));
      };
      channel.source.addEventListener(name, dispatch);
      listeners.push([name, dispatch]);
    }
    set.add(fn);
  }
  if (onStatus) {
    channel.status.add(onStatus);
    onStatus(channel.source.readyState === EventSource.OPEN);
  }
  return () => {
    for (const [name, fn] of Object.entries(events)) channel.handlers.get(name)?.delete(fn);
    if (onStatus) channel.status.delete(onStatus);
    channel.refs -= 1;
    if (channel.refs <= 0) {
      channel.source.close();
      channels.delete(url);
    }
  };
}
