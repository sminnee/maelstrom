import type { QueryClient } from '@tanstack/react-query';
import type { TranscriptEvent } from '../live/transcriptReducer';
import type { Backend } from '../protocol/backend';
import { applyEvent, initialClientState, type ClientState } from '../protocol/reducer';
import type { FakeServer } from '../test/fakeServer';

/**
 * Feed the fake backend's frames to the fake server, and rewrite every cached
 * query from the world that results. The fake backend still owns the world
 * until the transcript stream lands; this is the seam that lets the views
 * read it through the API in the meantime. One function, deleted with the
 * fake.
 */
export function bridgeToFakeServer(backend: Backend, server: FakeServer, queryClient: QueryClient) {
  let state: ClientState = initialClientState();
  backend.subscribe((frame) => {
    state = applyEvent(state, frame.event, frame.seq);
    server.world = state.world;
    server.transcripts = state.transcripts;
    if (frame.event.type.startsWith('transcript.')) {
      server.emitTranscript(frame.event as TranscriptEvent);
    }
    for (const query of queryClient.getQueryCache().getAll()) {
      const path = pathForKey(query.queryKey as readonly string[]);
      if (!path) continue;
      try {
        queryClient.setQueryData(query.queryKey, server.read(path));
      } catch {
        // Gone from the world: the next fetch answers 404, as the server would.
        void queryClient.invalidateQueries({ queryKey: query.queryKey });
      }
    }
  });
}

/** The GET behind a query key, mirroring the resource hooks. */
function pathForKey(key: readonly string[]): string | null {
  const [resource, part, id] = key;
  switch (resource) {
    case 'projects':
    case 'worktrees':
    case 'desk':
    case 'attention':
      return `/api/${resource}`;
    case 'tasks':
    case 'agents':
    case 'documents':
      if (part === 'list') return `/api/${resource}`;
      if (part === 'detail' && id) return `/api/${resource}/${id}`;
      return null;
    default:
      return null;
  }
}
