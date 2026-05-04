import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./NodeShell";
import { useGraph } from "../lib/store";
import type { ImageGenNodeData } from "../lib/types";

export function ImageGenNode({ id, data }: NodeProps) {
  const d = data as unknown as ImageGenNodeData;
  const runImageNode = useGraph((s) => s.runImageNode);
  const isLoading = d.status === "loading";

  return (
    <NodeShell title="Image Generator" icon="◧" width={260}>
      <div className="relative aspect-[3/4] w-full overflow-hidden bg-black/60">
        {d.imageUrl ? (
          <img
            src={d.imageUrl}
            alt=""
            className="absolute inset-0 h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-[11px] text-muted">
            {isLoading ? "Generating…" : "No image"}
          </div>
        )}
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50 text-[11px] text-ink">
            <span className="animate-pulse">generating…</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-edge p-2">
        <button
          onClick={() => runImageNode(id)}
          disabled={isLoading}
          className="nodrag flex-1 rounded bg-accent px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-black hover:bg-accent2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoading ? "Working…" : d.imageUrl ? "Regenerate" : "Generate"}
        </button>
        {d.error && (
          <span title={d.error} className="text-[10px] text-red-400">
            error
          </span>
        )}
      </div>
    </NodeShell>
  );
}
