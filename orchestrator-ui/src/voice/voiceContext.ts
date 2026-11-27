import { createContext } from 'react';
import type { VoiceDevices } from './devices';

/** The app-wide side of **Voice mode**: the devices, and which session view has voice. */
export interface VoiceHub {
  devices: VoiceDevices;
  /**
   * The token of the one session view that has voice on, or `null`. One mic
   * and one speaker, so turning voice on in one view turns it off in the others.
   */
  active: string | null;
  setActive(viewId: string | null): void;
}

export const VoiceContext = createContext<VoiceHub | null>(null);
