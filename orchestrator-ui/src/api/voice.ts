import { useQuery } from '@tanstack/react-query';
import type { ApiClient } from './http';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';

/**
 * Whether **Voice mode** can work: the server has a speech provider.
 *
 * Read once per page load. A key added to the config shows after a reload.
 */
export function useVoiceAvailable(): boolean {
  const api = useApi();
  const query = useQuery({
    queryKey: keys.voice(),
    queryFn: ({ signal }) => api.get<{ available: boolean }>('/api/voice', { signal }),
  });
  return query.data?.available ?? false;
}

/** The text of one recorded clip. The clip's type tells the server the format; the name is unused. */
export function transcribe(api: ApiClient, clip: Blob): Promise<{ text: string }> {
  const form = new FormData();
  form.append('file', clip, 'clip');
  return api.post('/api/voice/transcription', form, { timeoutMs: SLOW_CALL_TIMEOUT_MS });
}

/** `text` read aloud, as audio bytes. */
export function speak(api: ApiClient, text: string, signal?: AbortSignal): Promise<Blob> {
  return api.postBlob('/api/voice/speech', { text }, { timeoutMs: SLOW_CALL_TIMEOUT_MS, signal });
}
