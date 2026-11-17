// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@multica/core/i18n/react";
import { ApiClient } from "@multica/core/api";
import { runtimeModelsKeys } from "@multica/core/runtimes";
import { afterEach, describe, expect, it, vi } from "vitest";
import enAgents from "../../locales/en/agents.json";
import enCommon from "../../locales/en/common.json";
import enIssues from "../../locales/en/issues.json";
import { ModelPicker } from "../../agents/components/inspector/model-picker";
import { ThinkingPicker } from "../../agents/components/inspector/thinking-picker";

const resources = { en: { agents: enAgents, common: enCommon, issues: enIssues } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("chat picker inheritance", () => {
  // Removing either inheritance action leaves the shared picker sending the
  // inspector's empty-string CLI-default sentinel instead of session null.
  it.each(["model", "thinking_level"] as const)("selects explicit null inheritance for %s", async (field) => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ id: "chat-1", execution_config: { [field]: null } }));
    }));
    const api = new ApiClient("https://api.example.test");
    const inheritance = {
      label: "Follow agent settings", active: false,
      onSelect: async () => { await api.updateChatSession("chat-1", { execution_config: { [field]: null } }); },
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(runtimeModelsKeys.forRuntime("rt-1"), {
      supported: true, models: [{ id: "model-a", label: "A" }], unavailableModels: [],
    });
    const view = render(
      <I18nProvider locale="en" resources={resources}>
        <QueryClientProvider client={qc}>
          {field === "model" ? (
            <ModelPicker runtimeId="rt-1" runtimeOnline value="" inheritance={inheritance} onChange={() => {}} />
          ) : (
            <ThinkingPicker value="" levels={[{ value: "high", label: "High" }]} inheritance={inheritance} onChange={() => {}} />
          )}
        </QueryClientProvider>
      </I18nProvider>,
    );
    fireEvent.click(view.container.querySelector("button")!);
    fireEvent.click(await screen.findByRole("button", { name: inheritance.label }));
    await waitFor(() => expect(bodies).toEqual([{ execution_config: { [field]: null } }]));
    view.unmount();
    qc.clear();
  });
  // Removing the disabled open-state gate leaves stale catalog options visible
  // when discovery fails or the selected agent loses its capability signal.
  it.each(["model", "thinking_level"] as const)("closes an open %s picker when it becomes unavailable", async (field) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(runtimeModelsKeys.forRuntime("rt-1"), {
      supported: true, cached: false, models: [{ id: "model-a", label: "A" }], unavailableModels: [],
    });
    const inheritance = { label: "Follow agent settings", active: true, onSelect: () => {} };
    const picker = (disabled: boolean) => (
      <I18nProvider locale="en" resources={resources}><QueryClientProvider client={qc}>
        {field === "model"
          ? <ModelPicker runtimeId="rt-1" runtimeOnline value="" disabled={disabled} inheritance={inheritance} onChange={() => {}} />
          : <ThinkingPicker value="" levels={[{ value: "high", label: "High" }]} disabled={disabled} inheritance={inheritance} onChange={() => {}} />}
      </QueryClientProvider></I18nProvider>
    );
    const view = render(picker(false));
    fireEvent.click(view.container.querySelector("button")!);
    expect(await screen.findByRole("button", { name: inheritance.label })).toBeTruthy();
    view.rerender(picker(true));
    await waitFor(() => expect(screen.queryByRole("button", { name: inheritance.label })).toBeNull());
    expect(view.container.querySelector("button")?.disabled).toBe(true);
    view.unmount(); qc.clear();
  });

});
