import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./NodeShell";
import { useGraph } from "../lib/store";
import type { PromptNodeData } from "../lib/types";

export function PromptNode({ id, data }: NodeProps) {
  const d = data as unknown as PromptNodeData;
  const setPromptText = useGraph((s) => s.setPromptText);
  return (
    <NodeShell title={d.label || "Prompt"} icon="T" showTargetHandle={false} width={240}>
      <textarea
        value={d.prompt}
        onChange={(e) => setPromptText(id, e.target.value)}
        placeholder="Describe the scene…"
        rows={5}
        className="w-full resize-none bg-transparent px-3 py-2 text-[12px] leading-snug text-ink/90 outline-none placeholder:text-muted nodrag"
      />
    </NodeShell>
  );
}
