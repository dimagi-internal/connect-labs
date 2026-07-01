"""
Pipeline Agent for editing standalone pipeline schemas and visualizations via AI.

This agent uses tools instead of structured output to enable real-time streaming
of the AI's explanation while capturing structured updates via tool calls.

Architecture:
- Agent outputs plain text (streams naturally token-by-token)
- update_schema() tool captures pipeline schema changes
- update_render_code() tool captures UI code changes
- Tools are called during streaming, results available after completion

Pipelines define how to extract and transform data from form submissions.
They can be used standalone or referenced by Workflows as data sources.
"""

import json
import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from connect_labs.ai.types import UserDependencies

logger = logging.getLogger(__name__)

PIPELINE_AGENT_INSTRUCTIONS = """
You are an expert data analyst helping users build and modify data extraction pipelines.

## What You Can Edit

1. **Schema** - JSON config defining what data to extract from form submissions
2. **Render Code** - React/JSX code that visualizes the extracted data (optional)

## Pipeline Schema Structure

```json
{
    "name": "Pipeline Name",
    "description": "What this pipeline analyzes",
    "version": 1,
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "linking_field": "entity_id",
    "fields": [
        {
            "name": "field_name",
            "path": "form.path.to.field",
            "paths": ["form.path1", "form.path2"],
            "aggregation": "first",
            "transform": "float",
            "description": "Human readable description"
        }
    ],
    "histograms": [],
    "filters": {}
}
```

### Schema Fields Explained

- **grouping_key**: Field to group by ("username", "entity_id", "deliver_unit_id")
- **terminal_stage**:
  - "visit_level" - One row per form submission (visit)
  - "aggregated" - One row per FLW/user with aggregated stats
- **linking_field**: Field to link related visits (e.g., "entity_id" for beneficiary tracking)

### Field Configuration

Each field extracts data from form_json using JSONB paths:

```json
{
    "name": "weight",
    "path": "form.anthropometric.child_weight",
    "paths": ["form.path1", "form.path2"],
    "aggregation": "first",
    "transform": "kg_to_g",
    "description": "Child weight in grams",
    "default": null
}
```

**Aggregation Types** (for aggregated terminal_stage):
- "first" - First non-null value (by visit date)
- "last" - Last non-null value
- "sum" - Sum of values
- "avg" - Average of values
- "count" - Count of non-null values
- "min" / "max" - Minimum / maximum
- "list" - Array of all values
- "count_unique" - Count distinct values

**Transform Types** (applied to extracted values):
- "float" - Convert to decimal number
- "int" - Convert to integer
- "kg_to_g" - Multiply by 1000 (kilograms to grams)
- "date" - Parse as date string
- "string" - Convert to string
- null - No transformation

### Histogram Configuration

Histograms create binned distributions for numeric fields:

```json
{
    "name": "weight_distribution",
    "path": "form.anthropometric.child_weight",
    "lower_bound": 1000,
    "upper_bound": 5000,
    "num_bins": 8,
    "bin_name_prefix": "weight",
    "transform": "kg_to_g",
    "description": "Weight distribution in grams"
}
```

### Common Form JSON Paths

Form data is nested under `form_json`:
- `form.case.@case_id` - Case ID
- `form.case.update.field_name` - Case update fields
- `form.meta.timeEnd` - Form submission time
- `form.meta.username` - Submitting user
- `form.question_group.question_id` - Form questions

## Render Code (Optional)

Pipelines can have their own visualization. The component receives:

```jsx
function PipelineUI({ data, definition, onRefresh }) {
    // data: { rows: [...], metadata: {...}, from_cache: boolean }
    // definition: { name, description, schema, ... }
    // onRefresh: () => void - Trigger data reload
}
```

### Data Structure (Visit Level)

```javascript
data.rows = [
    {
        username: "flw123",
        visit_date: "2024-01-15",
        status: "approved",
        entity_id: "case123",
        computed: { weight: 2500, height: 45.5 }
    }
]
```

### Data Structure (Aggregated)

```javascript
data.rows = [
    {
        username: "flw123",
        total_visits: 45,
        approved_visits: 40,
        custom_fields: { avg_weight: 2750 }
    }
]
```

## Available CSS Classes (Tailwind)

- Layout: flex, grid, space-y-4, gap-4, p-4, m-2
- Colors: bg-white, bg-gray-50, text-gray-900, text-blue-600
- Borders: border, rounded-lg, shadow-sm
- Typography: text-sm, text-xl, font-bold, font-medium

## Tools Available

- `update_schema(schema)` - Update the pipeline schema
- `update_render_code(code)` - Update the visualization UI

## When to Use Which Tool

- User asks about data extraction/fields/aggregation -> call update_schema
- User asks about layout/display/charts -> call update_render_code
- User asks about both -> call both tools

## CRITICAL RULES

1. ALWAYS explain what you're going to change before calling tools
2. When calling update_render_code, provide the COMPLETE code, not snippets
3. When calling update_schema, provide the COMPLETE schema object
4. You may call zero, one, or both tools depending on the request
5. After calling tools, summarize what was changed

## IMPORTANT: You MUST Use Tools!

When the user asks you to add, modify, or remove fields/settings:
- You MUST call the appropriate tool (update_schema or update_render_code)
- Just describing the change is NOT enough - the tool call makes it happen
- If you don't call the tool, the change will NOT be saved
- Think of tools as "save buttons" - your explanation is for the user, the tool call is what persists the change

Example workflow:
1. User: "Add a phone_number field"
2. You: "I'll add a phone_number field to extract phone numbers from form submissions."
3. You: [CALL update_schema with the complete schema including the new field]
4. You: "Done! I've added the phone_number field that will extract from form.phone_number."
"""


