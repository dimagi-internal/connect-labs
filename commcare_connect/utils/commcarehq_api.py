import httpx
from asgiref.sync import async_to_sync

from commcare_connect.opportunity.models import HQApiKey


class CommCareHQAPIException(Exception):
    pass


class CommCareTokenException(CommCareHQAPIException):
    pass


def refresh_access_token(user, force=False):
    # allauth SocialApp/SocialAccount/SocialToken were removed during labs simplification.
    # Labs uses its own OAuth flow (/labs/login/) and does not use this function.
    raise CommCareTokenException(
        "refresh_access_token is not available in the labs environment. "
        "allauth social account models were removed during simplification."
    )


def get_domains_for_user(api_key):
    response = httpx.get(
        f"{api_key.hq_server.url}/api/v0.5/user_domains/?limit=100",
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise CommCareHQAPIException(f"Failed to fetch domains: {response.text}")
    data = response.json()
    domains = [domain["domain_name"] for domain in data["objects"]]
    return domains


def get_applications_for_user_by_domain(api_key: HQApiKey, domain):
    user_email = api_key.user.email
    hq_server_url = api_key.hq_server.url
    api_key = api_key.api_key
    return _get_applications_for_domain(user_email, api_key, domain, hq_server_url)


@async_to_sync
async def _get_applications_for_domain(user_email, api_key, domain, hq_server_url):
    async with httpx.AsyncClient(
        timeout=300,
        headers={
            "Authorization": f"ApiKey {user_email}:{api_key}",
        },
        base_url=hq_server_url,
    ) as client:
        applications = await _get_commcare_app_json(client, domain)
    return applications


async def _get_commcare_app_json(client, domain):
    applications = []
    response = await client.get(f"/a/{domain}/api/v0.5/application/")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise CommCareHQAPIException(f"Failed to fetch applications: {response.text}")
    data = response.json()

    for application in data.get("objects", []):
        app_name = application.get("name")
        if not application.get("is_released"):
            app_name = f"Unreleased - {app_name}"
        applications.append({"id": application.get("id"), "name": app_name})
    return applications
