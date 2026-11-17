"use client";

import { useId } from "react";
import { Brain, Cpu, Loader2, RotateCw, Shield } from "@multica/ui/icons";
import { Button } from "@multica/ui/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@multica/ui/components/ui/select";
import { redactSecrets } from "@multica/core/security/redact-secrets";
import { ModelPicker } from "../../agents/components/inspector/model-picker";
import { ThinkingPicker } from "../../agents/components/inspector/thinking-picker";
import { thinkingLevelRequiredForProvider } from "../../agents/components/inspector/model-capability";
import { useT } from "../../i18n";
import { useChatExecutionConfig } from "./use-chat-execution-config";
import type { ChatController } from "./use-chat-controller";

/**
 * Direct composer entrances for both chat surfaces. The shared Query/draft hook
 * owns writes; missing backend or catalog support retains disabled entrances.
 */
export function ChatExecutionControls({ controller: c }: { controller: ChatController }) {
  const { t } = useT("chat");
  const controls = useChatExecutionConfig(c);
  const id = useId();
  const inherit = t(($) => $.execution_config.inherit);
  const label = t(($) => $.execution_config.label);
  const approvalLabel = t(($) => $.execution_config.approval);
  const approvalItems = [
    { value: "inherit", label: t(($) => $.execution_config.approval_inherit) },
    { value: "on-request", label: t(($) => $.execution_config.approval_on_request) },
    { value: "full-access", label: t(($) => $.execution_config.approval_full_access) },
  ];
  const approvalValue = controls.config.approval_policy ?? "inherit";
  const approvalValueLabel = approvalItems.find((item) => item.value === approvalValue)?.label;
  const approvalHint = !controls.supported ? null
    : controls.capabilities?.approval_policy !== true
      ? t(($) => $.execution_config.approval_unsupported)
      : approvalValue === "full-access"
        ? t(($) => $.execution_config.full_access_hint)
        : null;
  if (!c.activeAgent) return null;

  return (
    <div role="group" aria-label={label} aria-busy={controls.saving} className="flex min-w-0 flex-1 basis-64 flex-wrap items-center gap-1 text-muted-foreground">
      <div role="group" aria-label={t(($) => $.execution_config.model)} className="flex min-w-0 max-w-44 items-center gap-1">
        <Cpu className="size-3.5 shrink-0" aria-hidden="true" />
        <ModelPicker
          runtimeId={c.runtime?.id ?? null}
          runtimeOnline={controls.canDiscoverModels}
          variant="chip"
          disabled={controls.modelDisabled}
          value={controls.config.model ?? ""}
          inheritance={{ label: inherit, active: controls.config.model == null, onSelect: () => controls.changeModel(null) }}
          onChange={controls.changeModel}
        />
      </div>
      <div role="group" aria-label={t(($) => $.execution_config.thinking)} className="flex min-w-0 max-w-44 items-center gap-1">
        <Brain className="size-3.5 shrink-0" aria-hidden="true" />
        <ThinkingPicker
          variant="chip"
          disabled={controls.thinkingDisabled}
          allowClear={controls.levels.length === 0 || !thinkingLevelRequiredForProvider(c.runtime?.provider ?? "")}
          value={controls.config.thinking_level ?? ""}
          levels={controls.levels}
          inheritance={{ label: inherit, active: controls.config.thinking_level == null, onSelect: () => controls.changeThinking(null) }}
          onChange={controls.changeThinking}
        />
      </div>
      <Select items={approvalItems} value={approvalValue} disabled={controls.approvalDisabled} onValueChange={(value) => {
        if (value === "inherit") void controls.changeApproval(null);
        else if (value === "on-request" || value === "full-access") void controls.changeApproval(value);
      }}>
        <SelectTrigger size="sm" aria-label={approvalLabel + " · " + approvalValueLabel} aria-describedby={approvalHint ? id + "-approval-hint" : undefined} className="min-w-0 max-w-44 border-0 bg-transparent px-1.5 text-caption hover:bg-accent data-open:bg-accent">
          <Shield className="size-3.5 shrink-0" aria-hidden="true" />
          <SelectValue className="min-w-0 truncate"><span className="truncate">{approvalValueLabel}</span></SelectValue>
        </SelectTrigger>
        <SelectContent side="top" align="start" alignItemWithTrigger={false} className="w-auto min-w-44 max-w-[calc(100vw_-_2rem)]">
          {approvalItems.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
        </SelectContent>
      </Select>
      {controls.saving && <span role="status" aria-label={label}><Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" /></span>}
      {!controls.supported && <p className="basis-full text-micro">{t(($) => $.execution_config.unsupported)}</p>}
      {controls.supported && controls.capabilities?.model !== true &&
        <p className="basis-full text-micro">{t(($) => $.execution_config.model_unsupported)}</p>}
      {controls.supported && controls.capabilities?.thinking_level !== true
        ? <p className="basis-full text-micro">{t(($) => $.execution_config.thinking_unsupported)}</p>
        : controls.supported && !controls.catalogUnavailable && controls.levels.length === 0 &&
          <p className="basis-full text-micro">{t(($) => $.execution_config.thinking_unavailable)}</p>}
      {controls.catalogUnavailable && (
        <div className="flex min-w-0 basis-full items-center gap-1 text-micro">
          <p className="min-w-0">{t(($) => $.execution_config.catalog_unavailable)}</p>
          {controls.canDiscoverModels && (
            <Button type="button" variant="ghost" size="icon-xs" disabled={controls.refreshingCatalog} aria-busy={controls.refreshingCatalog} onClick={controls.refreshCatalog} aria-label={t(($) => $.execution_config.refresh_catalog)}>
              <RotateCw className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
      {approvalHint && <p id={id + "-approval-hint"} className="basis-full text-micro">{approvalHint}</p>}
      {controls.error && <div role="alert" className="basis-full space-y-1 break-words text-caption text-destructive"><p>{t(($) => $.execution_config.save_failed)}</p><p>{redactSecrets(controls.error.message)}</p></div>}
    </div>
  );
}
