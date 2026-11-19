"""The Connect Investigations Tracker: one PUBLIC page of every program Connect delivers or is investigating.

``/labs/investigations/`` needs no sign-in, so what it can show is deliberately narrow:

* ``content``: the hand-edited words (program descriptions, stage, next steps, GiveWell references). Dimagi staff
  change them on a signed-in page, and nothing reaches the public page until someone publishes it.
* ``figures``: the base case and scenario range of the three Connect models, from the explorer's own engine, so the
  tracker and ``/labs/cea/models/`` cannot disagree.
* ``store``: the saved versions, which one is public, and the approved-visit counts a timed job keeps (the public page
  never queries visits itself).
* ``page``: the three laid together as the one payload the page draws.

``content``, ``figures`` and ``page`` are pure (no Django, no database).
"""
