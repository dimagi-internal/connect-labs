"""A live, working example of a system that embeds labs -- written to be read and copied.

This app plays the part of ANOTHER system (Connect, or anyone else) that shows a labs
report in its own page. It is deliberately written as an outside system would write it:
it imports nothing from ``connect_labs.embed``, holds its own private key, publishes its
own public keys, and reaches labs only over HTTP. Read the files in this order:

* ``embedder.py`` -- the whole protocol, server side: keys, the JWKS, signing the
  statement, and exchanging it for a token. About a hundred lines; nothing labs-specific.
* ``views.py`` -- the example system's three endpoints (its page, its token endpoint for
  the page's script, its JWKS).
* ``templates/embed_example/page.html`` -- the browser side: the iframe, both ways of
  handing it a token, and listening to the frame's messages.

It is safe to leave running: it only ever names the person already signed in (it never
takes a username from a request), and its registration is limited by labs to generated
data (``EmbedClient.generated_data_only``). Registered by ``manage.py
embed_example_register``. The integration guide: user_docs/developers/embedding.md.
"""
