import { create } from "zustand";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { generateImage, generateVideo } from "./api";
import type {
  GalleryNodeData,
  ImageGenNodeData,
  PromptListNodeData,
  PromptNodeData,
  VideoGenNodeData,
} from "./types";

type AnyNode = Node<Record<string, unknown>>;

type State = {
  nodes: AnyNode[];
  edges: Edge[];
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  patchNodeData: <T extends Record<string, unknown>>(id: string, patch: Partial<T>) => void;
  setPromptText: (id: string, prompt: string) => void;
  addPromptListItem: (id: string) => void;
  removePromptListItem: (id: string, itemId: string) => void;
  updatePromptListItem: (id: string, itemId: string, text: string) => void;
  enhanceAll: (id: string) => void;
  runImageNode: (id: string) => Promise<void>;
  runVideoNode: (id: string) => Promise<void>;
  runPromptListBatch: (promptListId: string, galleryId: string) => Promise<void>;
};

const idGen = () => Math.random().toString(36).slice(2, 9);

function getIncomingPrompt(nodes: AnyNode[], edges: Edge[], targetId: string): string | null {
  const incoming = edges.find((e) => e.target === targetId);
  if (!incoming) return null;
  const source = nodes.find((n) => n.id === incoming.source);
  if (!source) return null;
  if (source.type === "prompt") {
    return ((source.data as unknown as PromptNodeData).prompt ?? "").trim() || null;
  }
  if (source.type === "imageGen") {
    return ((source.data as unknown as ImageGenNodeData).promptOverride ?? "").trim() || null;
  }
  return null;
}

function getIncomingImageUrl(nodes: AnyNode[], edges: Edge[], targetId: string): string | null {
  const incomers = edges.filter((e) => e.target === targetId);
  for (const e of incomers) {
    const source = nodes.find((n) => n.id === e.source);
    if (source?.type === "imageGen") {
      const url = (source.data as unknown as ImageGenNodeData).imageUrl;
      if (url) return url;
    }
  }
  return null;
}

export const useGraph = create<State>((set, get) => ({
  nodes: [],
  edges: [],

  onNodesChange: (changes) =>
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes) as AnyNode[] })),
  onEdgesChange: (changes) => set((s) => ({ edges: applyEdgeChanges(changes, s.edges) })),
  onConnect: (connection) =>
    set((s) => ({
      edges: addEdge(
        { ...connection, animated: true, style: { stroke: "#ff7a1a", strokeWidth: 2 } },
        s.edges
      ),
    })),

  patchNodeData: (id, patch) =>
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...patch } } : n
      ),
    })),

  setPromptText: (id, prompt) => get().patchNodeData<PromptNodeData>(id, { prompt }),

  addPromptListItem: (id) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n;
        const data = n.data as unknown as PromptListNodeData;
        return {
          ...n,
          data: {
            ...data,
            prompts: [...data.prompts, { id: idGen(), text: "" }],
          } as unknown as Record<string, unknown>,
        };
      }),
    })),

  removePromptListItem: (id, itemId) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n;
        const data = n.data as unknown as PromptListNodeData;
        return {
          ...n,
          data: {
            ...data,
            prompts: data.prompts.filter((p) => p.id !== itemId),
          } as unknown as Record<string, unknown>,
        };
      }),
    })),

  updatePromptListItem: (id, itemId, text) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n;
        const data = n.data as unknown as PromptListNodeData;
        return {
          ...n,
          data: {
            ...data,
            prompts: data.prompts.map((p) => (p.id === itemId ? { ...p, text } : p)),
          } as unknown as Record<string, unknown>,
        };
      }),
    })),

  enhanceAll: (id) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n;
        const data = n.data as unknown as PromptListNodeData;
        return {
          ...n,
          data: {
            ...data,
            prompts: data.prompts.map((p) => ({
              ...p,
              text: p.text
                ? `${p.text}, cinematic lighting, ultra-detailed, 35mm film, shallow depth of field`
                : p.text,
            })),
          } as unknown as Record<string, unknown>,
        };
      }),
    })),

  runImageNode: async (id) => {
    const { nodes, edges, patchNodeData } = get();
    const node = nodes.find((n) => n.id === id);
    if (!node) return;
    const data = node.data as unknown as ImageGenNodeData;
    const prompt = (data.promptOverride && data.promptOverride.trim()) ||
      getIncomingPrompt(nodes, edges, id);
    if (!prompt) {
      patchNodeData<ImageGenNodeData>(id, {
        status: "error",
        error: "no upstream prompt connected",
      });
      return;
    }
    patchNodeData<ImageGenNodeData>(id, { status: "loading", error: undefined });
    try {
      const url = await generateImage(prompt);
      patchNodeData<ImageGenNodeData>(id, { status: "ready", imageUrl: url });
    } catch (err) {
      patchNodeData<ImageGenNodeData>(id, {
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  runVideoNode: async (id) => {
    const { nodes, edges, patchNodeData } = get();
    const prompt = getIncomingPrompt(nodes, edges, id);
    const imageUrl = getIncomingImageUrl(nodes, edges, id);
    if (!prompt && !imageUrl) {
      patchNodeData<VideoGenNodeData>(id, {
        status: "error",
        error: "connect a prompt or image",
      });
      return;
    }
    patchNodeData<VideoGenNodeData>(id, { status: "loading", error: undefined });
    try {
      const url = await generateVideo(prompt ?? "animate this image", imageUrl ?? undefined);
      patchNodeData<VideoGenNodeData>(id, { status: "ready", videoUrl: url });
    } catch (err) {
      patchNodeData<VideoGenNodeData>(id, {
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },

  runPromptListBatch: async (promptListId, galleryId) => {
    const { nodes, patchNodeData } = get();
    const list = nodes.find((n) => n.id === promptListId);
    if (!list) return;
    const prompts = (list.data as unknown as PromptListNodeData).prompts.filter((p) =>
      p.text.trim()
    );
    patchNodeData<GalleryNodeData>(galleryId, { images: [] });
    const results: { id: string; url: string }[] = [];
    await Promise.all(
      prompts.map(async (p) => {
        try {
          const url = await generateImage(p.text);
          results.push({ id: p.id, url });
          patchNodeData<GalleryNodeData>(galleryId, { images: [...results] });
        } catch {
          /* ignore individual failures */
        }
      })
    );
  },
}));
