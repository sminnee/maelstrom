import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Anchor, Document, DocumentDetail } from '../protocol/documents';
import type { CommentId, DocumentId, TaskId } from '../protocol/ids';
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
    queryFn: ({ signal }) => api.get<DocumentsBody>('/api/documents', { signal }),
  });
}

/** One document with its markdown, at `version`, else at its latest. */
export function useDocument(documentId: DocumentId | null, version?: number) {
  const api = useApi();
  const query = version === undefined ? '' : `?version=${version}`;
  return useQuery({
    queryKey: keys.documents.detail(documentId ?? '', version),
    queryFn: async ({ signal }): Promise<DocumentDetail> => {
      // An older Remote host answers without the stored fields.
      const detail = await api.get<
        Document & Partial<Pick<DocumentDetail, 'ref' | 'versions' | 'fragments'>>
      >(`/api/documents/${documentId}${query}`, { signal });
      return {
        ...detail,
        ref: detail.ref ?? null,
        versions: detail.versions ?? [],
        fragments: detail.fragments ?? [],
      };
    },
    enabled: documentId !== null,
  });
}

/** Every document command changes the document and its list row. */
function useDocumentMutation<V extends { documentId: DocumentId }, R = unknown>(
  send: (api: ReturnType<typeof useApi>, vars: V) => Promise<R>,
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

/** What an approve replies: the tasks it created, for a task set. */
export interface ApproveResult {
  taskIds?: TaskId[];
}

export function useApproveDocument() {
  return useDocumentMutation((api, vars: { documentId: DocumentId; version: number }) =>
    api.post<ApproveResult>(`/api/documents/${vars.documentId}/approve`, {
      version: vars.version,
    }),
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

/** Link `taskId` to the document, or to one heading of it. */
export function useLinkDocument() {
  return useDocumentMutation(
    (api, vars: { documentId: DocumentId; taskId: TaskId; fragment: string }) =>
      api.post(`/api/documents/${vars.documentId}/link`, {
        taskId: vars.taskId,
        fragment: vars.fragment,
      }),
  );
}

export function useUnlinkDocument() {
  return useDocumentMutation(
    (api, vars: { documentId: DocumentId; taskId: TaskId; fragment: string }) =>
      api.post(`/api/documents/${vars.documentId}/unlink`, {
        taskId: vars.taskId,
        fragment: vars.fragment,
      }),
  );
}

/** Archive or unarchive. No `by`: any document may be archived (ADR 0001). */
export function useArchiveDocument() {
  return useDocumentMutation((api, vars: { documentId: DocumentId; archived: boolean }) =>
    api.post(`/api/documents/${vars.documentId}/${vars.archived ? 'archive' : 'unarchive'}`),
  );
}
