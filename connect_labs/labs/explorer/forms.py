"""
Forms for Labs Data Explorer
"""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Div, Field, Layout, Row, Submit
from django import forms

from connect_labs.labs.explorer.sql_validator import validate_where_clause


class RecordFilterForm(forms.Form):
    """Form for filtering LabsRecord data."""

    experiment = forms.ChoiceField(
        required=False,
        label="Experiment",
        choices=[("", "All Experiments")],
    )

    type = forms.ChoiceField(
        required=False,
        label="Type",
        choices=[("", "All Types")],
    )

    username = forms.CharField(
        required=False,
        label="Username",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "Filter by username"}),
    )

    date_created_start = forms.DateField(
        required=False,
        label="Created After",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    date_created_end = forms.DateField(
        required=False,
        label="Created Before",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        # Extract dynamic choices from kwargs
        experiment_choices = kwargs.pop("experiment_choices", [])
        type_choices = kwargs.pop("type_choices", [])

        super().__init__(*args, **kwargs)

        # Set dynamic choices
        if experiment_choices:
            self.fields["experiment"].choices = [("", "All Experiments")] + [(exp, exp) for exp in experiment_choices]

        if type_choices:
            self.fields["type"].choices = [("", "All Types")] + [(t, t) for t in type_choices]

        # Setup crispy forms helper
        self.helper = FormHelper(self)
        self.helper.form_method = "get"
        self.helper.form_class = "form-horizontal"
        self.helper.layout = Layout(
            Div(
                Field("experiment", wrapper_class="mb-3"),
                Field("type", wrapper_class="mb-3"),
                Field("username", wrapper_class="mb-3"),
                Row(
                    Column(Field("date_created_start"), css_class="col-md-6"),
                    Column(Field("date_created_end"), css_class="col-md-6"),
                    css_class="row mb-3",
                ),
                Div(
                    Submit("apply", "Apply Filters", css_class="button button-sm primary-dark"),
                    Submit("clear", "Clear Filters", css_class="button button-sm outline-style"),
                    css_class="flex gap-2",
                ),
                css_class="filter-form",
            )
        )
        self.helper.form_tag = True


class RecordEditForm(forms.Form):
    """Form for editing a record's JSON data."""

    data = forms.CharField(
        label="JSON Data",
        widget=forms.Textarea(
            attrs={
                "rows": 20,
                "class": "form-control font-monospace",
                "style": "font-size: 14px;",
            }
        ),
        help_text="Edit the JSON data. Ensure it's valid JSON before saving.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            Field("data"),
            Div(
                Submit("save", "Save Changes", css_class="btn btn-primary"),
                css_class="d-flex gap-2 mt-3",
            ),
        )
        self.helper.form_tag = True

    def clean_data(self):
        """Validate that data is valid JSON."""
        import json

        data_str = self.cleaned_data["data"]
        try:
            data = json.loads(data_str)
            return data
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")


class RecordUploadForm(forms.Form):
    """Form for uploading/importing records."""

    file = forms.FileField(
        label="JSON File",
        help_text="Upload a JSON file containing records to import.",
        widget=forms.FileInput(attrs={"accept": ".json"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            Field("file"),
            Div(
                Submit("upload", "Upload", css_class="btn btn-primary"),
                css_class="d-flex gap-2 mt-3",
            ),
        )
        self.helper.form_tag = True
        self.helper.attrs = {"enctype": "multipart/form-data"}

    def clean_file(self):
        """Validate uploaded file."""
        uploaded_file = self.cleaned_data["file"]

        # Check file extension
        if not uploaded_file.name.endswith(".json"):
            raise forms.ValidationError("File must be a JSON file (.json)")

        # Check file size (max 10MB)
        if uploaded_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("File size must be less than 10MB")

        # Try to read and validate JSON
        try:
            import json

            content = uploaded_file.read().decode("utf-8")
            data = json.loads(content)

            # Validate structure
            if not isinstance(data, list):
                raise forms.ValidationError("JSON must be a list of records")

            # Reset file pointer for later use
            uploaded_file.seek(0)

            # Store parsed data for later use
            self.parsed_data = data

            return uploaded_file
        except UnicodeDecodeError:
            raise forms.ValidationError("File must be UTF-8 encoded")
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")


class VisitInspectorFilterForm(forms.Form):
    """Form for filtering visits with SQL WHERE clauses on form_json."""

    where_clause = forms.CharField(
        required=True,
        label="SQL WHERE Clause",
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "form-control font-monospace",
                "placeholder": "form_json->>'status' = 'complete'",
                "style": "font-size: 14px;",
            }
        ),
        help_text=(
            "Enter a SQL WHERE clause to filter visits by form_json content. "
            "Use PostgreSQL JSONB operators: -> for object navigation, ->> for text extraction. "
            "Example: form_json->'form'->'case'->>'update' = 'success'"
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_class = "form-horizontal"
        self.helper.layout = Layout(
            Div(
                Field("where_clause", wrapper_class="mb-3"),
                Div(
                    Submit("query", "Run Query", css_class="btn btn-primary"),
                    css_class="d-flex gap-2",
                ),
                css_class="filter-form",
            )
        )
        self.helper.form_tag = True

    def clean_where_clause(self):
        """Validate WHERE clause for safety."""
        clause = self.cleaned_data["where_clause"]

        is_valid, error_msg = validate_where_clause(clause)
        if not is_valid:
            raise forms.ValidationError(error_msg)

        return clause
