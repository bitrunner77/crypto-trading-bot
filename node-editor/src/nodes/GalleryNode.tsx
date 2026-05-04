import type { NodeProps } from "@xyflow/react";
import { NodeShell } from "./NodeShell";
import type { GalleryNodeData } from "../lib/types";

export function GalleryNode({ data }: NodeProps) {
  const d = data as unknown as GalleryNodeData;
  return (
    <NodeShell
      title="Image Gallery"
      icon="◧"
      meta={`${d.images.length} images`}
      showSourceHandle={false}
      width={340}
    >
      <div className="grid grid-cols-3 gap-1.5 p-2">
        {Array.from({ length: Math.max(9, d.images.length) }).map((_, i) => {
          const img = d.images[i];
          return (
            <div
              key={i}
              className="relative aspect-square overflow-hidden rounded bg-black/60"
            >
              {img ? (
                <img
                  src={img.url}
                  alt=""
                  className="absolute inset-0 h-full w-full object-cover"
                  draggable={false}
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-[10px] text-muted">
                  —
                </div>
              )}
            </div>
          );
        })}
      </div>
    </NodeShell>
  );
}
