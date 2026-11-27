import type { Recording, VoiceDevices } from '../voice/devices';

/**
 * A microphone and a speaker a test drives. The fake server's speech holds
 * the text it read, so `played` says what the user heard.
 */
export interface FakeVoiceDevices extends VoiceDevices {
  /** Whether a clip is being recorded now. */
  recording: boolean;
  /** The text of each reply played so far, in order. */
  played: string[];
  /** Whether a reply plays now. */
  playing: boolean;
  /** How many times a play was stopped. */
  stops: number;
  /** End the reply that plays, as its last sample would. */
  finishPlaying(): void;
  /** What the next `record` rejects with, for a refused microphone. */
  refuseMicrophone: Error | null;
}

export function createFakeVoiceDevices(): FakeVoiceDevices {
  let ended: (() => void) | null = null;
  const end = () => {
    devices.playing = false;
    ended?.();
    ended = null;
  };
  const devices: FakeVoiceDevices = {
    recording: false,
    played: [],
    playing: false,
    stops: 0,
    refuseMicrophone: null,
    async record(): Promise<Recording> {
      if (devices.refuseMicrophone) throw devices.refuseMicrophone;
      devices.recording = true;
      return {
        stop: async () => {
          devices.recording = false;
          return new Blob(['fake clip'], { type: 'audio/webm' });
        },
        cancel: () => {
          devices.recording = false;
        },
      };
    },
    player: {
      unlock: () => undefined,
      async play(audio) {
        end();
        devices.played.push(await audio.text());
        devices.playing = true;
        return new Promise<void>((resolve) => {
          ended = resolve;
        });
      },
      stop() {
        if (devices.playing) devices.stops += 1;
        end();
      },
    },
    finishPlaying: end,
  };
  return devices;
}
