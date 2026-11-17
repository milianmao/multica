import type { TaskMessagePayload } from "@multica/core/types/events";
import { redactSecrets } from "./redact";

/** A unified timeline entry: tool calls, thinking, text, and errors in chronological order. */
export interface TimelineItem {
  seq: number;
  type: "tool_use" | "tool_result" | "thinking" | "text" | "error";
  tool?: string;
  /** Opaque identity for pairing tool events within a backend execution. */
  callId?: string;
  content?: string;
  input?: Record<string, unknown>;
  output?: string;
  /**
   * Whether the stored `output` dropped bytes at the source (`tool_result`
   * only). `undefined` means unknown — the record predates the flag or came
   * from an older daemon — and must never be rendered as "complete".
   */
  output_truncated?: boolean;
  created_at?: string;
}

type ProseFragment = Pick<TimelineItem, "seq" | "content" | "created_at">;
// Retain immutable source positions without exposing unredacted fragments on rendered items.
const proseFragments = new WeakMap<TimelineItem, readonly ProseFragment[]>();

function canMergeStreamingText(prev: TimelineItem, next: TimelineItem): boolean {
  return (prev.type === "thinking" || prev.type === "text") && prev.type === next.type;
}

/** Merge adjacent text/thinking fragments that were split only by daemon flush timing. */
export function coalesceTimelineItems(items: TimelineItem[]): TimelineItem[] {
  const sorted = [...items].sort((a, b) => a.seq - b.seq);
  const out: TimelineItem[] = [];

  for (const item of sorted) {
    const prev = out[out.length - 1];
    if (prev && canMergeStreamingText(prev, item)) {
      const merged = {
        ...prev,
        content: `${prev.content ?? ""}${item.content ?? ""}`,
        created_at: item.created_at ?? prev.created_at,
      };
      proseFragments.set(merged, [...(proseFragments.get(prev) ?? [prev]), ...(proseFragments.get(item) ?? [item])]);
      out[out.length - 1] = merged;
      continue;
    }
    out.push(item);
  }

  return out;
}

export function appendTimelineItem(items: TimelineItem[], item: TimelineItem): TimelineItem[] {
  return coalesceTimelineItems([...items, item]);
}

/**
 * Split only prose runs crossed by published timestamps, keeping their original seqs.
 * Cuts are projected onto the fully redacted body: redacting each side separately
 * could expose a credential that the daemon streamed across a guidance boundary.
 */
export function splitTimelineItemsAt(items: TimelineItem[], dates: readonly (string | undefined)[]): TimelineItem[] {
  const boundaries = dates.map((date) => Date.parse(date ?? "")).filter(Number.isFinite).sort((a, b) => a - b);
  if (boundaries.length === 0) return items;
  return items.flatMap((item) => {
    const fragments = proseFragments.get(item);
    if (!fragments || fragments.length < 2) return [item];
    const groups: ProseFragment[][] = [];
    let group: ProseFragment[] = [];
    let bucket = 0;
    for (const fragment of fragments) {
      const stamp = Date.parse(fragment.created_at ?? "");
      const next = Number.isFinite(stamp) ? Math.max(bucket, boundaries.filter((at) => at < stamp).length) : bucket;
      if (next !== bucket && group.length > 0) { groups.push(group); group = []; }
      group.push(fragment);
      bucket = next;
    }
    groups.push(group);
    if (groups.length === 1) return [item];
    const body = redactSecrets(fragments.map((fragment) => fragment.content ?? "").join(""));
    let prefix = "";
    let from = 0;
    return groups.map((members, index) => {
      prefix += members.map((fragment) => fragment.content ?? "").join("");
      let end = body.length;
      if (index < groups.length - 1) {
        const safePrefix = redactSecrets(prefix);
        let common = 0;
        while (common < safePrefix.length && common < body.length && safePrefix[common] === body[common]) common++;
        end = Math.max(from, common);
      }
      const part: TimelineItem = { ...item, seq: members[0]!.seq, content: body.slice(from, end),
        created_at: members.reduce<string | undefined>((last, fragment) => fragment.created_at ?? last, undefined) };
      from = end;
      proseFragments.set(part, members);
      return part;
    });
  });
}

