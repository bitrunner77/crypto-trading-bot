import "dotenv/config";
import express, { Request, Response } from "express";
import cors from "cors";
import Replicate from "replicate";

const PORT = Number(process.env.PORT ?? 3001);
const IMAGE_MODEL = process.env.IMAGE_MODEL ?? "black-forest-labs/flux-schnell";
const VIDEO_MODEL = process.env.VIDEO_MODEL ?? "minimax/video-01";

if (!process.env.REPLICATE_API_TOKEN) {
  console.warn(
    "[server] REPLICATE_API_TOKEN is not set. Generation endpoints will return 500 until it is configured in node-editor/.env."
  );
}

const replicate = new Replicate({ auth: process.env.REPLICATE_API_TOKEN });

const app = express();
app.use(cors());
app.use(express.json({ limit: "10mb" }));

app.get("/api/health", (_req, res) => {
  res.json({
    ok: true,
    hasToken: Boolean(process.env.REPLICATE_API_TOKEN),
    imageModel: IMAGE_MODEL,
    videoModel: VIDEO_MODEL,
  });
});

function asUrl(output: unknown): string | null {
  if (!output) return null;
  if (typeof output === "string") return output;
  if (Array.isArray(output)) {
    const first = output[0];
    return typeof first === "string" ? first : null;
  }
  if (typeof output === "object" && output !== null) {
    const maybe = output as { url?: () => string | URL };
    if (typeof maybe.url === "function") {
      const u = maybe.url();
      return typeof u === "string" ? u : u.toString();
    }
  }
  return null;
}

app.post("/api/generate-image", async (req: Request, res: Response) => {
  const { prompt, model } = req.body as { prompt?: string; model?: string };
  if (!prompt || typeof prompt !== "string") {
    return res.status(400).json({ error: "prompt is required" });
  }
  try {
    const output = await replicate.run((model ?? IMAGE_MODEL) as `${string}/${string}`, {
      input: { prompt, aspect_ratio: "3:4", output_format: "webp" },
    });
    const url = asUrl(output);
    if (!url) return res.status(502).json({ error: "model returned no url", raw: output });
    res.json({ url });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[generate-image]", message);
    res.status(500).json({ error: message });
  }
});

app.post("/api/generate-video", async (req: Request, res: Response) => {
  const { prompt, imageUrl, model } = req.body as {
    prompt?: string;
    imageUrl?: string;
    model?: string;
  };
  if (!prompt || typeof prompt !== "string") {
    return res.status(400).json({ error: "prompt is required" });
  }
  try {
    const input: Record<string, unknown> = { prompt };
    if (imageUrl) input.first_frame_image = imageUrl;
    const output = await replicate.run((model ?? VIDEO_MODEL) as `${string}/${string}`, {
      input,
    });
    const url = asUrl(output);
    if (!url) return res.status(502).json({ error: "model returned no url", raw: output });
    res.json({ url });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[generate-video]", message);
    res.status(500).json({ error: message });
  }
});

app.listen(PORT, () => {
  console.log(`[server] listening on http://localhost:${PORT}`);
});
