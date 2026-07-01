# User credentials issuance — depends on connect_id_client and opportunity.tasks
# which were removed during labs simplification. This module is a no-op stub.


class UserCredentialIssuer:
    """Stub: credential issuance requires connect_id_client (removed)."""

    @classmethod
    def run(cls):
        raise NotImplementedError("UserCredentialIssuer requires connect_id_client which was removed")

    @classmethod
    def submit_user_credentials(cls):
        raise NotImplementedError("UserCredentialIssuer requires connect_id_client which was removed")
