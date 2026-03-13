"""
Labs Context Management

Provides unified context selection (organization, program, opportunity) that works
across all labs projects. Context is represented as URL parameters and backed by session.
"""
import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django.http import HttpRequest, HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


CONTEXT_PARAMS = ["organization_id", "program_id", "opportunity_id"]


def get_org_data(request) -> dict:
    """Get organization data from session.

    Returns the organizations/programs/opportunities dict stored during OAuth login.
    """
    labs_oauth = getattr(request, "session", {}).get("labs_oauth", {}) if hasattr(request, "session") else {}
    return labs_oauth.get("organization_data", {})


def extract_context_from_url(request: HttpRequest) -> dict:
    """Extract context parameters from URL query string.

    Args:
        request: HttpRequest object

    Returns:
        Dict with context IDs (e.g., {'opportunity_id': 123, 'program_id': 456})
    """
    context = {}
    for param in CONTEXT_PARAMS:
        value = request.GET.get(param)
        if value:
            # Convert to int if it's numeric (program_id, opportunity_id)
            # Keep as string if it's a slug (organization_id)
            if param in ["program_id", "opportunity_id"]:
                try:
                    context[param] = int(value)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid {param} in URL: {value}")
            else:
                context[param] = value
    return context


def extract_context_from_session(request: HttpRequest) -> dict:
    """Extract context from session storage.

    Args:
        request: HttpRequest object

    Returns:
        Dict with context IDs from session
    """
    return request.session.get("labs_context", {})


def save_context_to_session(request: HttpRequest, context: dict) -> None:
    """Save context to session.

    Args:
        request: HttpRequest object
        context: Dict with context IDs
    """
    request.session["labs_context"] = context


def clear_context_from_session(request: HttpRequest) -> None:
    """Clear context from session.

    Args:
        request: HttpRequest object
    """
    request.session.pop("labs_context", None)


def validate_context_access(request: HttpRequest, context: dict) -> dict:
    """Validate that user has access to the specified context.

    Checks user's OAuth data to ensure they have access to the specified
    organization, program, or opportunity.

    Args:
        request: HttpRequest object with authenticated user
        context: Dict with context IDs to validate

    Returns:
        Dict with validated context and full objects from OAuth data
    """
    org_data = get_org_data(request)
    if not org_data:
        return {}

    validated = {}

    # Validate organization_id
    # OAuth API now returns integer IDs alongside slugs
    # URLs use slugs (human-readable), but APIs need integer IDs
    if "organization_id" in context:
        org_slug = context["organization_id"]
        organizations = org_data.get("organizations", [])
        for org in organizations:
            if org.get("slug") == org_slug:
                # Store organization object for display
                validated["organization"] = org
                # Store slug for URLs
                validated["organization_slug"] = org_slug
                # Store integer ID for API calls
                validated["organization_id"] = org.get("id")
                break

    # Validate program_id
    if "program_id" in context:
        program_id = context["program_id"]
        programs = org_data.get("programs", [])
        for program in programs:
            if program.get("id") == program_id:
                validated["program_id"] = program_id
                validated["program"] = program
                break

    # Validate opportunity_id
    if "opportunity_id" in context:
        opp_id = context["opportunity_id"]
        opportunities = org_data.get("opportunities", [])
        opp_found = False
        for opp in opportunities:
            if opp.get("id") == opp_id:
                validated["opportunity_id"] = opp_id
                validated["opportunity"] = opp
                opp_found = True
                break

        # If opportunity not found in cached data, still pass through the ID
        # Let the view/API handle authorization (handles managed opps bug)
        if not opp_found:
            logger.info(f"Opportunity {opp_id} not in cached OAuth data, passing through for API validation")
            validated["opportunity_id"] = opp_id

    return validated


