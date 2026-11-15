"use client";

import { useState } from "react";
import { Bot, Camera, ExternalLink, Globe, Keyboard } from "@multica/ui/icons";
import { Button } from "@multica/ui/components/ui/button";
import { Spinner } from "@multica/ui/components/ui/spinner";
import { useT } from "../../i18n";
import { IssueSidebarSectionHeader } from "./issue-sidebar-section";
import {
  useQaBrowserSession,
  type QaBrowserActions,
} from "../hooks/use-qa-browser-session";
import type { QaBrowserSessionView } from "../../platform/qa-browser";

/**
 * "QA browser" in the issue sidebar (SDD §9, P2, P3).
 *
 * This card owns no view and caches no session content of its own: the view
 * lives in main so it survives tab switches (I3), and the state comes from the
 * daemon through the query store. What is left here is presentation plus the
 * two decisions the user makes: take over, hand back.
 *
 * `capability=none` renders the entry disabled with a reason rather than hidden
 * (P2): a missing entry leaves the user unable to tell whether the feature is
 * absent or broken.
 */

const STATE_LABEL_KEY = {
  requested: "requested",
  hosted: "hosted",
  orphaned: "orphaned",
  closing: "closing",
  closed: "closed",
  failed: "failed",
} as const;

export function IssueQaBrowserSection({
  issueId,
  taskId,
  actions,
}: {
  issueId: string;
  /** The run whose browser session belongs to this issue; null when none ran. */
  taskId: string | null;
  actions: QaBrowserActions;
}) {
  const { t } = useT("issues");
  const [open, setOpen] = useState(true);
  const session = useQaBrowserSession(issueId, taskId, actions);
  const view = session.session;

  const capability = view?.capability ?? "full";
  const capabilityBlocked = capability === "none";
  const lockTaken = view?.lock_owner === "human";
  const canOperate = view?.state === "hosted";

  return (
    <div>
      <IssueSidebarSectionHeader
        title={t(($) => $.qa_browser.section_title)}
        open={open}
        onOpenChange={setOpen}
      />
      {open && (
        <div className="px-4 pb-1">
          {capabilityBlocked ? (
            <p className="text-label text-muted-foreground">{t(($) => $.qa_browser.capability_none)}</p>
          ) : !view ? (
            <div className="flex items-center gap-2 text-label text-muted-foreground">
              {session.loading ? <Spinner className="size-3.5" /> : null}
              <span>{session.loading ? t(($) => $.qa_browser.loading) : t(($) => $.qa_browser.not_opened)}</span>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="flex min-w-0 items-center gap-1.5 text-label">
                <Globe aria-hidden="true" className="size-3.5 shrink-0 text-faint-foreground" />
                <span className="truncate text-muted-foreground">
                  {view.url.host ? `${view.url.host}${view.url.path}` : t(($) => $.qa_browser.no_page)}
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-label text-muted-foreground">
                <StatusDot state={view.state} />
                <span>{t(($) => $.qa_browser.state[STATE_LABEL_KEY[view.state]])}</span>
                {lockTaken && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span className="text-foreground">{t(($) => $.qa_browser.locked_by_you)}</span>
                  </>
                )}
              </div>
              {view.error && (
                <p className="text-label text-destructive">
                  {view.error.code === "BROWSER_HOST_OFFLINE"
                    ? t(($) => $.qa_browser.desktop_offline)
                    : view.error.message}
                </p>
              )}
              {view.last_evidence && (
                <p className="text-label text-muted-foreground">
                  {t(($) => $.qa_browser.last_evidence, {
                    time: new Date(view.last_evidence.captured_at).toLocaleTimeString(),
                  })}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-1.5">
                {canOperate && !lockTaken && (
                  <Button size="xs" variant="outline" onClick={() => void session.takeover()}>
                    <Keyboard aria-hidden="true" />
                    {t(($) => $.qa_browser.takeover)}
                  </Button>
                )}
                {canOperate && lockTaken && (
                  <Button size="xs" variant="outline" onClick={() => void session.release()}>
                    <Bot aria-hidden="true" />
                    {t(($) => $.qa_browser.release)}
                  </Button>
                )}
                <Button size="xs" variant="ghost" disabled={!canOperate} onClick={() => session.openView()}>
                  <ExternalLink aria-hidden="true" />
                  {t(($) => $.qa_browser.open_window)}
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={!canOperate}
                  onClick={() => void session.captureEvidence()}
                >
                  <Camera aria-hidden="true" />
                  {t(($) => $.qa_browser.capture)}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatusDot({ state }: { state: QaBrowserSessionView["state"] }) {
  const tone =
    state === "hosted"
      ? "bg-success"
      : state === "requested" || state === "closing"
        ? "bg-warning"
        : state === "orphaned" || state === "failed"
          ? "bg-destructive"
          : "bg-muted-foreground";
  return <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${tone}`} />;
}
