import { useGraph } from "../lib/store";
import type { Node } from "@xyflow/react";

const newId = (prefix: string) =>
  `${prefix}-${Math.random().toString(36).slice(2, 7)}`;

function addNode(node: Node) {
  useGraph.setState((s) => ({ nodes: [...s.nodes, node as never] }));
}

const buttons: { label: string; onClick: () => void }[] = [
  {
    label: "+ Prompt",
    onClick: () =>
      addNode({
        id: newId("p"),
        type: "prompt",
        position: { x: 80, y: 80 },
        data: { label: "New Prompt", prompt: "" },
      }),
  },
  {
    label: "+ Image Gen",
    onClick: () =>
      addNode({
        id: newId("i"),
        type: "imageGen",
        position: { x: 360, y: 80 },
        data: { status: "idle" },
      }),
  },
  {
    label: "+ Video Gen",
    onClick: () =>
      addNode({
        id: newId("v"),
        type: "videoGen",
        position: { x: 700, y: 80 },
        data: { status: "idle" },
      }),
  },
  {
    label: "+ Prompt List",
    onClick: () =>
      addNode({
        id: newId("pl"),
        type: "promptList",
        position: { x: 1040, y: 80 },
        data: { prompts: [{ id: newId("plp"), text: "" }] },
      }),
  },
  {
    label: "+ Gallery",
    onClick: () =>
      addNode({
        id: newId("g"),
        type: "gallery",
        position: { x: 1440, y: 80 },
        data: { images: [] },
      }),
  },
];

export function Toolbar() {
  return (
    <div className="flex h-11 w-full items-center gap-2 border-b border-edge bg-panel px-3">
      <span className="mr-3 text-[12px] font-semibold tracking-wider text-ink/90 uppercase">
        Node Editor
      </span>
      {buttons.map((b) => (
        <button
          key={b.label}
          onClick={b.onClick}
          className="rounded border border-edge bg-panel2 px-2.5 py-1 text-[11px] font-medium text-ink hover:border-accent hover:text-accent"
        >
          {b.label}
        </button>
      ))}
      <span className="ml-auto text-[10px] text-muted">
        Drag from a node's right dot to another node's left dot to connect
      </span>
    </div>
  );
}
