import { createContext, ReactNode, useContext, useEffect, useState } from 'react';
import { devices, DeviceId, Health, ping } from '../devices';

export type DeviceStatus = Health & { checkedAt?: number; lastOnline?: number };

const ids = Object.keys(devices) as DeviceId[];
const initial = Object.fromEntries(ids.map((id) => [id, { state: 'offline' }])) as Record<DeviceId, DeviceStatus>;

const Ctx = createContext<{ status: Record<DeviceId, DeviceStatus>; ready: boolean }>({
  status: initial,
  ready: false,
});

export function DevicesProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState(initial);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      const res = await Promise.all(ids.map(ping));
      if (!alive) return;
      const now = Date.now();
      setStatus(
        (prev) =>
          Object.fromEntries(
            ids.map((id, i) => [
              id,
              { ...res[i], checkedAt: now, lastOnline: res[i].state === 'offline' ? prev[id].lastOnline : now },
            ]),
          ) as Record<DeviceId, DeviceStatus>,
      );
      setReady(true);
    };
    check();
    const t = setInterval(check, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return <Ctx.Provider value={{ status, ready }}>{children}</Ctx.Provider>;
}

export const useDevices = () => useContext(Ctx);
