export type GenStatus = "idle" | "loading" | "ready" | "error";

export type PromptNodeData = {
  label: string;
  prompt: string;
};

export type ImageGenNodeData = {
  status: GenStatus;
  imageUrl?: string;
  error?: string;
  promptOverride?: string;
};

export type VideoGenNodeData = {
  status: GenStatus;
  videoUrl?: string;
  error?: string;
};

export type PromptListItem = {
  id: string;
  text: string;
};

export type PromptListNodeData = {
  prompts: PromptListItem[];
};

export type GalleryNodeData = {
  images: { id: string; url: string }[];
};

export type AppNodeData =
  | PromptNodeData
  | ImageGenNodeData
  | VideoGenNodeData
  | PromptListNodeData
  | GalleryNodeData;
