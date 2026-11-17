"use client";

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getCurrentWsId } from "@multica/core/platform";
import { useChatStore, getNewChatExecutionConfig } from "@multica/core/chat";
import { useSetChatSessionExecutionConfig } from "@multica/core/chat/mutations";
import { isRuntimeUsableForUser, refreshRuntimeModels, runtimeModelsOptions } from "@multica/core/runtimes";
import type { ChatExecutionConfig } from "@multica/core/types/chat";
import { buildChatModelChangeUpdate } from "../../agents/components/inspector/model-change-cleanup";
import { pickModelEntry } from "../../agents/components/inspector/thinking-prop-row";
import type { ChatController } from "./use-chat-controller";

const EMPTY_CONFIG: ChatExecutionConfig = {};

/**
 * One composer contract for both surfaces: only unsent settings live in the
 * draft store; existing sessions are rendered from server-owned Query data.
 * A missing backend flag or unavailable catalog never advertises native options.
 */
export function useChatExecutionConfig(c: ChatController) {
  const qc = useQueryClient();
  const update = useSetChatSessionExecutionConfig();
  const draft = useChatStore((state) => state.draftExecutionConfigs[c.activeAgent?.id ?? ""]);
  const provider = c.runtime?.provider ?? "";
  const support = c.executionConfigSupport;
  const supported = support?.execution_config_supported === true;
  const capabilities = support?.execution_config_capabilities;
  const defaults = useMemo(() => getNewChatExecutionConfig(support), [support]);
  const config = useMemo(() => c.activeSessionId
    ? c.currentSession?.execution_config ?? EMPTY_CONFIG
    : { ...defaults, ...draft }, [c.activeSessionId, c.currentSession?.execution_config, defaults, draft]);
  const runtimeOnline = c.runtime?.status === "online";
  const canReadRuntime = !!c.runtime && isRuntimeUsableForUser(c.runtime, c.user?.id ?? null);
  const canDiscoverModels = supported && runtimeOnline && canReadRuntime &&
    (capabilities?.model === true || capabilities?.thinking_level === true);
  const modelsQuery = useQuery(runtimeModelsOptions(canDiscoverModels ? c.runtime?.id : null));
  // A failed refresh retains cached rows in Query, but they are no longer an
  // authoritative capability catalog and must not drive model-change cleanup.
  const catalog = canDiscoverModels && modelsQuery.isSuccess && modelsQuery.data.supported
    ? modelsQuery.data.models : null;
  const effectiveModel = config.model ?? c.currentSession?.effective_execution_config?.model ?? c.activeAgent?.model ?? "";
  const modelEntry = catalog ? pickModelEntry(catalog, effectiveModel, provider) : undefined;
  const levels = modelEntry?.thinking?.supported_levels ?? [];
  const saving = update.isPending && update.variables?.sessionId === c.activeSessionId;
  const readOnly = c.isSessionArchived || c.isAgentArchived || c.isAgentAccessRevoked ||
    !c.isAgentRuntimeBound || (!!c.activeSessionId && !c.currentSession);
  const canEdit = supported && !!c.activeAgent && !readOnly && !saving;
  const modelDisabled = !canEdit || capabilities?.model !== true || catalog === null;
  const thinkingDisabled = !canEdit || capabilities?.thinking_level !== true || catalog === null ||
    (levels.length === 0 && !config.thinking_level);
  const approvalDisabled = !canEdit || capabilities?.approval_policy !== true;
  const error = update.variables?.sessionId === c.activeSessionId ? update.error : null;

  /** Await a server write without optimistic labels; merge only client-owned drafts. */
  async function save(patch: ChatExecutionConfig): Promise<void> {
    if (!canEdit || !c.activeAgent || getCurrentWsId() !== c.wsId) return;
    if (c.activeSessionId) {
      try {
        await update.mutateAsync({ sessionId: c.activeSessionId, config: patch });
      } catch {
        // The mutation retains the original Query config and exposes its error.
      }
    } else {
      const state = useChatStore.getState();
      state.setDraftExecutionConfig(c.activeAgent.id, {
        ...defaults, ...state.draftExecutionConfigs[c.activeAgent.id], ...patch,
      });
    }
  }

  /** Keep compatible effort/tier values in the same model PATCH; null remains inheritance. */
  async function changeModel(model: string | null): Promise<void> {
    if (modelDisabled) return;
    await save(buildChatModelChangeUpdate({
      provider, model, config, catalog,
      inherited: {
        model: c.activeAgent?.model ?? "",
        thinking_level: c.activeAgent?.thinking_level ?? "",
        service_tier: c.activeAgent?.service_tier ?? "",
      },
    }));
  }

  /** Explicit refresh shares the picker cache; failures remain visible and retryable. */
  function refreshCatalog() {
    if (!canDiscoverModels || !c.runtime) return;
    void refreshRuntimeModels(qc, c.runtime.id).catch(() => {});
  }

  return {
    config, supported, capabilities, saving, error, levels,
    modelDisabled, thinkingDisabled, approvalDisabled,
    canDiscoverModels, catalogUnavailable: supported && catalog === null &&
      (capabilities?.model === true || capabilities?.thinking_level === true),
    refreshingCatalog: modelsQuery.isFetching, refreshCatalog, changeModel,
    changeThinking: (level: string | null) => thinkingDisabled ? Promise.resolve() : save({ thinking_level: level }),
    changeApproval: (policy: ChatExecutionConfig["approval_policy"]) => approvalDisabled ? Promise.resolve() : save({ approval_policy: policy }),
  };
}
