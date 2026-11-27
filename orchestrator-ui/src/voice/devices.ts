/**
 * The microphone and the speaker **Voice mode** uses, behind an interface, so
 * a test hands in fakes as `live/socketFactory.ts` does for sockets.
 */

/** One clip being recorded. */
export interface Recording {
  /** Stop, and resolve with the clip. */
  stop(): Promise<Blob>;
  /** Stop and drop the clip. */
  cancel(): void;
}

/** The one speaker every session view shares. */
export interface VoicePlayer {
  /**
   * Make later plays work. iOS plays audio only from a user gesture, so the
   * Voice toggle calls this in its click, and later replies reuse the element.
   */
  unlock(): void;
  /** Play `audio`; resolve when it ends or is stopped. */
  play(audio: Blob): Promise<void>;
  /** Stop what plays now. */
  stop(): void;
}

export interface VoiceDevices {
  /** Start recording from the microphone. Rejects when the user refuses it. */
  record(): Promise<Recording>;
  player: VoicePlayer;
}

/** The browser's `MediaRecorder` and one reused `<audio>` element. */
export function browserVoiceDevices(): VoiceDevices {
  return { record: recordMicrophone, player: audioElementPlayer() };
}

async function recordMicrophone(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const release = () => stream.getTracks().forEach((track) => track.stop());
  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream);
    recorder.start();
  } catch (err) {
    // A browser with no recorder must not leave the mic live.
    release();
    throw err;
  }
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  const clip = () => new Blob(chunks, { type: recorder.mimeType });
  return {
    stop: () =>
      new Promise((resolve, reject) => {
        // iOS can end the track on an interruption: the recorder is then
        // inactive already, and `onstop` never fires again.
        if (recorder.state === 'inactive') {
          release();
          resolve(clip());
          return;
        }
        recorder.onstop = () => {
          release();
          resolve(clip());
        };
        recorder.onerror = () => {
          release();
          reject(new Error('The recording failed'));
        };
        recorder.stop();
      }),
    cancel: () => {
      recorder.onstop = release;
      if (recorder.state !== 'inactive') recorder.stop();
      else release();
    },
  };
}

function audioElementPlayer(): VoicePlayer {
  let audio: HTMLAudioElement | null = null;
  // Ends the clip that plays now. Each play binds its own, so a late event of
  // an earlier clip cannot end the next one.
  let endCurrent: (() => void) | null = null;
  const element = () => (audio ??= new Audio());
  return {
    unlock() {
      const el = element();
      el.src = SILENCE;
      void el.play().catch(() => undefined);
    },
    play(clip) {
      endCurrent?.();
      const el = element();
      const url = URL.createObjectURL(clip);
      return new Promise<void>((resolve, reject) => {
        const settle = (err?: unknown) => {
          if (endCurrent !== end) return;
          endCurrent = null;
          el.onended = null;
          el.onerror = null;
          URL.revokeObjectURL(url);
          if (err === undefined) resolve();
          else reject(err instanceof Error ? err : new Error('The reply could not play'));
        };
        const end = () => settle();
        endCurrent = end;
        el.onended = end;
        el.onerror = () => settle(new Error('The reply could not play'));
        el.src = url;
        // A refused play, such as an unlock iOS did not keep, is an error the
        // view shows, not a reply that ended.
        el.play().catch((err: unknown) => settle(err ?? new Error('The reply could not play')));
      });
    },
    stop() {
      audio?.pause();
      endCurrent?.();
    },
  };
}

/** A tenth of a second of silence, as a WAV data URL: what `unlock` plays. */
const SILENCE = (() => {
  const samples = 800;
  const bytes = new Uint8Array(44 + samples);
  const view = new DataView(bytes.buffer);
  const text = (at: number, s: string) =>
    [...s].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples, true);
  text(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, 8000, true);
  view.setUint32(28, 8000, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true);
  text(36, 'data');
  view.setUint32(40, samples, true);
  bytes.fill(128, 44); // 8-bit PCM silence is the midpoint
  return `data:audio/wav;base64,${btoa(String.fromCharCode(...bytes))}`;
})();
