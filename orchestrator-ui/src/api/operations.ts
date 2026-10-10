import { QueryObserver, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryClient, QueryObserverResult } from '@tanstack/react-query';
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
 * so this covers the console's history and every wait.
 */
export const OPERATION_LIST_LIMIT = 200;

const operationsQuery = (api: ApiClient) => ({
  queryKey: keys.operations.list(),
  queryFn: () => api.get<OperationsBody>(`/api/operations?limit=${OPERATION_LIST_LIMIT}`),
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
    queryFn: () =>
      api.get<OperationLog>(`/api/operations/${encodeURIComponent(operationId ?? '')}/log`),
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

/** An operation that ended refused or failed. Its message is the operation's words. */
export class OperationEndedBadly extends Error {
  readonly operation: Operation;
  constructor(operation: Operation) {
    super(operation.words);
    this.name = 'OperationEndedBadly';
    this.operation = operation;
  }
}

/**
 * Wait for the operation `operationId` to end. Resolves when it is done, and
 * rejects with `OperationEndedBadly` when it is refused or failed.
 *
 * It reads the list through an observer of its own, so the list stays live
 * while nothing on screen draws it, and it waits on the entity rather than on
 * any HTTP call: an operation can run for minutes. A read made after the start
 * that lacks the operation, or a read that fails, ends the wait too, so a
 * server that lost the operation never leaves a button processing.
 */
export function untilEnded(
  queryClient: QueryClient,
  api: ApiClient,
  operationId: OperationId,
): Promise<Operation> {
  return new Promise((resolve, reject) => {
    const observer = new QueryObserver(queryClient, operationsQuery(api));
    // Set once a read that began after the start has landed: only such a read
    // can say the operation is missing.
    let fresh = false;
    let stop = () => {};
    const settle = (result: QueryObserverResult<OperationsBody>) => {
      if (!fresh || result.isFetching) return;
      if (result.isError) {
        stop();
        reject(result.error);
        return;
      }
      const op = result.data?.operations.find((o) => o.id === operationId);
      if (op?.state === 'running') return;
      stop();
      if (!op) reject(new Error(`The server no longer holds operation ${operationId}`));
      else if (op.state === 'done') resolve(op);
      else reject(new OperationEndedBadly(op));
    };
    stop = observer.subscribe(settle);
    // The cached list predates the operation, so it is read again now, which
    // cancels a read already in flight; the change notices keep it moving.
    void queryClient.invalidateQueries({ queryKey: keys.operations.list() }).then(() => {
      fresh = true;
      settle(observer.getCurrentResult());
    });
  });
}

/**
 * Start an operation over `start`, and wait for it to end. What a worktree
 * mutation runs, so its button shows `processing` until the operation ends.
 */
export async function runOperation(
  queryClient: QueryClient,
  api: ApiClient,
  start: () => Promise<OperationStarted>,
): Promise<Operation> {
  const { operationId } = await start();
  return untilEnded(queryClient, api, operationId);
}