function redactTimelineItems(items: TimelineItem[]): TimelineItem[] {
  return items.map((item) => ({
    ...item,
    content: item.content ? redactSecrets(item.content) : item.content,
    output: item.output ? redactSecrets(item.output) : item.output,
  }));
}

/**
 * Whether this record's stored output is known to have dropped bytes.
 *
 * Only tool results carry the measurement, and an empty output has nothing to
 * be missing — truncation keeps the first 8 KiB, so a preview that dropped
 * bytes is never empty.
 */
export function isOutputTruncated(item: TimelineItem): boolean {
  return (
    item.type === "tool_result" && (item.output?.length ?? 0) > 0 && item.output_truncated === true
  );
}

/**
 * Timeline items already built, keyed on the first message behind each one.
 *
 * Redaction is essentially the whole cost of building a timeline: coalescing a
 * 3000-message transcript takes ~0.1ms and redacting it ~20ms, because every
 * rule scans every byte of every body. A live run rebuilds its timeline on each
 * 100ms flush window, so that scan was repeating over the entire transcript
 * several times a second while all but its newest messages were byte for byte
 * what they had been on the previous pass (MUL-7227).
 *
 * A message is immutable once it lands — the realtime merge appends unseen seqs
 * and keeps the objects it already holds — so identity is a sound proof that a
 * body has not changed. Each entry records the exact messages it was built
 * from, and is reused only when that list still matches: a coalescing run that
 * gained a fragment is rebuilt, which is what keeps redaction applied to merged
 * text rather than to the pieces.
 *
 * Weak, so entries are collected with the messages they belong to. They hold
 * little: `String.replace` returns its input unchanged when nothing matches, so
 * a body with no secrets in it is shared rather than copied.
 */
const builtRuns = new WeakMap<
  TaskMessagePayload,
  { members: readonly TaskMessagePayload[]; item: TimelineItem }
>();

function sameMembers(a: readonly TaskMessagePayload[], b: readonly TaskMessagePayload[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((message, index) => message === b[index]);
}

/** Merge one coalescing run into its item, exactly as `coalesceTimelineItems` would. */
function mergeRun(run: readonly TaskMessagePayload[]): TimelineItem {
  const first = run[0]!;
  let content = first.content;
  let createdAt = first.created_at;
  for (const message of run.slice(1)) {
    content = `${content ?? ""}${message.content ?? ""}`;
    createdAt = message.created_at ?? createdAt;
  }
  return {
    seq: first.seq,
    type: first.type,
    tool: first.tool,
    callId: first.call_id,
    content,
    input: first.input,
    output: first.output,
    output_truncated: first.output_truncated,
    created_at: createdAt,
  };
}

/** Retains stable redacted prose identity and original fragments for guidance insertion. */
function buildRun(run: readonly TaskMessagePayload[]): TimelineItem {
  const cached = builtRuns.get(run[0]!);
  if (cached && sameMembers(cached.members, run)) return cached.item;
  const item = redactTimelineItems([mergeRun(run)])[0]!;
  if (run.length > 1) proseFragments.set(item, run);
  builtRuns.set(run[0]!, { members: [...run], item });
  return item;
}

/** Build a chronologically ordered timeline from raw task messages. */
export function buildTimeline(msgs: TaskMessagePayload[]): TimelineItem[] {
  const sorted = [...msgs].sort((a, b) => a.seq - b.seq);
  const out: TimelineItem[] = [];
  let run: TaskMessagePayload[] = [];

  for (const msg of sorted) {
    const previous = run[run.length - 1];
    // Same rule as `canMergeStreamingText`, read off the messages: a run's type
    // is its first message's, and every member shares it.
    if (previous && (previous.type === "text" || previous.type === "thinking") && previous.type === msg.type) {
      run.push(msg);
      continue;
    }
    if (run.length > 0) out.push(buildRun(run));
    run = [msg];
  }
  if (run.length > 0) out.push(buildRun(run));

  return out;
}
