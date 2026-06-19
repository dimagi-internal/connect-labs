from commcare_connect.campaign.models import Activity


def create_activity(campaign, data, synced: bool) -> Activity:
    n = campaign.activities.count() + 1
    return Activity.objects.create(
        campaign=campaign,
        activity_id=f"ACT-{n:02d}",
        name=(data.get("name") or "").strip(),
        donor=data.get("donor") or "",
        region=data.get("region") or "",
        start=data.get("start") or "",
        end=data.get("end") or "",
        target=int(data.get("target") or 0),
        status="Planned",
        requests=0,
        workers=0,
        reached=0,
        synced=bool(synced),
    )


def sync_activity(activity) -> Activity:
    activity.synced = True
    activity.save(update_fields=["synced"])
    return activity
