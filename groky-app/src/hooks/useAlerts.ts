import { useEffect, useRef } from 'react';
import { DeviceId, devices } from '../devices';
import type { Line } from '../voice/types';
import { DeviceStatus, useDevices } from './useDevices';
import { useNotify } from './useNotify';

const LOW_BATTERY = 20;

// turns robot status changes and failed actions into notifications
export function useAlerts(lines: Line[]) {
  const { notify } = useNotify();
  const { status, ready } = useDevices();
  const prev = useRef<Record<DeviceId, DeviceStatus> | null>(null);
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (!ready) return;
    const before = prev.current;
    prev.current = status;
    for (const id of Object.keys(devices) as DeviceId[]) {
      const label = devices[id].label;
      const now = status[id];
      const was = before?.[id];

      if (!was) {
        if (now.state === 'offline')
          notify({ kind: 'warn', title: `${label} is offline`, body: 'Could not reach its bridge.' });
      } else if (was.state !== now.state) {
        if (now.state === 'offline') notify({ kind: 'bad', title: `${label} went offline`, body: devices[id].url });
        else if (was.state === 'offline')
          notify({ kind: 'ok', title: `${label} is back online`, body: now.ms != null ? `${now.ms} ms` : undefined });
      }

      const bat = typeof now.info?.battery === 'number' ? (now.info.battery as number) : null;
      const prevBat = typeof was?.info?.battery === 'number' ? (was.info.battery as number) : null;
      if (bat != null && bat < LOW_BATTERY && (prevBat == null || prevBat >= LOW_BATTERY)) {
        notify({ kind: 'warn', title: `${label} battery low`, body: `${bat}% left` });
      }
    }
  }, [status, ready, notify]);

  useEffect(() => {
    for (const l of lines) {
      if (seen.current.has(l.id)) continue;
      if (l.kind === 'error') {
        seen.current.add(l.id);
        notify({ kind: 'bad', title: 'Grok problem', body: l.text });
      } else if (l.kind === 'tool' && l.ok === false && l.name !== 'run_skill') {
        // run_skill failures are already reported by the skill runner
        seen.current.add(l.id);
        notify({
          kind: 'bad',
          title: `${l.name.replace(/_/g, ' ')} failed`,
          body: l.args && l.args !== '{}' ? l.args : undefined,
        });
      } else if (l.kind === 'tool' && l.ok === true && l.name === 'stop') {
        seen.current.add(l.id);
        notify({ kind: 'warn', title: 'Emergency stop sent', body: 'All robots told to halt.' });
      }
    }
  }, [lines, notify]);
}
