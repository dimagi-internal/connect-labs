"""
Forms for solicitations.

These forms work with the LabsRecord API pattern — they produce dicts
via to_data_dict() / get_responses_dict() for the data access layer.
No Django ORM models are involved.
"""
import json

from django import forms

# =========================================================================
# Shared widget classes
# =========================================================================

_INPUT_CLASSES = "base-input"
_TEXTAREA_CLASSES = "base-input"
_SELECT_CLASSES = "base-dropdown"

# =========================================================================
# Choice Constants
# =========================================================================

SOLICITATION_TYPE_CHOICES = [
    ("eoi", "Expression of Interest (EOI)"),
    ("rfp", "Request for Proposal (RFP)"),
]

STATUS_CHOICES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("closed", "Closed"),
    ("awarded", "Awarded"),
]

RECOMMENDATION_CHOICES = [
    ("under_review", "Under Review"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("needs_revision", "Needs Revision"),
]


# =========================================================================
# SolicitationForm
# =========================================================================


class SolicitationForm(forms.Form):
    """
    Form for creating/editing a solicitation.

    Produces a dict via to_data_dict() suitable for passing to
    SolicitationsDataAccess.create_solicitation / update_solicitation.
    """

    title = forms.CharField(
        max_length=255,
        required=True,
        label="Solicitation Title",
        widget=forms.TextInput(attrs={"placeholder": "Enter solicitation title...", "class": _INPUT_CLASSES}),
    )

    description = forms.CharField(
        required=True,
        label="Description",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "Describe the solicitation, its objectives, and requirements...",
                "class": f"{_TEXTAREA_CLASSES} h-auto min-h-40",
            }
        ),
    )

    scope_of_work = forms.CharField(
        required=False,
        label="Scope of Work",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Define the scope of work...",
                "class": f"{_TEXTAREA_CLASSES} h-auto min-h-28",
            }
        ),
    )

    solicitation_type = forms.ChoiceField(
        choices=SOLICITATION_TYPE_CHOICES,
        required=True,
        label="Type",
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=True,
        label="Status",
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )

    is_public = forms.BooleanField(
        required=False,
        initial=True,
        label="Publicly Listed",
        help_text="Make this solicitation visible in public listings",
    )

    application_deadline = forms.DateField(
        required=False,
        label="Application Deadline",
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLASSES}),
    )

    expected_start_date = forms.DateField(
        required=False,
        label="Expected Start Date",
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLASSES}),
    )

    expected_end_date = forms.DateField(
        required=False,
        label="Expected End Date",
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLASSES}),
    )

    estimated_scale = forms.CharField(
        required=False,
        label="Estimated Scale",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 1000 beneficiaries", "class": _INPUT_CLASSES}),
    )

    contact_email = forms.EmailField(
        required=False,
        label="Contact Email",
        widget=forms.EmailInput(attrs={"placeholder": "contact@example.com", "class": _INPUT_CLASSES}),
    )

    questions_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    evaluation_criteria_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def to_data_dict(self) -> dict:
        """Convert cleaned form data to a dict for the data access layer.

        Serializes date fields to ISO strings and parses questions_json
        into a Python list.
        """
        data = self.cleaned_data.copy()

        # Serialize date fields to ISO strings or None
        for date_field in ("application_deadline", "expected_start_date", "expected_end_date"):
            value = data.get(date_field)
            if value:
                data[date_field] = value.isoformat()
            else:
                data[date_field] = None

        # Parse questions_json into a list
        raw_questions = data.pop("questions_json", "")
        if raw_questions:
            try:
                data["questions"] = json.loads(raw_questions)
            except (json.JSONDecodeError, TypeError):
                data["questions"] = []
        else:
            data["questions"] = []

        # Parse evaluation_criteria_json into a list
        raw_criteria = data.pop("evaluation_criteria_json", "")
        if raw_criteria:
            try:
                data["evaluation_criteria"] = json.loads(raw_criteria)
            except (json.JSONDecodeError, TypeError):
                data["evaluation_criteria"] = []
        else:
            data["evaluation_criteria"] = []

        return data


