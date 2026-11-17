// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildChatModelChangeUpdate } from "../../agents/components/inspector/model-change-cleanup";
import { pickModelEntry } from "../../agents/components/inspector/thinking-prop-row";
import type { RuntimeModel } from "@multica/core/types";

const catalog: RuntimeModel[] = [
  { id: "model-a", label: "A", thinking: { supported_levels: [{ value: "high", label: "High" }] }, service_tiers: [{ id: "priority", name: "Fast" }] },
  { id: "model-b", label: "B", thinking: { supported_levels: [{ value: "low", label: "Low" }] } },
];
const inherited = { model: "model-a", thinking_level: "high", service_tier: "priority" };

describe("chat model-change cleanup", () => {
  // Removing the cleanup merge sends the agent's old effort and Fast tier to a
  // model that advertised neither, despite the composer showing the new model.
  it("clears incompatible inherited effort and tier in the same model PATCH", () => {
    expect(buildChatModelChangeUpdate({ provider: "codex", model: "model-b", config: {}, inherited, catalog }))
      .toEqual({ model: "model-b", thinking_level: "", service_tier: "" });
  });

  // Removing the explicit nulls lets the server's model-change reset discard
  // compatible settings, and an inherited model becomes an explicit override.
  it("preserves compatible settings and sends explicit null for inheritance", () => {
    expect(buildChatModelChangeUpdate({ provider: "codex", model: null, config: { model: "model-b" }, inherited, catalog }))
      .toEqual({ model: null, thinking_level: null, service_tier: null });
    expect(buildChatModelChangeUpdate({ provider: "codex", model: "manual-id", config: { thinking_level: "high" }, inherited, catalog: null }))
      .toEqual({ model: "manual-id", thinking_level: "high", service_tier: null });
  });

  it("does not infer a Codex or OMP effort catalog from an arbitrary flagged default", () => {
    expect(pickModelEntry(catalog, "", "codex")).toBeUndefined();
    expect(pickModelEntry(catalog, "", "omp")).toBeUndefined();
    expect(pickModelEntry(catalog, "model-b", "codex")?.id).toBe("model-b");
  });
});
