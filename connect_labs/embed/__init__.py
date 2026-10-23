"""Other systems framing labs: Connect prod first, anyone registered later.

Design: docs/superpowers/specs/2026-10-23-embedding-labs-design.md.

An embed client (``models.EmbedClient``) is registered by staff with its public keys, the
origins that may frame labs, and the resources it may show. Its backend redeems a signed
RFC 7523 assertion naming the viewer and the resource at ``/embed/token/`` (``grant.py``)
for a single-use bootstrap token; its page frames ``/embed/c/<client>/<kind>/<id>/``,
which trades the token for a partitioned cookie (``sessions.py``). From then on
``middleware.EmbedSessionMiddleware`` lets that cookie act as the viewer, with their own
Connect token, on the routes the resource's kind names, and nowhere else.

What can be embedded is a registry of kinds (``kinds.py``). A workflow (and so a page) is
the first; nothing outside ``kinds.py`` knows what a workflow is.
"""
