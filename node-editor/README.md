# Node Editor — AI Image / Video Workflow

A node-based visual workflow editor (think ComfyUI / n8n style) for chaining
text prompts into image and video generators. Built with React + React Flow
(`@xyflow/react`) + Tailwind, with a tiny Express proxy that calls
[Replicate](https://replicate.com) for real generation.

## Layout

```
node-editor/
├── server/         Express proxy to Replicate (image + video)
├── src/
│   ├── nodes/      Custom React Flow nodes
│   ├── components/ Toolbar
│   └── lib/        zustand store, API client, types
└── ...
```

## Node types

| Node           | Inputs                   | Output         | Behavior                                  |
| -------------- | ------------------------ | -------------- | ----------------------------------------- |
| Prompt         | —                        | text           | Editable textarea                         |
| Image Gen      | prompt                   | image url      | Calls `/api/generate-image`               |
| Video Gen      | prompt + (optional) image| video url      | Calls `/api/generate-video`               |
| Prompt List    | —                        | list           | Add / remove / enhance / batch-generate   |
| Gallery        | image batch              | —              | Grid view of batch results                |

Drag from a node's right (orange) dot to another node's left dot to wire them
together.

## Setup

```bash
cd node-editor
cp .env.example .env
# edit .env and paste your REPLICATE_API_TOKEN
npm install
npm run dev
```

Then open http://localhost:5173. The frontend dev server proxies `/api/*` to
the Express server on port 3001.

## Configuration

Environment variables (see `.env.example`):

- `REPLICATE_API_TOKEN` — required for real generation
- `IMAGE_MODEL` — default `black-forest-labs/flux-schnell`
- `VIDEO_MODEL` — default `minimax/video-01`
- `PORT` — backend port (default 3001)

Any text-to-image or text/image-to-video model on Replicate should work; the
proxy passes `prompt` (and `first_frame_image` for video) and returns the first
URL the model emits.

## Scripts

- `npm run dev` — runs Vite + Express together
- `npm run build` — production build of the frontend
- `npm run typecheck` — TS only, no emit
- `npm run start` — run the backend without Vite (for production)

## Notes

- The seed graph mirrors a 7-scene storyboard plus a 10-prompt batch list and
  gallery, matching the reference layout.
- Without a `REPLICATE_API_TOKEN`, the UI is fully functional — only the
  generate endpoints will return 500.
- The backend deliberately holds the API key server-side; the browser never
  sees it.
