import { Handle, Position } from "@xyflow/react";
import type { ReactNode } from "react";

type Variant = "dark" | "blue";

type Props = {
  title: string;
  icon: string;
  meta?: string;
  variant?: Variant;
  showSourceHandle?: boolean;
  showTargetHandle?: boolean;
  children: ReactNode;
  width?: number;
};

const headerBg: Record<Variant, string> = {
  dark: "bg-[#1f2530] border-b border-edge",
  blue: "bg-blue-600 border-b border-blue-700",
};

export function NodeShell({
  title,
  icon,
  meta,
  variant = "dark",
  showSourceHandle = true,
  showTargetHandle = true,
  children,
  width = 260,
}: Props) {
  return (
    <div
      className="rounded-lg overflow-hidden border border-edge bg-panel shadow-[0_8px_24px_rgba(0,0,0,0.45)]"
      style={{ width }}
    >
      {showTargetHandle && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-2.5 !h-2.5 !bg-accent !border-2 !border-canvas"
        />
      )}
      <div className={`flex items-center gap-2 px-3 py-2 ${headerBg[variant]}`}>
        <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-black/40 text-[11px] font-semibold text-ink">
          {icon}
        </span>
        <span className="text-[11px] font-semibold tracking-[0.14em] text-ink uppercase">
          {title}
        </span>
        {meta && <span className="ml-auto text-[10px] text-muted">{meta}</span>}
      </div>
      <div className="bg-panel">{children}</div>
      {showSourceHandle && (
        <Handle
          type="source"
          position={Position.Right}
          className="!w-2.5 !h-2.5 !bg-accent !border-2 !border-canvas"
        />
      )}
    </div>
  );
}
