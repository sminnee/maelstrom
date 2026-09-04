import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Anchor, Document } from '../protocol/documents';
import type { CommentId, DocumentId } from '../protocol/ids';
import type { DocumentRow } from '../selectors/world';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface DocumentsBody {
  documents: DocumentRow[];
}

export function useDocuments() {
  const api = useApi();
  return useQuery({
    queryKey: keys.documents.list(),
    queryFn: () => api.get<DocumentsBody>('/api/documents'),
  });
}

/** One document with its markdown. */
export function useDocument(documentId: DocumentId | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.documents.detail(documentId ?? ''),
    queryFn: () => api.get<Document>(`/api/documents/${documentId}`),
    enabled: documentId !== null,
  });
}

/**
 * Every document command changes the document and its list row. Every route
 * below answers 501 today, so the invalidation waits for the server to serve
 * them; the shape is here so the hooks land ready.
 */
function useDocumentMutation<V extends { documentId: DocumentId }>(
  send: (api: ReturnType<typeof useApi>, vars: V) => Promise<unknown>,
) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: V) => send(api, vars),
    onSuccess: (_result, vars) => {
      void queryClient.invalidateQueries({ queryKey: keys.documents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.documents.detail(vars.documentId) });
    },
  });
}

export function useAddComment() {
  return useDocumentMutation(
    (api, vars: { documentId: DocumentId; version: number; anchor: Anchor; body: string }) =>
      api.post(`/api/documents/${vars.documentId}/comments`, {
        version: vars.version,
        anchor: vars.anchor,
        body: vars.body,
      }),
  );
}

export function useResolveComment() {
  return useDocumentMutation((api, vars: { documentId: DocumentId; commentId: CommentId }) =>
    api.post(`/api/documents/${vars.documentId}/comments/${vars.commentId}/resolve`),
  );
}

export function useApproveDocument() {
  return useDocumentMutation((api, vars: { documentId: DocumentId; version: number }) =>
    api.post(`/api/documents/${vars.documentId}/approve`, { version: vars.version }),
  );
}

export function useRequestChanges() {
  return useDocumentMutation(
    (api, vars: { documentId: DocumentId; version: number; summary: string }) =>
      api.post(`/api/documents/${vars.documentId}/request-changes`, {
        version: vars.version,
        summary: vars.summary,
      }),
  );
}
