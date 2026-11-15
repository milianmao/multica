"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { QaBrowserSessionView } from "../../platform/qa-browser";

/**
 * The sidebar card's server data (SDD §9).
 *
 * TanStack Query owns this, not a store: session state is server (daemon)
 * state, and AGENTS.md reserves Zustand for client-owned UI state. The card
 * itself keeps no copy, so a tab switch cannot resurrect a stale lock owner.
 *
 * Actions come in through a prop rather than being read off `window` here: the
 * platform adapter belongs to the app layer, and `packages/views` must not
 * reach into Electron globals.
 */
export type QaBrowserActions = {
  /** Opens (or reuses) the session and returns its state. */
  open(taskId: string, issueId: string): Promise<QaBrowserSessionView | null>;
  /** Reads the current state without opening anything. */
  read(): Promise<QaBrowserSessionView | null>;
  takeover(sessionId: string): Promise<QaBrowserSessionView | null>;
  release(sessionId: string): Promise<QaBrowserSessionView | null>;
  captureEvidence(sessionId: string): Promise<{ ok: boolean }>;
  setVisible(sessionId: string, visible: boolean): void;
};

export function qaBrowserQueryKey(issueId: string) {
  return ["qa-browser", issueId] as const;
}

export function useQaBrowserSession(
  issueId: string,
  taskId: string | null,
  actions: QaBrowserActions,
) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const query = useQuery({
    queryKey: qaBrowserQueryKey(issueId),
    // A read is cheap and the state is authoritative only on the daemon; the
    // card polls while it is on screen so a takeover by the agent shows up.
    queryFn: async () => actions.read(),
    enabled: taskId !== null,
    refetchInterval: 5_000,
    staleTime: 2_000,
  });

  const run = useCallback(
    async (operation: () => Promise<QaBrowserSessionView | null>) => {
      setPending(true);
      try {
        const session = await operation();
        if (session) queryClient.setQueryData(qaBrowserQueryKey(issueId), session);
        return session;
      } finally {
        setPending(false);
      }
    },
    [issueId, queryClient],
  );

  const session = query.data ?? null;

  const takeover = useCallback(async () => {
    if (!session) return null;
    return run(() => actions.takeover(session.session_id));
  }, [actions, run, session]);

  const release = useCallback(async () => {
    if (!session) return null;
    return run(() => actions.release(session.session_id));
  }, [actions, run, session]);

  const captureEvidence = useCallback(async () => {
    if (!session) return { ok: false };
    setPending(true);
    try {
      return await actions.captureEvidence(session.session_id);
    } finally {
      setPending(false);
      await queryClient.invalidateQueries({ queryKey: qaBrowserQueryKey(issueId) });
    }
  }, [actions, issueId, queryClient, session]);

  const openView = useCallback(() => {
    if (!session) return;
    // Showing is separate from taking over (P3): opening the window does not
    // move the lock, so the agent keeps driving unless the user takes over.
    actions.setVisible(session.session_id, true);
  }, [actions, session]);

  const open = useCallback(async () => {
    if (!taskId) return null;
    return run(() => actions.open(taskId, issueId));
  }, [actions, issueId, run, taskId]);

  return useMemo(
    () => ({
      session,
      loading: query.isLoading || pending,
      open,
      takeover,
      release,
      captureEvidence,
      openView,
    }),
    [captureEvidence, open, openView, pending, query.isLoading, release, session, takeover],
  );
}
