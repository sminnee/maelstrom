import { useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import type { Attachment } from '../api/attachments';
import { useApi } from '../api/ApiProvider';
import { speak, transcribe, useVoiceAvailable } from '../api/voice';
import { speakableText } from '../markdown/speakable';
import type { TranscriptItem } from '../protocol/transcript';
import type { Recording } from './devices';
import { VoiceContext } from './voiceContext';

/** The longest text one speech request takes: the server's cap. */
const MAX_SPEECH_CHARS = 4000;

/** What the mic button shows: ready to record, recording, or sending the clip. */
export type MicState = 'idle' | 'recording' | 'sending';

/** What a `MessageInput` draws for **Voice mode**. */
export interface VoiceControls {
  /** Whether the server can transcribe and speak. No toggle without it. */
  available: boolean;
  on: boolean;
  toggle(): void;
  mic: MicState;
  /** Whether a reply plays, or waits to play. */
  speaking: boolean;
  /** Start recording; stop a reply that plays. A second tap sends the clip. */
  tapMic(): Promise<void>;
  /** Why the last recording, upload or reply failed. `''` when none did. */
  error: string;
}

/**
 * **Voice mode** for one session view: the toggle, the mic, and the replies
 * read aloud.
 *
 * With voice on, each assistant message that finishes after the toggle is
 * read aloud, in order, by its `speakableText`. Messages already finished at
 * the toggle are not. A tap on the mic, or voice off, stops the reply that
 * plays and drops the queued ones.
 */
export function useVoice(
  items: TranscriptItem[],
  onSend: (text: string, attachments: Attachment[]) => Promise<unknown>,
): VoiceControls {
  const api = useApi();
  const hub = useContext(VoiceContext);
  if (!hub) throw new Error('useVoice outside VoiceProvider');
  const { devices, active, setActive } = hub;
  const available = useVoiceAvailable();
  // A token per view, not the agent id: one agent can show in two views at
  // once, such as the Foreman pane and a panel tab, and only one may speak.
  const viewId = useId();
  const on = active === viewId;
  const [mic, setMic] = useState<MicState>('idle');
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState('');
  // The finished assistant messages already seen, so each is read at most once.
  // `null` while voice is off: the next turn-on takes the transcript as read.
  const heard = useRef<Set<string> | null>(null);
  const queue = useRef<string[]>([]);
  const playing = useRef(false);
  // Bumped by each interrupt, so a reply fetched before it is never played.
  const generation = useRef(0);
  const recording = useRef<Recording | null>(null);
  // Aborts the speech request in flight, so an interrupted reply is not paid for.
  const inFlight = useRef<AbortController | null>(null);

  // Stop the reply that plays, and drop the queued ones. No state is set here,
  // so the off effect can call it.
  const silence = useCallback(() => {
    generation.current += 1;
    queue.current = [];
    playing.current = false;
    inFlight.current?.abort();
    inFlight.current = null;
    devices.player.stop();
  }, [devices]);

  // Read the queue aloud, one reply after the next, until it is empty or an
  // interrupt bumps the generation.
  const pump = useCallback(async () => {
    if (playing.current || queue.current.length === 0) return;
    playing.current = true;
    const gen = generation.current;
    for (let text = queue.current.shift(); text !== undefined; text = queue.current.shift()) {
      try {
        inFlight.current = new AbortController();
        const audio = await speak(api, text, inFlight.current.signal);
        if (gen !== generation.current) return;
        setSpeaking(true);
        await devices.player.play(audio);
      } catch (err) {
        if (gen === generation.current) setError(messageOf(err));
      }
      if (gen !== generation.current) return;
    }
    playing.current = false;
    setSpeaking(false);
  }, [api, devices]);

  useEffect(() => {
    if (!on) {
      heard.current = null;
      return;
    }
    const finished = items.filter(
      (i) => i.type === 'message' && i.role === 'assistant' && !i.partial,
    );
    if (heard.current === null) {
      heard.current = new Set(finished.map((i) => i.id));
      return;
    }
    for (const item of finished) {
      if (heard.current.has(item.id) || item.type !== 'message') continue;
      heard.current.add(item.id);
      const text = speakableText(item.markdown).slice(0, MAX_SPEECH_CHARS);
      if (text) queue.current.push(text);
    }
    void pump();
  }, [on, items, pump]);

  // Off, by this toggle or another view's: stop speaking and listening. Only on
  // the way off: a view that never had voice must not stop the speaker another view uses.
  const wasOn = useRef(false);
  useEffect(() => {
    if (on) {
      wasOn.current = true;
      return;
    }
    if (!wasOn.current) return;
    wasOn.current = false;
    silence();
    recording.current?.cancel();
    recording.current = null;
  }, [on, silence]);
  // Unmounted with voice on: hand voice back.
  useEffect(
    () => () => {
      if (!wasOn.current) return;
      silence();
      recording.current?.cancel();
      recording.current = null;
      setActive(null);
    },
    [silence, setActive],
  );

  const toggle = () => {
    if (on) {
      setActive(null);
      return;
    }
    // In the click itself: see `VoicePlayer.unlock`.
    devices.player.unlock();
    setError('');
    setMic('idle');
    setSpeaking(false);
    setActive(viewId);
  };

  const tapMic = async () => {
    silence();
    setSpeaking(false);
    if (mic === 'idle') {
      setError('');
      const gen = generation.current;
      try {
        const started = await devices.record();
        // Voice went off, or the view went, while the browser asked for the
        // mic: nothing would cancel this recording later.
        if (gen !== generation.current) {
          started.cancel();
          return;
        }
        recording.current = started;
        setMic('recording');
      } catch (err) {
        setError(`The microphone is not available: ${messageOf(err)}`);
      }
      return;
    }
    const clip = recording.current;
    if (mic !== 'recording' || !clip) return;
    recording.current = null;
    setMic('sending');
    try {
      const { text } = await transcribe(api, await clip.stop());
      // Nothing heard: nothing to send.
      if (text.trim()) await onSend(text.trim(), []);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setMic('idle');
    }
  };

  // While off, the last session's mic and speaker state no longer holds.
  return {
    available,
    on,
    toggle,
    mic: on ? mic : 'idle',
    speaking: on && speaking,
    tapMic,
    error,
  };
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
