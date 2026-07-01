# Frontend

The React SPA is built with Vite + TypeScript and served from the FastAPI container.

## Tech stack

| Layer | Library |
|-------|---------|
| Framework | React 18 |
| Language | TypeScript |
| Bundler | Vite |
| Routing | React Router v6 |
| Icons | Lucide React |
| Styling | CSS custom properties (design tokens), no CSS framework |

## Pages

| Route | File | Description |
|-------|------|-------------|
| `/` | `Dashboard.tsx` | Connector health badges, pipeline status, recent activity |
| `/connectors` | `Connectors.tsx` | Install/configure connectors |
| `/boards` | `Boards.tsx` | Pipeline activity + Quick Actions panel |
| `/pipelines` | `Pipelines.tsx` | Redirect to first pipeline |
| `/pipelines/:id` | `PipelineEditor.tsx` | Blueprint + Edit mode for pipeline |
| `/pipelines/:id/runs` | `PipelineRuns.tsx` | Run history with per-issue detail |
| `/pipelines/:id/logs` | `PipelineLogs.tsx` | Live SSE log viewer |
| `/agents` | `Agents.tsx` | Agent library — CRUD + import/export |
| `/skills` | `Skills.tsx` | Skill browser + file editor |
| `/sessions` | `Sessions.tsx` | Copilot session history + cost |
| `/environment` | `Environment.tsx` | Variables, secrets, files |
| `/settings` | `Settings.tsx` | Server settings |
| `/wizard` | `Wizard.tsx` | First-run setup flow |

## App shell (App.tsx)

- Fixed sidebar with nav links
- Top icon bar (theme toggle, menu toggle)
- Main content area (scrollable)
- React Router `<Routes>` for page rendering

## Design system

CSS custom properties defined in `index.css`:

```css
--bg          /* page background */
--surface     /* card background */
--surface2    /* elevated surface */
--border      /* dividers */
--text        /* primary text */
--muted       /* secondary text */
--accent      /* brand color */
--blue        /* info / links */
--green       /* success */
--yellow      /* warning */
--red         /* error */
```

Light/dark themes swap these tokens. Toggle in the sidebar.

## Component patterns

### Data fetching
No state management library — plain `fetch()` in `useEffect` and `useCallback`.
```tsx
const [items, setItems] = useState<Item[]>([])
const load = useCallback(() => {
  fetch('/api/items').then(r => r.json()).then(d => setItems(d))
}, [])
useEffect(() => { load() }, [load])
```

### Inline editing
Click a row to load data into local `editing` state:
```tsx
const [editing, setEditing] = useState<Item | null>(null)
```
Save/cancel buttons appear inline. No modal dialog.

### SSE streams
```tsx
useEffect(() => {
  const es = new EventSource(`/api/pipelines/${id}/log`)
  es.onmessage = e => {
    const ev = JSON.parse(e.data)
    setLogs(prev => [...prev, ev])
  }
  return () => es.close()
}, [id])
```

## Pipeline Editor (PipelineEditor.tsx)

The most complex page (~1200 lines). Two modes:

**Blueprint mode** (default):
- Orchestrator section (if assigned)
- Stage Flow — cards in a horizontal scrollable row
- Each card: agent name, model badge, skills chips, tools chips
- All cards stretch to tallest in row (`align-items: stretch`)
- Shared Tools bar chart (tool usage across all stages)
- Pipeline Stats (run count, success rate, status indicator)

**Edit mode**:
- Orchestrator picker (filtered to `is_orchestrator = true`)
- Stage CRUD (add/remove/reorder)
- Per-stage: column name, actor, agent assignment, prompt editor with variable chips
- YAML import modal (paste or upload)
- YAML export (download button)
- Ctrl+S shortcut for save

## Boards page (Boards.tsx)

Two-column layout:
- **Left**: pipeline activity (last run, current status, running issue)
- **Right**: Quick Actions panel (sticky)

Quick Actions panel:
- Per-action: always-visible textarea (thoughts) + title input + Generate button
- Generated body appears below (editable textarea)
- Publish button → creates issue(s) on GitHub board
- Skill badge shows selected skill name

## Development

```bash
# Start Vite dev server (with proxy to backend)
npm --prefix web run dev
# → http://localhost:5173 (proxies /api/* to localhost:9400)

# Build for production
npm --prefix web run build
# → outputs to server/static/

# Type-check only
npm --prefix web run tsc -- --noEmit
```

## Hot deploy (frontend only)

No server restart required:
```bash
npm --prefix web run build && docker cp server/static/. gru-server-dev:/app/server/static/
```

The browser gets the new JS/CSS on next hard refresh.
