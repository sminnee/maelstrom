import { useQuery } from '@tanstack/react-query';
import type { Document } from '../protocol/documents';
import type { DocumentId } from '../protocol/ids';
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
