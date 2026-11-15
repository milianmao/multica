import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nProvider } from "@multica/core/i18n/react";
import enCommon from "../../locales/en/common.json";
import enIssues from "../../locales/en/issues.json";
import type { QaBrowserSessionView } from "../../platform/qa-browser";
import type { QaBrowserActions } from "../hooks/use-qa-browser-session";
import { IssueQaBrowserSection } from "./issue-qa-browser-section";

/**
 * The sidebar card's own decisions (SDD §9, P2, P3).
 *
 * The card renders state and offers two actions; everything else (the view, the
 * geometry, the session whitelist) lives in main and is covered there. So the
 * cases worth pinning are: the disabled entry for a provider that cannot host a
 * browser, and the fact that showing the window is not taking it over.
 */

const TEST_RESOURCES = { en: { common: enCommon, issues: enIssues } };

function session(overrides: Partial<QaBrowserSessionView> = {}): QaBrowserSessionView {
  return {
    session_id: "bs_1",
    task_id: "t1",
    issue_id: "i1",
    state: "hosted",
    lock_owner: "agent",
    url: { host: "127.0.0.1:3000", path: "/dashboard" },
    partition: "qa-task-t1",
    capability: "full",
    started_at: "2026-11-15T06:00:00Z",
    ...overrides,
  };
}

function actions(overrides: Partial<QaBrowserActions> = {}): QaBrowserActions {
  return {
    open: vi.fn(async () => session()),
    read: vi.fn(async () => session()),
    takeover: vi.fn(async () => session({ lock_owner: "human" })),
    release: vi.fn(async () => session({ lock_owner: "agent" })),
    captureEvidence: vi.fn(async () => ({ ok: true })),
    setVisible: vi.fn(),
    ...overrides,
  };
}

function renderCard(props: { taskId?: string | null; actions?: QaBrowserActions }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider locale="en" resources={TEST_RESOURCES}>
        <IssueQaBrowserSection
          issueId="i1"
          taskId={props.taskId === undefined ? "t1" : props.taskId}
          actions={props.actions ?? actions()}
        />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("IssueQaBrowserSection", () => {
  it("explains an unavailable provider instead of hiding the entry", async () => {
    const deps = actions({ read: vi.fn(async () => session({ capability: "none" })) });
    renderCard({ actions: deps });

    // P2: the entry stays visible so the user can tell "absent" from "broken".
    expect(await screen.findByText(enIssues.qa_browser.section_title)).toBeInTheDocument();
    expect(await screen.findByText(enIssues.qa_browser.capability_none)).toBeInTheDocument();
    expect(screen.queryByText(enIssues.qa_browser.takeover)).not.toBeInTheDocument();
  });

  it("offers takeover while the agent drives, and hand-back once taken over", async () => {
    const takeover = vi.fn(async () => session({ lock_owner: "human" }));
    const deps = actions({ takeover });
    renderCard({ actions: deps });

    const button = await screen.findByRole("button", { name: enIssues.qa_browser.takeover });
    fireEvent.click(button);

    await waitFor(() => expect(takeover).toHaveBeenCalledWith("bs_1"));
    expect(await screen.findByText(enIssues.qa_browser.locked_by_you)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: enIssues.qa_browser.release })).toBeInTheDocument();
  });

  it("shows the window without taking the lock over", async () => {
    const setVisible = vi.fn();
    const takeover = vi.fn();
    renderCard({ actions: actions({ setVisible, takeover }) });

    fireEvent.click(await screen.findByRole("button", { name: enIssues.qa_browser.open_window }));

    expect(setVisible).toHaveBeenCalledWith("bs_1", true);
    // P3: watching is not controlling, so the agent keeps driving.
    expect(takeover).not.toHaveBeenCalled();
  });

  it("renders the daemon's host+path and never a query string", async () => {
    renderCard({ actions: actions({ read: vi.fn(async () => session({ url: { host: "127.0.0.1:3000", path: "/dashboard" } })) }) });
    expect(await screen.findByText("127.0.0.1:3000/dashboard")).toBeInTheDocument();
    expect(screen.queryByText(/token=/)).not.toBeInTheDocument();
  });

  it("names a missing Desktop as the reason a session cannot start", async () => {
    renderCard({
      actions: actions({
        read: vi.fn(async () =>
          session({
            state: "failed",
            error: { code: "BROWSER_HOST_OFFLINE", message: "no host" },
          }),
        ),
      }),
    });
    expect(await screen.findByText(enIssues.qa_browser.desktop_offline)).toBeInTheDocument();
  });

  it("disables the view actions once the session has ended", async () => {
    renderCard({ actions: actions({ read: vi.fn(async () => session({ state: "closed" })) }) });
    expect(await screen.findByText(enIssues.qa_browser.state.closed)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: enIssues.qa_browser.open_window })).toBeDisabled();
    expect(screen.getByRole("button", { name: enIssues.qa_browser.capture })).toBeDisabled();
  });
});
