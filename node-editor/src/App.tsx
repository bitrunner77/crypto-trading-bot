import { useEffect, useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useGraph } from "./lib/store";
import { PromptNode } from "./nodes/PromptNode";
import { ImageGenNode } from "./nodes/ImageGenNode";
import { VideoGenNode } from "./nodes/VideoGenNode";
import { PromptListNode } from "./nodes/PromptListNode";
import { GalleryNode } from "./nodes/GalleryNode";
import { Toolbar } from "./components/Toolbar";

const SCENES: { label: string; prompt: string }[] = [
  {
    label: "Scene 1 Prompt",
    prompt:
      "Wide cinematic shot of a sleek, modern urban plaza at early morning blue hour. Minimalist concrete architecture, smooth polished surfaces, and a subtle interplay of cool, desaturated blues and greys dominate the frame.",
  },
  {
    label: "Scene 2 Prompt",
    prompt:
      "Medium shot, low angle, of a young adult (20s) male, athletic build, short dark hair, wearing minimalist athletic wear, with a confident, focused expression.",
  },
  {
    label: "Scene 3 Prompt",
    prompt:
      "Ultra close-up, low angle, of the lateral side of a Nike Air Max 270 shoe (white mesh upper, light grey overlays, black Nike Swoosh, white laces, translucent light blue to cyan air unit).",
  },
  {
    label: "Scene 4 Prompt",
    prompt:
      "Dynamic medium shot of the lower legs of a young adult (20s) male mid-stride, athletic build, wearing minimalist athletic wear with clean white socks.",
  },
  {
    label: "Scene 5 Prompt",
    prompt:
      "Close-up shot of the lateral side of a pristine Nike Air Max 270 shoe with translucent cyan air unit, on polished concrete.",
  },
  {
    label: "Scene 6 Prompt",
    prompt:
      "Medium wide shot of a Nike Air Max 270 shoe artfully placed on a polished concrete surface in soft diffused light.",
  },
  {
    label: "Scene 7 Prompt",
    prompt:
      "Wide cinematic shot of a young adult (20s) male, athletic build, mid-stride in an urban concrete park, golden hour rim light.",
  },
];

function buildSeed(): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  const X_PROMPT = 0;
  const X_IMAGE = 290;
  const X_VIDEO = 620;
  const Y_GAP = 380;

  SCENES.forEach((scene, i) => {
    const y = i * Y_GAP;
    const promptId = `p-${i + 1}`;
    const imageId = `i-${i + 1}`;
    const videoId = `v-${i + 1}`;

    nodes.push({
      id: promptId,
      type: "prompt",
      position: { x: X_PROMPT, y: y + 60 },
      data: { label: scene.label, prompt: scene.prompt },
    });
    nodes.push({
      id: imageId,
      type: "imageGen",
      position: { x: X_IMAGE, y },
      data: { status: "idle" },
    });
    nodes.push({
      id: videoId,
      type: "videoGen",
      position: { x: X_VIDEO, y },
      data: { status: "idle" },
    });

    edges.push({
      id: `e-${promptId}-${imageId}`,
      source: promptId,
      target: imageId,
      animated: true,
      style: { stroke: "#ff7a1a", strokeWidth: 2 },
    });
    edges.push({
      id: `e-${imageId}-${videoId}`,
      source: imageId,
      target: videoId,
      animated: true,
      style: { stroke: "#ff7a1a", strokeWidth: 2 },
    });
    edges.push({
      id: `e-${promptId}-${videoId}`,
      source: promptId,
      target: videoId,
      animated: true,
      style: { stroke: "#ff7a1a", strokeWidth: 2 },
    });
  });

  const sharedPromptText =
    "Late afternoon in an outdoor urban concrete park, soft diffused light, cool blue and grey";
  nodes.push({
    id: "prompt-list",
    type: "promptList",
    position: { x: 1000, y: 80 },
    data: {
      prompts: Array.from({ length: 10 }).map((_, i) => ({
        id: `pl-${i + 1}`,
        text: sharedPromptText,
      })),
    },
  });
  nodes.push({
    id: "gallery",
    type: "gallery",
    position: { x: 1400, y: 80 },
    data: { images: [] },
  });
  edges.push({
    id: "e-list-gallery",
    source: "prompt-list",
    target: "gallery",
    animated: true,
    style: { stroke: "#ff7a1a", strokeWidth: 2 },
  });

  return { nodes, edges };
}

function Flow() {
  const nodes = useGraph((s) => s.nodes);
  const edges = useGraph((s) => s.edges);
  const onNodesChange = useGraph((s) => s.onNodesChange);
  const onEdgesChange = useGraph((s) => s.onEdgesChange);
  const onConnect = useGraph((s) => s.onConnect);

  const nodeTypes = useMemo(
    () => ({
      prompt: PromptNode,
      imageGen: ImageGenNode,
      videoGen: VideoGenNode,
      promptList: PromptListNode,
      gallery: GalleryNode,
    }),
    []
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.2}
      maxZoom={1.5}
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{
        animated: true,
        style: { stroke: "#ff7a1a", strokeWidth: 2 },
      }}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={24}
        size={1.2}
        color="#1c2230"
      />
      <Controls className="!bg-panel !border-edge !rounded-md overflow-hidden" />
      <MiniMap
        pannable
        zoomable
        className="!bg-panel !border !border-edge !rounded-md"
        nodeColor="#1c2230"
        maskColor="rgba(11,14,19,0.8)"
      />
    </ReactFlow>
  );
}

export function App() {
  useEffect(() => {
    if (useGraph.getState().nodes.length === 0) {
      const seed = buildSeed();
      useGraph.setState({ nodes: seed.nodes, edges: seed.edges });
    }
  }, []);

  return (
    <div className="h-screen w-screen bg-canvas">
      <Toolbar />
      <div className="h-[calc(100%-44px)] w-full">
        <ReactFlowProvider>
          <Flow />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
