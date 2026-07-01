"""
Unified Workflow Agent for editing workflow definitions, pipeline schemas, and UI via AI.

This agent uses tools instead of structured output to enable real-time streaming
of the AI's explanation while capturing structured updates via tool calls.

Architecture:
- Agent outputs plain text (streams naturally token-by-token)
- update_definition() tool captures workflow definition changes
- update_render_code() tool captures UI code changes
- add_pipeline_source() / remove_pipeline_source() tools manage data sources
- Tools are called during streaming, results available after completion

Workflows can reference Pipelines as data sources. The workflow UI receives
pipeline data via the `pipelines` prop, keyed by alias.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from connect_labs.ai.types import UserDependencies

logger = logging.getLogger(__name__)

_REFERENCE_PATH = Path(__file__).resolve().parents[2] / "workflow" / "WORKFLOW_REFERENCE.md"


def _load_reference() -> str:
    """Load the workflow reference document for agent context."""
    try:
        return _REFERENCE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Workflow reference not found at {_REFERENCE_PATH}")
        return ""


_WORKFLOW_REFERENCE = _load_reference()

WORKFLOW_AGENT_INSTRUCTIONS = f"""
You are an expert helping users build data-driven workflows with custom React UIs.

## Context Awareness

The user may be viewing different parts of the workflow:
- **Workflow tab**: Focus on workflow definition and render code (UI)
- **Pipeline tab**: Focus on pipeline schema (data extraction configuration)

The prompt will include "Active Context" telling you which tab the user is on.
Use this to determine which tools to call:
- On workflow tab: primarily use update_definition() and update_render_code()
- On pipeline tab: primarily use update_pipeline_schema(), but also update_render_code() if UI changes are needed
- Some requests may require changes to both (e.g., "add a new data field and display it")

## Workflow Engine Reference

{_WORKFLOW_REFERENCE}

## Tools Available

- `update_definition(definition)` - Update workflow definition (statuses, config, pipeline_sources)
- `update_render_code(code)` - Update the React UI component
- `add_pipeline_source(pipeline_id, alias)` - Add a pipeline as a data source
- `remove_pipeline_source(alias)` - Remove a pipeline source by alias
- `update_pipeline_schema(pipeline_id, schema)` - Update a pipeline's data extraction schema

## When to Use Which Tool

- User asks about statuses/config/settings -> call update_definition
- User asks about layout/display/UI -> call update_render_code
- User wants to add data from a pipeline -> call add_pipeline_source
- User wants to remove a data source -> call remove_pipeline_source
- User asks about both definition and UI -> call both tools
- User is on pipeline tab and asks to add/modify fields -> call update_pipeline_schema
- User wants to add a field to pipeline AND show it in UI -> call update_pipeline_schema AND update_render_code

## CRITICAL RULES

1. ALWAYS call the appropriate tool(s) when the user requests ANY change
2. First briefly explain what you will change (1-2 sentences max), then IMMEDIATELY call the tool(s)
3. When calling update_render_code, provide the COMPLETE code, not snippets
4. When calling update_definition, provide the COMPLETE definition object
5. Do NOT just explain what you would do - you MUST actually call the tools to make changes
6. If the user asks to add/change/remove anything in the UI, you MUST call update_render_code
7. If the user asks to change statuses/config/settings, you MUST call update_definition
8. After calling tools, briefly confirm what was changed

