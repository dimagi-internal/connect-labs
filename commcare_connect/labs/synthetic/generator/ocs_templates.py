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
    # ------------------------------------------------------------------
    # Task-archetype-aligned coaching templates. These pair with the
    # task archetypes in commcare_connect/labs/synthetic/archetypes.py so a
    # generated coaching task carries an OCS conversation that matches its
    # narrative outcome.
    # ------------------------------------------------------------------
    "coaching_resolved_clean": [
        # closed_satisfactory — a calm coaching touch-up after one bad batch.
        {
            "role": "bot",
            "text": "Hi {flw_name}, your supervisor flagged a small data-quality issue from this week's visits. Got a minute to talk it through?",
        },
        {"role": "flw", "text": "Yes, sure. Was it the photo angle issue?"},
        {
            "role": "bot",
            "text": "That's exactly it — a few of your MUAC photos were framed too tight to see the measurement window. The audit photos all passed though, so this is just a small refresher.",
        },
        {
            "role": "flw",
            "text": "I think I know which ones. I was rushing because one of the houses had a sick child waiting.",
        },
        {
            "role": "bot",
            "text": "That's completely understandable. Two quick tips: keep the camera ~30 cm back so the colour bands are visible, and re-take if the reticle isn't fully in frame. Want to look at one of yours together?",
        },
        {"role": "flw", "text": "Yes please."},
        {
            "role": "bot",
            "text": "Here's photo #3 from yesterday — see how the reading window is cut off by the thumb? Just slightly rotating the wrist fixes that. Try a few practice shots next visit and your supervisor will spot-check.",
        },
        {"role": "flw", "text": "Got it, I'll be careful with framing. Thanks for the help."},
        {
            "role": "bot",
            "text": "Anytime. Closing this task as resolved — you're doing great work overall, {flw_name}.",
        },
    ],
    "coaching_formal_warning": [
        # closed_warned — coaching with a recorded warning. FLW remains on roster.
        {
            "role": "bot",
            "text": "Hi {flw_name}. The audit on your recent visits found a pattern we need to address — multiple photos in a row had measurement issues. This needs to be a formal coaching call.",
        },
        {"role": "flw", "text": "Okay. I wasn't sure all of them were a problem."},
        {
            "role": "bot",
            "text": "I'll walk through what we found. Of your last five MUAC photos, three were unreadable due to framing and two looked like the tape was applied loosely. That kind of clustering tells us the issue isn't an off day — it's a habit that needs to change.",
        },
        {
            "role": "flw",
            "text": "I see. I've been doing the visits very quickly because there are so many in my area.",
        },
        {
            "role": "bot",
            "text": "I understand the pressure. But these measurements drive referrals for severe acute malnutrition — if we miss them, real children miss treatment. Speed matters less than accuracy here.",
        },
        {"role": "flw", "text": "You're right. I will slow down."},
        {
            "role": "bot",
            "text": "Good. Three concrete things for this week: (1) Re-do the MUAC training module before your next visit. (2) Your supervisor will shadow your first three visits next week. (3) I'm recording this as a formal warning. If photo quality drops again next week, the next step is suspension from the active roster.",
        },
        {"role": "flw", "text": "Understood. I will not let this happen again."},
        {
            "role": "bot",
            "text": "I believe you. Closing this task as 'warned'. Reach out if you need a refresher on any specific measurement step before your next visit, {flw_name}.",
        },
    ],
    "coaching_repeat_offense_suspension": [
        # closed_suspended — second strike on the same FLW. Removed from roster.
        {
            "role": "bot",
            "text": "Hi {flw_name}. We're following up on the audit again this week. I'm sorry to say the issues from your formal warning two weeks ago have continued.",
        },
        {"role": "flw", "text": "I tried to do better. What did the audit find?"},
        {
            "role": "bot",
            "text": "All five of your MUAC photos this week were flagged. The reviewers' conclusion was that the patterns are consistent with rushed measurements — same framing problems we discussed at the warning, plus two photos that appear to be of the same tape position rather than separate measurements.",
        },
        {"role": "flw", "text": "I see. I have been struggling to keep up."},
        {
            "role": "bot",
            "text": "I appreciate your honesty. Given this is a repeat failure right after a formal warning, the program standard is suspension from the active roster. I want to be clear: this isn't permanent — it's pause to give you time to retrain properly without delivery pressure.",
        },
        {"role": "flw", "text": "What happens next?"},
        {
            "role": "bot",
            "text": "Three things. (1) Effective today you're off the visit roster. (2) Your supervisor will set up a full retraining session for you next week — at a pace that works. (3) After you complete the training and demonstrate competency on practice measurements, you can apply to rejoin.",
        },
        {"role": "flw", "text": "I understand. I want to come back when I am ready."},
        {
            "role": "bot",
            "text": "That's the right attitude. We'd rather have you out for two weeks and confident than rushing and missing children who need help. Closing this task as 'suspended'. Your supervisor will be in touch about the retraining schedule.",
        },
    ],
    "coaching_repeat_offense_fraud_suspension": [
        # closed_suspended for suspected_fraudulent archetype — stronger framing.
        {
            "role": "bot",
            "text": "Hi {flw_name}. We need to discuss the audit findings on your recent visits. The reviewers flagged a serious concern.",
        },
        {"role": "flw", "text": "Okay, what did they find?"},
        {
            "role": "bot",
            "text": "Multiple photos this week appear to show the MUAC tape on objects that don't look like a child's arm — the reviewer noted the tape was visibly on a finger in one image and on a piece of cloth in another. Combined with your warning two weeks ago, this is a pattern we can't continue with.",
        },
        {"role": "flw", "text": "I... had trouble reaching some of the households this week."},
        {
            "role": "bot",
            "text": "I hear you. Reaching every household is hard, but submitting photos that aren't real measurements is a line we can't move on. Real children with SAM get missed when measurements aren't taken.",
        },
        {"role": "flw", "text": "I understand. What now?"},
        {
            "role": "bot",
            "text": "Suspension from the active roster, effective immediately. Your supervisor will reach out about next steps. There's a path back through retraining and a probationary period — but the audit team needs to be confident this won't recur before you'd resume visits.",
        },
        {"role": "flw", "text": "I understand."},
        {
            "role": "bot",
            "text": "Closing this task as 'suspended'. I know this is hard news — your supervisor will follow up tomorrow.",
        },
    ],
    "coaching_in_progress": [
        # investigating — mid-conversation, no resolution yet.
        {
            "role": "bot",
            "text": "Hi {flw_name}, your supervisor asked me to follow up on a few photos from this week's audit. They want to talk through what happened before flagging anything formally. Have a minute?",
        },
        {"role": "flw", "text": "Yes. Which photos were the issue?"},
        {
            "role": "bot",
            "text": "Two of the MUAC photos from yesterday had readings that didn't look consistent — one read green but the tape position looked too low on the arm. Can you walk me through how you took it?",
        },
        {
            "role": "flw",
            "text": "I think the child was crying and moving so I might have placed the tape too low. I will look at the photo.",
        },
        {
            "role": "bot",
            "text": "Thanks for being honest. Take a look and let me know — if you want to flag it for re-measurement on a follow-up visit, your supervisor can schedule that. Once you've reviewed I'll wait for your reply.",
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