@dataclass
class PipelineAgentDeps:
    """Dependencies for pipeline agent that capture tool results."""

    user_deps: UserDependencies
    pending_schema: dict | None = None
    pending_render_code: str | None = None
    schema_changed: bool = False
    render_code_changed: bool = False


def create_pipeline_agent_with_model(model: str) -> Agent[PipelineAgentDeps, str]:
    """
    Create the pipeline agent with a specific model.

    Args:
        model: Full model string (e.g., 'anthropic:claude-sonnet-4-6', 'openai:gpt-4o')

    Returns:
        Agent configured with the specified model
    """
    logger.debug(f"[Pipeline Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=PipelineAgentDeps,
        output_type=str,  # Plain text output enables smooth streaming
        instructions=PIPELINE_AGENT_INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=16384),
    )

    @agent.tool
    async def update_schema(ctx: RunContext[PipelineAgentDeps], schema: dict) -> str:
        """
        Update the pipeline schema JSON.

        Call this tool when you need to modify the data extraction configuration,
        including fields, aggregations, transforms, or histograms.

        Args:
            schema: Complete pipeline schema object with name, description,
                   fields, grouping_key, terminal_stage, etc.
        """
        logger.debug("[Pipeline Agent] update_schema called")
        ctx.deps.pending_schema = schema
        ctx.deps.schema_changed = True
        return "Pipeline schema will be updated."

    @agent.tool
    async def update_render_code(ctx: RunContext[PipelineAgentDeps], code: str) -> str:
        """
        Update the React render code for the pipeline visualization.

        Call this tool when you need to modify how the data is displayed.
        Always provide the COMPLETE component code, not just the changed parts.

        Args:
            code: Complete React functional component code as a string.
        """
        logger.debug("[Pipeline Agent] update_render_code called")
        ctx.deps.pending_render_code = code
        ctx.deps.render_code_changed = True
        return "Render code will be updated."

    return agent


# Convenience functions for specific models
def create_pipeline_agent() -> Agent[PipelineAgentDeps, str]:
    """Create the pipeline agent with default Claude Sonnet 4 model."""
    return create_pipeline_agent_with_model("anthropic:claude-sonnet-4-6")


def create_pipeline_agent_openai() -> Agent[PipelineAgentDeps, str]:
    """Create the pipeline agent with GPT-4o."""
    return create_pipeline_agent_with_model("openai:gpt-4o")


def build_pipeline_prompt(
    user_prompt: str,
    current_schema: dict | None = None,
    current_render_code: str | None = None,
    conversation_history: list | None = None,
) -> str:
    """
    Build the prompt for the pipeline agent including current context.

    Args:
        user_prompt: The user's request
        current_schema: The current pipeline schema JSON
        current_render_code: The current React render code
        conversation_history: List of prior messages [{role, content}, ...]

    Returns:
        The full prompt to send to the agent
    """
    prompt_parts = []

    if current_schema:
        prompt_parts.append("## Current Pipeline Schema")
        prompt_parts.append(f"```json\n{json.dumps(current_schema, indent=2)}\n```")
        prompt_parts.append("")

    if current_render_code:
        prompt_parts.append("## Current Render Code")
        prompt_parts.append(f"```jsx\n{current_render_code}\n```")
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


# Default render code template for new pipelines
DEFAULT_RENDER_CODE = """function PipelineUI({ data, definition, onRefresh }) {
    const rows = data?.rows || [];
    const schema = definition?.schema || {};
    const fields = schema.fields || [];

    const totalRows = rows.length;
    const fromCache = data?.from_cache;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">
                            {definition?.name || 'Pipeline Results'}
                        </h1>
                        <p className="text-gray-600 mt-1">
                            {definition?.description || 'Data analysis results'}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        {fromCache && (
                            <span className="px-3 py-1 bg-green-100 text-green-800 rounded text-sm">
                                Cached
                            </span>
                        )}
                        <button
                            onClick={onRefresh}
                            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                        >
                            Refresh
                        </button>
                    </div>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-4">
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="text-sm text-gray-500">Total Rows</div>
                    <div className="text-2xl font-bold text-gray-900">{totalRows}</div>
                </div>
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="text-sm text-gray-500">Fields</div>
                    <div className="text-2xl font-bold text-gray-900">{fields.length}</div>
                </div>
                <div className="bg-white rounded-lg shadow-sm p-4">
                    <div className="text-sm text-gray-500">Stage</div>
                    <div className="text-2xl font-bold text-gray-900">
                        {schema.terminal_stage === 'aggregated' ? 'Aggregated' : 'Visit Level'}
                    </div>
                </div>
            </div>

            {/* Data Table */}
            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                    User
                                </th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                    Date
                                </th>
                                {fields.map(field => (
                                    <th
                                        key={field.name}
                                        className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase"
                                        title={field.description}
                                    >
                                        {field.name}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {rows.slice(0, 100).map((row, idx) => (
                                <tr key={idx} className="hover:bg-gray-50">
                                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                                        {row.username}
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                        {row.visit_date || row.last_visit_date || '-'}
                                    </td>
                                    {fields.map(field => (
                                        <td key={field.name} className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                            {formatValue(row.computed?.[field.name] ?? row.custom_fields?.[field.name])}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {rows.length > 100 && (
                    <div className="px-6 py-3 bg-gray-50 text-sm text-gray-500">
                        Showing first 100 of {rows.length} rows
                    </div>
                )}
            </div>
        </div>
    );
}

function formatValue(val) {
    if (val === null || val === undefined) return '-';
    if (typeof val === 'number') return val.toLocaleString();
    if (Array.isArray(val)) return val.length + ' items';
    return String(val);
}"""
