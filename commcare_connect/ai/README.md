# AI App

AI agent integration using pydantic-ai with SSE streaming. Agents modify workflow definitions, pipeline schemas, render code, and solicitations via tool functions.

Unlike other labs apps, the AI app has **no data_access.py** — agents call into other apps' DataAccess classes via tool functions.

## Key Files

| File                           | Purpose                                                                  |
| ------------------------------ | ------------------------------------------------------------------------ |
| `views.py`                     | `AIStreamView` — single POST endpoint for all agent types, SSE streaming |
| `agents/workflow_agent.py`     | Edits workflow definitions, render code, pipeline schemas via tools      |
| `agents/pipeline_agent.py`     | Edits standalone pipeline schemas and visualizations                     |
| `agents/solicitation_agent.py` | Queries solicitations (read-only, uses GPT-4o-mini)                      |
| `types.py`                     | `UserDependencies` dataclass — shared context for all agents             |
| `session_store.py`             | Redis-backed message history (optional, 7-day TTL)                       |
| `urls.py`                      | Single route: `POST /ai/stream/`                                         |

## Architecture

**Single endpoint:** `POST /ai/stream/` receives `{agent_type, prompt, definition, schema, ...}` and returns SSE events:

- `delta` — streaming text tokens
- `complete` — final event with updated definitions, schemas, render code

**Agent pattern (pydantic-ai):**

```python
agent = Agent(model="anthropic:claude-sonnet-...", deps_type=AgentDeps)

@agent.tool
async def update_definition(ctx: RunContext[AgentDeps], definition: dict) -> str:
    ctx.deps.pending_definition = definition  # Modify shared state
    return "Updated definition"
```

Tools mutate a `deps` dataclass during streaming. The view collects pending changes from deps after the agent finishes and returns them in the `complete` event.

## Agents

**Workflow Agent** (`workflow_agent.py`):

- Tools: `update_definition`, `update_render_code`, `add_pipeline_source`, `remove_pipeline_source`, `update_pipeline_schema`
- Models: Claude Sonnet/Opus
- Context-aware: tracks active tab (workflow vs pipeline)

**Pipeline Agent** (`pipeline_agent.py`):

- Tools: `update_schema`, `update_render_code`
- Models: Claude Sonnet/Opus
- Understands pipeline schema structure (fields, aggregations, grouping_key, terminal_stage)

**Solicitation Agent** (`solicitation_agent.py`):

- Tools: `list_solicitations`, `get_program_details`, `list_programs`, `list_organizations`, `list_opportunities`
- Model: GPT-4o-mini
- Read-only (no create/update tools yet)

## Cross-App Connections

- **Depends on:** `workflow/` (WorkflowDataAccess, PipelineDataAccess for saving chat history), `solicitations/` (SolicitationsDataAccess)
- **Used by:** Called from workflow editor and pipeline editor UIs

## Testing

```bash
pytest commcare_connect/ai/
```

Mock pydantic-ai agents and DataAccess classes.
