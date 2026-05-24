"""Template coaching conversations for synthetic OCS transcripts."""

from __future__ import annotations

import datetime as dt

TEMPLATES: dict[str, list[dict]] = {
    "high_flag_rate": [
        {
            "role": "bot",
            "text": "Hi {flw_name}, I noticed some of your recent visits were flagged for review. I wanted to check in — are you having any difficulty with the MUAC measurement process?",
        },
        {
            "role": "flw",
            "text": "Yes, sometimes the children are moving a lot and it's hard to get an accurate reading.",
        },
        {
            "role": "bot",
            "text": "That's very common! A few tips: try measuring when the child is calm, perhaps while the caregiver holds them. Also make sure the tape is snug but not tight on the left arm, midway between the shoulder and elbow.",
        },
        {"role": "flw", "text": "OK, I will try that. Should I re-measure if the child moves?"},
        {
            "role": "bot",
            "text": "Yes, always re-measure if you're unsure. It's better to take an extra minute than to record an inaccurate reading. Your supervisor can also observe a few measurements to give you feedback.",
        },
        {"role": "flw", "text": "Thank you, that is helpful."},
    ],
    "missing_visits": [
        {
            "role": "bot",
            "text": "Hi {flw_name}, I see you had fewer visits than expected last week. Is everything alright? Were there any barriers to completing your rounds?",
        },
        {"role": "flw", "text": "There was heavy rain for two days and I could not reach some households."},
        {
            "role": "bot",
            "text": "I understand — weather can be a real challenge. When that happens, try to prioritize households with children who had concerning MUAC readings previously. They need follow-up most urgently.",
        },
        {"role": "flw", "text": "Good idea. I will plan my route to visit the high-priority ones first."},
        {
            "role": "bot",
            "text": "Great plan. Also, please log the missed visits in the app so your supervisor knows the reason — it helps with scheduling catch-up visits.",
        },
    ],
    "low_approval_rate": [
        {
            "role": "bot",
            "text": "Hi {flw_name}, I wanted to follow up on your recent submission quality. Some of your visits had incomplete data. Can we talk about what fields might be getting missed?",
        },
        {"role": "flw", "text": "Sometimes I forget to fill in the consent question before doing the MUAC reading."},
        {
            "role": "bot",
            "text": "That's an important one — the consent field is required before the MUAC measurement section unlocks. Try going through the form sections in order without skipping ahead. The app is designed to guide you through the right sequence.",
        },
        {"role": "flw", "text": "I will be more careful to follow the form order."},
        {
            "role": "bot",
            "text": "Perfect. If you have any trouble with the form flow, reach out to your supervisor — they can walk through a practice submission with you.",
        },
    ],
    "new_hire_checkin": [
        {
            "role": "bot",
            "text": "Welcome {flw_name}! How is your first week going? I wanted to check if you have any questions about the MUAC screening process.",
        },
        {"role": "flw", "text": "It's going well but I'm not sure what to do when a child has a red MUAC reading."},
        {
            "role": "bot",
            "text": "Great question! A red reading (below 11.5 cm) indicates Severe Acute Malnutrition (SAM). You should immediately refer the child to the nearest health facility and record the referral in the app.",
        },
        {"role": "flw", "text": "And what about yellow readings?"},
        {
            "role": "bot",
            "text": "Yellow (11.5-12.5 cm) is Moderate Acute Malnutrition (MAM). Also refer to a health facility, but it's less urgent. Make sure to follow up on the next visit to see if the child received care. You're doing great — keep asking questions!",
        },
        {"role": "flw", "text": "Thank you, I feel more confident now."},
    ],
    "positive_reinforcement": [
        {
            "role": "bot",
            "text": "Hi {flw_name}, I wanted to share some great news — your approval rate this month is one of the highest in the program! Your data quality has been excellent.",
        },
        {"role": "flw", "text": "Thank you! I have been trying to be very careful with the measurements."},
        {
            "role": "bot",
            "text": "It shows! Your MUAC measurements are consistent and your referral documentation is thorough. The program coordinator specifically mentioned your work as an example for other health workers.",
        },
        {"role": "flw", "text": "That makes me happy. I want to help the children in my community."},
        {
            "role": "bot",
            "text": "Your dedication is making a real difference. Keep up the excellent work, and don't hesitate to reach out if you ever need support.",
        },
    ],
}

_DEFAULT_KEY = "positive_reinforcement"


def render_transcript(
    *,
    template_key: str,
    flw_name: str,
    base_timestamp: dt.datetime,
) -> list[dict[str, str]]:
    """Fill a template with FLW name and absolute timestamps."""
    template = TEMPLATES.get(template_key, TEMPLATES[_DEFAULT_KEY])

    result = []
    for i, msg in enumerate(template):
        ts = base_timestamp + dt.timedelta(minutes=i * 2)
        result.append(
            {
                "role": msg["role"],
                "text": msg["text"].format(flw_name=flw_name),
                "ts": ts.isoformat(),
            }
        )
    return result
