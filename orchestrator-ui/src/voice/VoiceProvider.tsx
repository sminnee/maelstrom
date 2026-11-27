import { useMemo, useState, type ReactNode } from 'react';
import { browserVoiceDevices, type VoiceDevices } from './devices';
import { VoiceContext } from './voiceContext';

/** Voice is off on each page load: which session has it lives in memory only. */
export function VoiceProvider({
  devices,
  children,
}: {
  devices?: VoiceDevices;
  children: ReactNode;
}) {
  const [own] = useState(() => devices ?? browserVoiceDevices());
  const [active, setActive] = useState<string | null>(null);
  const hub = useMemo(() => ({ devices: own, active, setActive }), [own, active]);
  return <VoiceContext.Provider value={hub}>{children}</VoiceContext.Provider>;
}
