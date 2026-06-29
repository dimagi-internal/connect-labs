import pytest

from commcare_connect.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
)


def test_default_data_source_is_connect_csv():
    config = AnalysisPipelineConfig(grouping_key="username")
    assert config.data_source.type == "connect_csv"
    assert config.data_source.form_name == ""


def test_cchq_forms_data_source():
    ds = DataSourceConfig(
        type="cchq_forms",
        form_name="Register Mother",
        app_id_source="opportunity",
    )
    config = AnalysisPipelineConfig(
        grouping_key="case_id",
        data_source=ds,
        terminal_stage=CacheStage.VISIT_LEVEL,
    )
    assert config.data_source.type == "cchq_forms"
    assert config.data_source.form_name == "Register Mother"


def test_cchq_cases_data_source():
    ds = DataSourceConfig(
        type="cchq_cases",
        case_type="work-area",
    )
    config = AnalysisPipelineConfig(
        grouping_key="entity_id",
        data_source=ds,
        terminal_stage=CacheStage.VISIT_LEVEL,
    )
    assert config.data_source.type == "cchq_cases"
    assert config.data_source.case_type == "work-area"


def test_invalid_data_source_type():
    with pytest.raises(ValueError, match="Invalid data source type"):
        DataSourceConfig(type="invalid")


def test_existing_configs_unaffected():
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="test", path="form.test", aggregation="first")],
        terminal_stage=CacheStage.AGGREGATED,
    )
    assert config.data_source.type == "connect_csv"