# =========================================================================
# SolicitationResponseForm
# =========================================================================


class SolicitationResponseForm(forms.Form):
    """
    Dynamic form built from solicitation questions.

    Constructor takes a ``questions`` list (from the solicitation record)
    and dynamically creates form fields based on each question's type.
    """

    def __init__(self, questions=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = questions or []

        for question in self.questions:
            q_id = question.get("id", "")
            q_text = question.get("text", "")
            q_type = question.get("type", "text")
            q_required = question.get("required", False)
            q_options = question.get("options", [])

            field_name = f"question_{q_id}"

            if q_type == "textarea":
                field = forms.CharField(
                    label=q_text,
                    required=q_required,
                    widget=forms.Textarea(attrs={"rows": 4, "class": _TEXTAREA_CLASSES}),
                )
            elif q_type == "number":
                field = forms.IntegerField(
                    label=q_text,
                    required=q_required,
                    widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES}),
                )
            elif q_type == "multiple_choice":
                choices = [(opt, opt) for opt in q_options]
                field = forms.ChoiceField(
                    label=q_text,
                    required=q_required,
                    choices=choices,
                    widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
                )
            else:
                # Default: text / short_text / unknown types
                field = forms.CharField(
                    label=q_text,
                    required=q_required,
                    max_length=500,
                    widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
                )

            self.fields[field_name] = field

    def get_responses_dict(self) -> dict:
        """Return a ``{question_id: answer}`` dict from the cleaned data."""
        responses = {}
        for question in self.questions:
            q_id = question.get("id", "")
            field_name = f"question_{q_id}"
            if field_name in self.cleaned_data:
                value = self.cleaned_data[field_name]
                # Include the value even if empty (it was explicitly submitted)
                responses[q_id] = value
        return responses


# =========================================================================
# ReviewForm
# =========================================================================


class ReviewForm(forms.Form):
    """
    Form for reviewing a solicitation response.

    Accepts an optional ``evaluation_criteria`` list to dynamically add
    per-criterion score fields (1-10) alongside the overall review fields.
    """

    score = forms.IntegerField(
        label="Score (1-100)",
        min_value=1,
        max_value=100,
        required=True,
        widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES, "placeholder": "1-100"}),
    )

    recommendation = forms.ChoiceField(
        label="Recommendation",
        choices=RECOMMENDATION_CHOICES,
        required=True,
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )

    notes = forms.CharField(
        label="Review Notes",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "class": _TEXTAREA_CLASSES}),
    )

    tags = forms.CharField(
        label="Tags",
        required=False,
        help_text="Comma-separated tags",
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES, "placeholder": "e.g. quality, scalable"}),
    )

    reward_budget = forms.IntegerField(
        label="Award Budget",
        required=False,
        help_text="Budget to award this grantee",
        widget=forms.NumberInput(attrs={"placeholder": "e.g. 500000", "class": _INPUT_CLASSES}),
    )

    def __init__(self, evaluation_criteria=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evaluation_criteria = evaluation_criteria or []
        for criterion in self.evaluation_criteria:
            field_name = f"criteria_score_{criterion['id']}"
            self.fields[field_name] = forms.IntegerField(
                label=criterion["name"],
                min_value=1,
                max_value=10,
                required=False,
                widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES, "placeholder": "1-10"}),
            )

    def get_criteria_scores(self) -> dict:
        """Return a ``{criterion_id: score}`` dict from the cleaned criteria fields."""
        scores = {}
        for criterion in self.evaluation_criteria:
            field_name = f"criteria_score_{criterion['id']}"
            if field_name in self.cleaned_data and self.cleaned_data[field_name] is not None:
                scores[criterion["id"]] = self.cleaned_data[field_name]
        return scores
