"""
Views for AI streaming endpoints.

Provides SSE streaming of AI responses for workflow and pipeline editing.
"""

import asyncio
import json
import logging
import queue
import threading

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from commcare_connect.ai.types import UserDependencies

logger = logging.getLogger(__name__)


def send_sse_event(
    message: str = "",
    event_type: str = "message",
    data: dict | None = None,
    error: str | None = None,
) -> str:
    """
    Format a message as a Server-Sent Event.

    Args:
        message: Status message or text content
        event_type: Type of event (delta, complete, error, etc.)
        data: Optional data payload
        error: Optional error message

    Returns:
        Formatted SSE event string
    """
    event = {"event_type": event_type, "message": message}
    if data:
        event["data"] = data
        event["complete"] = True
    if error:
        event["error"] = error
        event["complete"] = True
    return f"data: {json.dumps(event)}\n\n"


@method_decorator(csrf_exempt, name="dispatch")
class AIStreamView(LoginRequiredMixin, View):
    """
    SSE streaming endpoint for AI chat.

    Provides real-time streaming of AI responses using Server-Sent Events.
    Supports both workflow and pipeline agents with tool-based updates.

    Accepts POST requests with JSON body:
        agent: 'workflow' or 'pipeline'
        prompt: User's message
        definition_id: ID of the workflow/pipeline definition
        opportunity_id: ID of the opportunity
        current_definition: Current definition/schema object
        current_render_code: Current render code string
        model: Full model string (e.g., 'anthropic:claude-sonnet-4-20250514', 'openai:gpt-4o')
    """

    # Allowed models for security - using latest 2025/2026 models
    ALLOWED_MODELS = {
        # Claude 4.5 models (late 2025)
        "anthropic:claude-sonnet-4-5-20250929",
        "anthropic:claude-opus-4-5-20251101",
        # GPT-5.2 models (December 2025)
        "openai:gpt-5.2",
        "openai:gpt-5.2-2025-12-11",
        # Legacy models for backwards compatibility
        "anthropic:claude-sonnet-4-20250514",
        "anthropic:claude-opus-4-20250514",
        "openai:gpt-4o",
        "openai:gpt-4o-mini",
    }
    DEFAULT_MODEL = "anthropic:claude-sonnet-4-5-20250929"

    def post(self, request):
        """Handle POST request and return streaming response."""
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        # Parse JSON body
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON body"}, status=400)

        # Extract parameters from body
        agent_type = body.get("agent", "").strip()
        prompt = body.get("prompt", "").strip()
        definition_id = str(body.get("definition_id", "")).strip()
        opportunity_id = str(body.get("opportunity_id", "")).strip()
        current_definition = body.get("current_definition")
        current_render_code = body.get("current_render_code", "").strip() if body.get("current_render_code") else ""
        model = body.get("model", self.DEFAULT_MODEL).strip()
        active_context = body.get("active_context")  # {active_tab, pipeline_id, pipeline_alias, pipeline_schema}
        conversation_history = body.get("messages", [])  # List of {role, content} dicts

        # Log request (without huge payloads)
        logger.info(
            f"[AI Stream] POST request: agent={agent_type}, model={model}, "
            f"definition_id={definition_id}, prompt_length={len(prompt)}, "
            f"render_code_length={len(current_render_code)}"
        )

        # Validate model
        if model not in self.ALLOWED_MODELS:
            logger.warning(f"[AI Stream] Invalid model {model}, using default")
            model = self.DEFAULT_MODEL

        # Validate required parameters
        if not agent_type:
            return JsonResponse({"error": "agent is required"}, status=400)
        if agent_type not in ("workflow", "pipeline", "solicitations"):
            return JsonResponse({"error": "agent must be 'workflow', 'pipeline', or 'solicitations'"}, status=400)
        if not prompt:
            return JsonResponse({"error": "prompt is required"}, status=400)

        # Get OAuth token from session
        access_token = None
        labs_oauth = request.session.get("labs_oauth", {})
        if labs_oauth:
            expires_at = labs_oauth.get("expires_at", 0)
            if timezone.now().timestamp() < expires_at:
                access_token = labs_oauth.get("access_token")

        # Get program_id from labs_context
        program_id = None
        if hasattr(request, "labs_context"):
            program_id = request.labs_context.get("program_id")

        # Create sync generator that runs async code properly
        def stream_generator():
            yield from self._run_streaming_agent(
                agent_type=agent_type,
                prompt=prompt,
                current_definition=current_definition,
                current_render_code=current_render_code,
                model=model,
                user=request.user,
                access_token=access_token,
                program_id=program_id,
                definition_id=definition_id,
                opportunity_id=opportunity_id,
                active_context=active_context,
                conversation_history=conversation_history,
            )

        response = StreamingHttpResponse(
            stream_generator(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _run_streaming_agent(
        self,
        agent_type: str,
        prompt: str,
        current_definition: dict | None,
        current_render_code: str | None,
        model: str,
        user,
        access_token: str | None,
        program_id: int | None,
        definition_id: str | None,
        opportunity_id: str | None,
        active_context: dict | None = None,
        conversation_history: list | None = None,
    ):
        """
        Run the streaming agent, yielding SSE events in real-time.

        Uses a Queue to bridge async streaming to sync generator.
        """
        logger.debug(f"[AI Stream] Starting stream for agent={agent_type}, model={model}")

        event_queue = queue.Queue()
        DONE_SENTINEL = object()

        user_deps = UserDependencies(user=user, request=None, program_id=program_id)

        def run_async_in_thread():
            """Run the async agent in a separate thread with its own event loop."""

            async def run_agent():
                if agent_type == "workflow":
                    from commcare_connect.ai.agents.workflow_agent import (
                        WorkflowAgentDeps,
                        build_workflow_prompt,
                        create_workflow_agent_with_model,
                    )

                    agent = create_workflow_agent_with_model(model)
                    deps = WorkflowAgentDeps(user_deps=user_deps)
                    full_prompt = build_workflow_prompt(
                        prompt,
                        current_definition,
                        current_render_code,
                        active_context=active_context,
                        conversation_history=conversation_history,
                    )

                elif agent_type == "solicitations":
                    from commcare_connect.ai.agents.solicitation_agent import (
                        SolicitationAgentDeps,
                        create_solicitation_agent_with_model,
                    )

                    agent = create_solicitation_agent_with_model(model)
                    deps = SolicitationAgentDeps(user_deps=user_deps)
                    full_prompt = prompt

                else:  # pipeline
                    from commcare_connect.ai.agents.pipeline_agent import (
                        PipelineAgentDeps,
                        build_pipeline_prompt,
                        create_pipeline_agent_with_model,
                    )

                    agent = create_pipeline_agent_with_model(model)
                    deps = PipelineAgentDeps(user_deps=user_deps)
                    full_prompt = build_pipeline_prompt(
                        prompt,
                        current_definition,
                        current_render_code,
                        conversation_history=conversation_history,
                    )

                try:
                    # Use agent.iter() for streaming with proper tool execution
                    # This runs the agent graph to completion even if text was received ahead of tool calls
                    # See: https://ai.pydantic.dev/agents/#streaming
                    from pydantic_ai import Agent
                    from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

                    final_text = ""
                    async with agent.iter(full_prompt, deps=deps) as run:
                        async for node in run:
                            if Agent.is_model_request_node(node):
                                # Stream text as it arrives from the model
                                async with node.stream(run.ctx) as request_stream:
                                    async for event in request_stream:
                                        if isinstance(event, PartDeltaEvent):
                                            if isinstance(event.delta, TextPartDelta):
                                                chunk = event.delta.content_delta
                                                final_text += chunk
                                                event_queue.put(send_sse_event(message=chunk, event_type="delta"))

                    # If no text was streamed (e.g., only tool calls), get the final output
                    if not final_text:
                        final_text = str(run.result.output) if run.result else ""
                        if final_text:
                            event_queue.put(send_sse_event(message=final_text, event_type="delta"))

                    # Log tool call results
                    logger.info(
                        f"[AI Stream] Agent finished. Response length: {len(final_text)}, "
                        f"definition_changed: {deps.definition_changed}, "
                        f"render_code_changed: {deps.render_code_changed}"
                    )
                    if deps.render_code_changed and deps.pending_render_code:
                        logger.info(f"[AI Stream] New render code length: {len(deps.pending_render_code)}")

                    # Build completion data based on agent type
                    if agent_type == "workflow":
                        completion_data = {
                            "message": final_text,
                            "definition": deps.pending_definition,
                            "definition_changed": deps.definition_changed,
                            "render_code": deps.pending_render_code,
                            "render_code_changed": deps.render_code_changed,
                            "pipeline_actions": deps.pending_pipeline_actions,
                            "pipeline_schema_updates": deps.pending_pipeline_schema_updates,
                            "pipeline_schema_changed": deps.pipeline_schema_changed,
                        }
                    elif agent_type == "solicitations":
                        completion_data = {
                            "message": final_text,
                        }
                    else:  # pipeline
                        completion_data = {
                            "message": final_text,
                            "schema": deps.pending_schema,
                            "schema_changed": deps.schema_changed,
                            "render_code": deps.pending_render_code,
                            "render_code_changed": deps.render_code_changed,
                        }

                    def_changed = completion_data.get("definition_changed", completion_data.get("schema_changed"))
                    render_changed = completion_data.get("render_code_changed")
                    logger.info(
                        f"[AI Stream] Complete: definition_changed={def_changed}, "
                        f"render_code_changed={render_changed}"
                    )

                    event_queue.put(send_sse_event(message="Complete", event_type="complete", data=completion_data))

                    # Save chat history
                    self._save_chat_history_sync(
                        agent_type=agent_type,
                        definition_id=definition_id,
                        opportunity_id=opportunity_id,
                        access_token=access_token,
                        program_id=program_id,
                        user_prompt=prompt,
                        assistant_response=final_text,
                    )

                except Exception as e:
                    logger.error(f"[AI Stream] Agent error: {e}", exc_info=True)
                    event_queue.put(send_sse_event(error=str(e), event_type="error"))

            asyncio.run(run_agent())
            event_queue.put(DONE_SENTINEL)

        # Start the async agent in a separate thread
        thread = threading.Thread(target=run_async_in_thread, daemon=True)
        thread.start()

        # Yield events from the queue as they arrive
        while True:
            try:
                event = event_queue.get(timeout=120)
                if event is DONE_SENTINEL:
                    break
                yield event
            except queue.Empty:
                logger.warning("[AI Stream] Timeout waiting for event")
                yield send_sse_event(error="Request timed out", event_type="error")
                break

        thread.join(timeout=5)

    def _save_chat_history_sync(
        self,
        agent_type: str,
        definition_id: str | None,
        opportunity_id: str | None,
        access_token: str | None,
        program_id: int | None,
        user_prompt: str,
        assistant_response: str,
    ):
        """Save chat messages to history (synchronous version)."""
        if not definition_id or not access_token:
            return

        try:
            definition_id_int = int(definition_id)
            opportunity_id_int = int(opportunity_id) if opportunity_id else None
        except (ValueError, TypeError):
            return

        try:
            from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess

            if agent_type == "workflow":
                data_access = WorkflowDataAccess(
                    access_token=access_token,
                    program_id=program_id,
                    opportunity_id=opportunity_id_int,
                )
                data_access.add_chat_message(definition_id_int, "user", user_prompt)
                data_access.add_chat_message(definition_id_int, "assistant", assistant_response)
                data_access.close()
            else:  # pipeline
                data_access = PipelineDataAccess(
                    access_token=access_token,
                    program_id=program_id,
                    opportunity_id=opportunity_id_int,
                )
                data_access.add_chat_message(definition_id_int, "user", user_prompt)
                data_access.add_chat_message(definition_id_int, "assistant", assistant_response)
                data_access.close()

            logger.debug(f"[AI Stream] Saved chat history for {agent_type} definition {definition_id_int}")
        except Exception as e:
            logger.error(f"[AI Stream] Failed to save chat history: {e}")
