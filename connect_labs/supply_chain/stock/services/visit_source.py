"""Where the stock reader gets visits: the analysis pipeline's raw-visit read.

Through `AnalysisPipeline.fetch_raw_visits`, which fills the shared visit
cache through `get_export_client` -- never a hand-built export client -- so a
synthetic opportunity's fixtures are read exactly as a real one's would be
(design §4.1). A synthetic client ignores the token; a real opportunity needs
one with the export scope, and the reader refuses real programmes for now.

Status changes reach the reader when the cached copy expires (or on
--refresh): the cache is shared with every pipeline on the opportunity, and a
forced walk per hourly run would re-download the whole opportunity each time.
"""

SYNTHETIC_TOKEN = "labs-synthetic-no-connect-token"


def fetch_visits(opportunity_id: int, access_token: str | None, *, force_refresh: bool = False) -> list[dict]:
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(access_token=access_token or SYNTHETIC_TOKEN)
    return pipeline.fetch_raw_visits(opportunity_id=opportunity_id, force_refresh=force_refresh)
