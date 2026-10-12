import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Operation } from '../protocol/entities';
import type { OperationId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import type { ApiClient } from './http';
import { keys } from './keys';

export interface OperationsBody {
  operations: Operation[];
}

/** One operation's log lines, grouped by step in the order the steps ran. */
export interface OperationLog {
  operationId: OperationId;
  steps: { name: string; lines: string[] }[];
}

/** What a route that starts an operation answers, at once. */
export interface OperationStarted {
  operationId: OperationId;
}

/**
 * How many operations the list reads. A running one is always among the newest,
 * so this covers the console's history and the board.
 */
export const OPERATION_LIST_LIMIT = 200;

const operationsQuery = (api: ApiClient) => ({
  queryKey: keys.operations.list(),
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    api.get<OperationsBody>(`/api/operations?limit=${OPERATION_LIST_LIMIT}`, { signal }),
});

/** The operations, newest first. A change notice of kind `operation` refreshes it. */
export function useOperations() {
  const api = useApi();
  return useQuery(operationsQuery(api));
}

/** One operation's log: a separate read from the list. */
export function useOperationLog(operationId: OperationId | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.operations.log(operationId ?? ''),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.get<OperationLog>(`/api/operations/${encodeURIComponent(operationId ?? '')}/log`, {
        signal,
      }),
    enabled: operationId !== null,
  });
}

/** Mark an operation seen: it leaves the strip, and the record stays. */
export function useMarkSeen() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { operationId: OperationId }) =>
      api.post(`/api/operations/${encodeURIComponent(vars.operationId)}/seen`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.operations.list() });
    },
  });
}

/**
 * Run a refused or failed operation again, from its first step that did not
 * finish. It keeps its id; the entity says how it goes.
 */
export function useRetryOperation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { operationId: OperationId }) =>
      api.post<OperationStarted>(`/api/operations/${encodeURIComponent(vars.operationId)}/retry`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.operations.list() });
    },
  });
}

/**
 * A mutation that starts an operation. It resolves on the server's `202`, and
 * rejects when the server refuses the start (a 400, or Busy). It reads the
 * operation list again so the run shows at once — see "Commands are mutations"
 * in `docs/dev/orchestrator-ui.md`.
 */
export function useStartOperation<V>(
  start: (api: ApiClient, vars: V) => Promise<OperationStarted>,
) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: V) => start(api, vars),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.operations.list() });
    },
  });
}
