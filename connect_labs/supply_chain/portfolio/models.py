"""A named set of programmes -- the one table here that spans them.

Imported by `supply_chain.models` so Django registers it with the app, the
same way `alerts/models.py` and `update_links/models.py` are.
"""

from django.db import models

from connect_labs.supply_chain.models import TimestampedModel


class Portfolio(TimestampedModel):
    """A named set of programmes, and the one thing here that is NOT programme-scoped.

    Every other model in this app carries a `program_id` or a `scope_key`.
    This one deliberately does not: it exists to span them. It holds no supply
    data -- only a name and which programmes belong -- so it grants nothing and
    reveals nothing on its own. **Do not "fix" it by adding a programme
    scope.** A portfolio that belonged to one programme could not answer the
    only question it exists for, which is what the whole operation is doing.

    Membership is not access, and that separation is the load-bearing part.
    Adding a programme here gives nobody the right to see it: the view renders
    only the rows the viewer could already reach through
    `labs.context.get_org_data`, and says so when it leaves one out. A
    programme therefore belongs to as many portfolios as anybody finds useful,
    and putting one in costs no permission decision.

    There is no operation for writing one, and that is deliberate rather than
    an omission. Every supply write goes through the operation registry
    because the registry is what validates a payload and stamps who recorded
    it -- and both of those exist to protect *supply data*. A name and a list
    of ids is neither, and an operation over it would need a programme scope
    it does not have. The demo's portfolio is seeded by
    `scripts/walkthroughs/oes-demo/seed_remote.py` straight through the ORM.
    """

    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=300)
    # Connect programme ids, in the order the portfolio states them. The ORDER
    # IS THE CONTENT: the master view renders its rows in it, because ranking
    # them would be a judgement the database cannot make (see PortfolioView).
    program_ids = models.JSONField(default=list)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name
