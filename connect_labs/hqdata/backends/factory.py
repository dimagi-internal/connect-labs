from connect_labs.hqdata.backends.base import BackendUnavailable, SourceBackend
from connect_labs.hqdata.models import HQProject


def backend_for(project: HQProject) -> SourceBackend:
    if project.backend == HQProject.BACKEND_HQ_API:
        from connect_labs.hqdata.backends.hq_api import HQApiBackend

        return HQApiBackend.for_project(project)
    raise BackendUnavailable(f"No backend is available for {project.backend!r}.")
