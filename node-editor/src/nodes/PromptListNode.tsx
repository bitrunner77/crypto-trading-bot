import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./NodeShell";
import { useGraph } from "../lib/store";
import type { PromptListNodeData } from "../lib/types";

export function PromptListNode({ id, data }: NodeProps) {
  const d = data as unknown as PromptListNodeData;
  const addPromptListItem = useGraph((s) => s.addPromptListItem);
  const removePromptListItem = useGraph((s) => s.removePromptListItem);
  const updatePromptListItem = useGraph((s) => s.updatePromptListItem);
  const enhanceAll = useGraph((s) => s.enhanceAll);
  const runPromptListBatch = useGraph((s) => s.runPromptListBatch);
  const edges = useGraph((s) => s.edges);
  const galleryEdge = edges.find((e) => e.source === id);
  const galleryId = galleryEdge?.target;

  return (
    <NodeShell
      title="Prompt List"
      icon="T"
      meta={`${d.prompts.length} prompts`}
      showTargetHandle={false}
      width={340}
    >
      <div className="max-h-[280px] overflow-y-auto p-2 nodrag">
        {d.prompts.map((p, idx) => (
          <div
            key={p.id}
            className="mb-1 flex items-start gap-1.5 rounded border border-edge bg-panel2/60 p-1.5 hover:border-accent/50"
          >
            <span className="pt-1 text-[10px] text-muted w-4 text-right tabular-nums">
              {idx + 1}
            </span>
            <textarea
              value={p.text}
              rows={2}
              onChange={(e) => updatePromptListItem(id, p.id, e.target.value)}
              className="flex-1 resize-none bg-transparent text-[11px] leading-snug text-ink/90 outline-none placeholder:text-muted"
              placeholder="prompt…"
            />
            <button
              onClick={() => removePromptListItem(id, p.id)}
              className="text-[12px] text-muted hover:text-red-400"
              aria-label="remove"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-2 border-t border-edge p-2">
        <button
          onClick={() => addPromptListItem(id)}
          className="nodrag flex-1 rounded border border-edge bg-panel2 px-2 py-1.5 text-[11px] font-medium text-ink hover:border-accent"
        >
          + Add Prompt
        </button>
        <button
          onClick={() => enhanceAll(id)}
          className="nodrag flex-1 rounded border border-edge bg-panel2 px-2 py-1.5 text-[11px] font-medium text-accent hover:border-accent"
        >
          ✨ Enhance All
        </button>
      </div>
      <div className="border-t border-edge p-2">
        <button
          disabled={!galleryId}
          onClick={() => galleryId && runPromptListBatch(id, galleryId)}
          className="nodrag w-full rounded bg-accent px-2 py-2 text-[11px] font-semibold uppercase tracking-wider text-black hover:bg-accent2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {galleryId
            ? `Generate ${d.prompts.length} Images from Prompts`
            : "Connect a Gallery →"}
        </button>
      </div>
    </NodeShell>
  );
}
