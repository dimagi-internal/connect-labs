from connect_labs.messaging.canopy_bridge import cards


def test_a_reply_without_a_card_is_its_text():
    assert cards.split("  Hello.  ") == cards.Reply("Hello.")


def test_the_card_block_comes_out_and_the_prose_around_it_is_the_caption():
    reply = cards.split("Before.\n\n```card-html\n<p>hi</p>\n```\n\nAfter.")
    assert reply == cards.Reply("Before.\n\nAfter.", "<p>hi</p>")


def test_only_the_first_card_is_kept_and_a_plain_code_block_is_left_alone():
    text = "A\n```card-html\n<p>1</p>\n```\n```card-html\n<p>2</p>\n```\n```python\nx = 1\n```"
    reply = cards.split(text)
    assert reply.card_html == "<p>1</p>"
    assert "<p>2</p>" not in reply.text and "```python\nx = 1\n```" in reply.text


def test_a_card_alone_has_no_caption():
    assert cards.split("~~~card-html\n<p>x</p>\n~~~") == cards.Reply("", "<p>x</p>")