def add_context_to_url(url: str, context: dict) -> str:
    """Add context parameters to a URL.

    Args:
        url: URL to modify
        context: Dict with context parameters to add

    Returns:
        Modified URL with context parameters
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    # Add context params (only the ID fields, not the full objects)
    for param in CONTEXT_PARAMS:
        # For organization_id, prefer slug for human-readable URLs
        if param == "organization_id" and "organization_slug" in context:
            query_params[param] = [str(context["organization_slug"])]
        elif param in context:
            query_params[param] = [str(context[param])]

    new_query = urlencode(query_params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def get_context_url_params(context: dict) -> str:
    """Get context as URL query string.

    Args:
        context: Dict with context IDs

    Returns:
        Query string (e.g., "opportunity_id=123&program_id=456")
    """
    params = {}
    for param in CONTEXT_PARAMS:
        # For organization_id, prefer slug for human-readable URLs
        if param == "organization_id" and "organization_slug" in context:
            params[param] = context["organization_slug"]
        elif param in context:
            params[param] = context[param]
    return urlencode(params)


def try_auto_select_context(request: HttpRequest) -> dict | None:
    """Try to auto-select context if user has exactly one option.

    Auto-selects if:
    - User has exactly 1 opportunity -> select that opportunity
    - User has exactly 1 program (and no opportunities) -> select that program
    - User has exactly 1 organization (and no programs/opportunities) -> select that org

    Args:
        request: HttpRequest object with authenticated user

    Returns:
        Dict with auto-selected context, or None if can't auto-select
    """
    org_data = get_org_data(request)
    if not org_data:
        return None

    organizations = org_data.get("organizations", [])
    programs = org_data.get("programs", [])
    opportunities = org_data.get("opportunities", [])

    # Priority 1: Auto-select if exactly 1 opportunity
    if len(opportunities) == 1:
        opp = opportunities[0]
        return {"opportunity_id": opp.get("id")}

    # Priority 2: Auto-select if exactly 1 program (and no opportunities)
    if len(programs) == 1 and len(opportunities) == 0:
        program = programs[0]
        return {"program_id": program.get("id")}

    # Priority 3: Auto-select if exactly 1 organization (and no programs/opportunities)
    if len(organizations) == 1 and len(programs) == 0 and len(opportunities) == 0:
        org = organizations[0]
        org_id = org.get("slug") or org.get("id")
        if org_id:
            return {"organization_id": org_id}

    # Can't auto-select - user has multiple options or no options
    return None


def labs_org_data_context(request):
    """Template context processor: expose org data lists from session."""
    org_data = get_org_data(request)
    return {
        "user_organizations": org_data.get("organizations", []),
        "user_programs": org_data.get("programs", []),
        "user_opportunities": org_data.get("opportunities", []),
    }


class LabsContextMiddleware(MiddlewareMixin):
    """Middleware to handle labs context selection.

    Extracts context from URL parameters, validates access, and manages session storage.
    If session has context but URL doesn't, redirects to add params to URL.
    """

    def process_request(self, request: HttpRequest):
        """Process request to extract and validate context."""
        # Only run in labs environment for authenticated users
        from django.conf import settings

        is_labs = getattr(settings, "IS_LABS_ENVIRONMENT", False)

        if not is_labs or not request.user.is_authenticated:
            request.labs_context = {}
            return None

        # Extract context from URL params
        url_context = extract_context_from_url(request)

        # Check session for context
        session_context = extract_context_from_session(request)

        # If session has context but URL doesn't, redirect to add params
        # Only redirect GET requests - POST/PUT/DELETE requests can't preserve their body through redirects
        if session_context and not url_context and request.method == "GET":
            # Check if this is a labs whitelisted path (not login/logout)
            path = request.path
            whitelisted_prefixes = [
                "/audit/",
                "/tasks/",
                "/solicitations/",
                "/solicitations_new/",
                "/ai/",
                "/labs/explorer/",
                "/labs/workflow/",
                "/labs/pipelines/",
                "/labs/scout/",
                "/coverage/",
                "/custom_analysis/",
            ]
            is_whitelisted = any(path.startswith(prefix) for prefix in whitelisted_prefixes)

            if is_whitelisted:
                logger.debug(f"Redirecting {path} to add context params from session")
                redirect_url = add_context_to_url(request.get_full_path(), session_context)
                return HttpResponseRedirect(redirect_url)

        # URL params take precedence over session
        context = url_context if url_context else session_context

        # Validate user has access to this context
        if context:
            validated_context = validate_context_access(request, context)

            if not validated_context:
                # User doesn't have access, clear context
                logger.warning(f"User {request.user.username} doesn't have access to context: {context}")
                clear_context_from_session(request)
                request.labs_context = {}

                # If we got here from URL params that are invalid, redirect without them
                if url_context:
                    # Remove context params from URL
                    parsed = urlparse(request.get_full_path())
                    query_params = parse_qs(parsed.query, keep_blank_values=True)
                    for param in CONTEXT_PARAMS:
                        query_params.pop(param, None)
                    new_query = urlencode(query_params, doseq=True)
                    clean_url = urlunparse(
                        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
                    )
                    return HttpResponseRedirect(clean_url)
            else:
                request.labs_context = validated_context

                # Update session with current context from URL
                if url_context:
                    save_context_to_session(request, url_context)
        else:
            # No context set - check for auto-selection
            auto_selected_context = try_auto_select_context(request)
            if auto_selected_context:
                # Redirect to add auto-selected context to URL
                redirect_url = add_context_to_url(request.get_full_path(), auto_selected_context)
                save_context_to_session(request, auto_selected_context)
                logger.info(f"Auto-selected context for user {request.user.username}: {auto_selected_context}")
                return HttpResponseRedirect(redirect_url)

            request.labs_context = {}

        return None