IMPORTANT: If a user says "add X" or "change Y" or "show Z", you MUST call the tools. Do not just describe the changes - MAKE them.
"""


@dataclass
class WorkflowAgentDeps:
    """Dependencies for workflow agent that capture tool results."""

    user_deps: UserDependencies
    pending_definition: dict | None = None
    pending_render_code: str | None = None
    pending_pipeline_actions: list = field(default_factory=list)
    pending_pipeline_schema_updates: dict = field(default_factory=dict)  # {pipeline_id: schema}
    definition_changed: bool = False
    render_code_changed: bool = False
    pipeline_schema_changed: bool = False


def create_workflow_agent_with_model(model: str) -> Agent[WorkflowAgentDeps, str]:
    """
    Create the workflow agent with a specific model.

    Args:
        model: Full model string (e.g., 'anthropic:claude-sonnet-4-6', 'openai:gpt-4o')

    Returns:
        Agent configured with the specified model
    """
    logger.info(f"[Workflow Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=WorkflowAgentDeps,
        output_type=str,  # Plain text output enables smooth streaming
        instructions=WORKFLOW_AGENT_INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=16384),
    )
    logger.info("[Workflow Agent] Agent created, registering tools...")

    @agent.tool
    async def update_definition(ctx: RunContext[WorkflowAgentDeps], definition: dict) -> str:
        """
        Update the workflow definition JSON.

        Call this tool when you need to modify the workflow configuration,
        including name, description, statuses, config options, or pipeline_sources.

        Args:
            definition: Complete workflow definition object with name, description,
                       version, statuses, config, and optionally pipeline_sources.
        """
        logger.info("[Workflow Agent] TOOL CALLED: update_definition")
        ctx.deps.pending_definition = definition
        ctx.deps.definition_changed = True
        return "Workflow definition will be updated."

    @agent.tool
    async def update_render_code(ctx: RunContext[WorkflowAgentDeps], code: str) -> str:
        """
        Update the React render code for the workflow UI.

        Call this tool when you need to modify how the workflow is displayed.
        Always provide the COMPLETE component code, not just the changed parts.

        The component should be named WorkflowUI and receive these props:
        definition, instance, workers, pipelines, links, actions, onUpdateState

        Args:
            code: Complete React functional component code as a string.
        """
        logger.info(f"[Workflow Agent] TOOL CALLED: update_render_code (code length: {len(code)})")
        ctx.deps.pending_render_code = code
        ctx.deps.render_code_changed = True
        return "Render code will be updated."

    @agent.tool
    async def add_pipeline_source(ctx: RunContext[WorkflowAgentDeps], pipeline_id: int, alias: str) -> str:
        """
        Add a pipeline as a data source for this workflow.

        The pipeline data will be available in render code via pipelines[alias].

        Args:
            pipeline_id: ID of the pipeline to add as a data source
            alias: Name to use when accessing this pipeline's data (e.g., "visits", "metrics")
        """
        logger.info(f"[Workflow Agent] TOOL CALLED: add_pipeline_source({pipeline_id}, {alias})")
        ctx.deps.pending_pipeline_actions.append(
            {
                "action": "add",
                "pipeline_id": pipeline_id,
                "alias": alias,
            }
        )
        return f"Pipeline {pipeline_id} will be added as '{alias}' data source."

    @agent.tool
    async def remove_pipeline_source(ctx: RunContext[WorkflowAgentDeps], alias: str) -> str:
        """
        Remove a pipeline data source from this workflow.

        Args:
            alias: The alias of the pipeline source to remove
        """
        logger.info(f"[Workflow Agent] TOOL CALLED: remove_pipeline_source({alias})")
        ctx.deps.pending_pipeline_actions.append(
            {
                "action": "remove",
                "alias": alias,
            }
        )
        return f"Pipeline source '{alias}' will be removed."

    @agent.tool
    async def update_pipeline_schema(ctx: RunContext[WorkflowAgentDeps], pipeline_id: int, schema: dict) -> str:
        """
        Update a pipeline's schema (fields, grouping, filters).

        Call this when the user wants to modify what data a pipeline extracts.
        Use this when the user is focused on a pipeline tab or asks to modify pipeline fields.

        The schema should include:
        - name: Pipeline name
        - description: What the pipeline extracts
        - grouping_key: How to group data ("username", "entity_id", "deliver_unit_id")
        - terminal_stage: Output level ("visit_level" or "aggregated")
        - fields: Array of field definitions with name, path, aggregation, description

        Field definition structure:
        {
            "name": "field_name",
            "path": "form.path.to.value",
            "aggregation": "first|last|sum|avg|count|min|max|list|count_unique",
            "description": "What this field represents"
        }

        Args:
            pipeline_id: ID of the pipeline to update
            schema: Complete pipeline schema object
        """
        logger.info(f"[Workflow Agent] TOOL CALLED: update_pipeline_schema({pipeline_id})")
        ctx.deps.pending_pipeline_schema_updates[pipeline_id] = schema
        ctx.deps.pipeline_schema_changed = True
        return f"Pipeline {pipeline_id} schema will be updated."

    logger.info("[Workflow Agent] All tools registered successfully")
    return agent


# Convenience functions for specific models
def create_workflow_agent() -> Agent[WorkflowAgentDeps, str]:
    """Create the workflow agent with default Claude Sonnet 4 model."""
    return create_workflow_agent_with_model("anthropic:claude-sonnet-4-6")


def create_workflow_agent_openai() -> Agent[WorkflowAgentDeps, str]:
    """Create the workflow agent with GPT-4o."""
    return create_workflow_agent_with_model("openai:gpt-4o")


def build_workflow_prompt(
    user_prompt: str,
    current_definition: dict | None = None,
    current_render_code: str | None = None,
    available_pipelines: list[dict] | None = None,
    active_context: dict | None = None,
    conversation_history: list | None = None,
) -> str:
    """
    Build the prompt for the workflow agent including current context.

    Args:
        user_prompt: The user's request
        current_definition: The current workflow definition JSON
        current_render_code: The current React render code
        available_pipelines: List of available pipelines user can add as sources
        active_context: Dict with active_tab, pipeline_id, pipeline_schema if on pipeline tab
        conversation_history: List of prior messages [{role, content}, ...]

    Returns:
        The full prompt to send to the agent
    """
    prompt_parts = []

    # Add active context first so the agent knows what the user is focused on
    if active_context:
        active_tab = active_context.get("active_tab", "workflow")
        prompt_parts.append("## Active Context")
        if active_tab == "workflow":
            prompt_parts.append("The user is viewing the **Workflow** tab (UI/render code focused).")
        elif active_tab == "pipeline":
            pipeline_id = active_context.get("pipeline_id")
            pipeline_alias = active_context.get("pipeline_alias", "")
            prompt_parts.append(f"The user is viewing the **Pipeline** tab: '{pipeline_alias}' (ID: {pipeline_id})")
            prompt_parts.append("Focus on pipeline schema modifications unless the user explicitly asks about UI.")

            # Include pipeline schema if available
            pipeline_schema = active_context.get("pipeline_schema")
            if pipeline_schema:
                prompt_parts.append("")
                prompt_parts.append("### Current Pipeline Schema")
                prompt_parts.append(f"```json\n{json.dumps(pipeline_schema, indent=2)}\n```")
        prompt_parts.append("")

    if current_definition:
        prompt_parts.append("## Current Workflow Definition")
        prompt_parts.append(f"```json\n{json.dumps(current_definition, indent=2)}\n```")
        prompt_parts.append("")

    if current_render_code:
        prompt_parts.append("## Current Render Code")
        prompt_parts.append(f"```jsx\n{current_render_code}\n```")
        prompt_parts.append("")

    if available_pipelines:
        prompt_parts.append("## Available Pipelines")
        prompt_parts.append("These pipelines can be added as data sources:")
        for p in available_pipelines:
            prompt_parts.append(f"- ID {p['id']}: {p['name']} - {p.get('description', 'No description')}")
        prompt_parts.append("")

    # Include conversation history if present
    if conversation_history:
        prompt_parts.append("## Conversation History")
        for msg in conversation_history:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            prompt_parts.append(f"**{role}:** {content}")
            prompt_parts.append("")

    prompt_parts.append(f"## User Request\n{user_prompt}")

    return "\n".join(prompt_parts)


# Default render code template for new workflows
DEFAULT_RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    const [sortBy, setSortBy] = React.useState('name');
    const [filterStatus, setFilterStatus] = React.useState('all');

    const statuses = definition.statuses || [];
    const workerStates = instance.state?.worker_states || {};
    const config = definition.config || {};

    // Calculate stats
    const stats = React.useMemo(() => {
        const counts = {};
        statuses.forEach(s => { counts[s.id] = 0; });
        workers.forEach(w => {
            const status = workerStates[w.username]?.status || 'pending';
            counts[status] = (counts[status] || 0) + 1;
        });
        return {
            total: workers.length,
            reviewed: workers.length - (counts['pending'] || 0),
            counts
        };
    }, [workers, workerStates, statuses]);

    // Filter workers
    const displayWorkers = React.useMemo(() => {
        let filtered = workers;
        if (filterStatus !== 'all') {
            filtered = workers.filter(w =>
                (workerStates[w.username]?.status || 'pending') === filterStatus
            );
        }
        return [...filtered].sort((a, b) => {
            if (sortBy === 'name') return (a.name || a.username).localeCompare(b.name || b.username);
            if (sortBy === 'visits') return b.visit_count - a.visit_count;
            return 0;
        });
    }, [workers, workerStates, filterStatus, sortBy]);

    const handleStatusChange = async (username, newStatus) => {
        await onUpdateState({
            worker_states: {
                ...workerStates,
                [username]: { ...workerStates[username], status: newStatus }
            }
        });
    };

    const getStatusColor = (statusId) => {
        const colorMap = {
            gray: 'bg-gray-100 text-gray-800',
            green: 'bg-green-100 text-green-800',
            yellow: 'bg-yellow-100 text-yellow-800',
            blue: 'bg-blue-100 text-blue-800',
            red: 'bg-red-100 text-red-800',
            purple: 'bg-purple-100 text-purple-800',
            orange: 'bg-orange-100 text-orange-800',
            pink: 'bg-pink-100 text-pink-800'
        };
        const status = statuses.find(s => s.id === statusId);
        return colorMap[status?.color] || colorMap.gray;
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                        <p className="text-gray-600 mt-1">{definition.description}</p>
                    </div>
                    <div className="text-sm text-gray-500">
                        {instance.state?.period_start} - {instance.state?.period_end}
                    </div>
                </div>
            </div>

            {/* Summary Cards */}
            {config.showSummaryCards !== false && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-white p-4 rounded-lg shadow-sm">
                        <div className="text-3xl font-bold text-gray-900">{stats.total}</div>
                        <div className="text-gray-600">Total Workers</div>
                    </div>
                    <div className="bg-green-50 p-4 rounded-lg shadow-sm border border-green-200">
                        <div className="text-3xl font-bold text-green-700">{stats.reviewed}</div>
                        <div className="text-gray-600">Reviewed</div>
                    </div>
                    {statuses.slice(0, 2).map(status => (
                        <div key={status.id} className={"p-4 rounded-lg shadow-sm " + getStatusColor(status.id)}>
                            <div className="text-2xl font-bold">{stats.counts[status.id] || 0}</div>
                            <div className="text-sm">{status.label}</div>
                        </div>
                    ))}
                </div>
            )}

            {/* Filters */}
            {config.showFilters !== false && (
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="flex flex-wrap gap-4 items-center">
                        <select
                            value={filterStatus}
                            onChange={e => setFilterStatus(e.target.value)}
                            className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                        >
                            <option value="all">All Statuses</option>
                            {statuses.map(s => (
                                <option key={s.id} value={s.id}>{s.label}</option>
                            ))}
                        </select>
                        <select
                            value={sortBy}
                            onChange={e => setSortBy(e.target.value)}
                            className="border border-gray-300 rounded-md px-3 py-2 text-sm"
                        >
                            <option value="name">Sort by Name</option>
                            <option value="visits">Sort by Visits</option>
                        </select>
                        <div className="ml-auto text-sm text-gray-500">
                            Showing {displayWorkers.length} of {workers.length} workers
                        </div>
                    </div>
                </div>
            )}

            {/* Worker Table */}
            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Worker</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Visits</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Last Active</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {displayWorkers.map(worker => {
                            const currentStatus = workerStates[worker.username]?.status || 'pending';
                            return (
                                <tr key={worker.username} className="hover:bg-gray-50">
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="font-medium text-gray-900">{worker.name || worker.username}</div>
                                        <div className="text-sm text-gray-500">{worker.username}</div>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                        {worker.visit_count || 0}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                        {worker.last_active || 'Never'}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <select
                                            value={currentStatus}
                                            onChange={e => handleStatusChange(worker.username, e.target.value)}
                                            className="border rounded px-2 py-1 text-sm"
                                        >
                                            {statuses.map(s => (
                                                <option key={s.id} value={s.id}>{s.label}</option>
                                            ))}
                                        </select>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                                        <div className="flex gap-2">
                                            <a
                                                href={links.auditUrl({ username: worker.username, count: 5 })}
                                                className="text-blue-600 hover:text-blue-800"
                                            >
                                                Audit
                                            </a>
                                            <a
                                                href={links.taskUrl({ username: worker.username })}
                                                className="text-blue-600 hover:text-blue-800"
                                            >
                                                Task
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {displayWorkers.length === 0 && (
                    <div className="px-6 py-12 text-center text-gray-500">
                        No workers match the current filter.
                    </div>
                )}
            </div>
        </div>
    );
}"""
