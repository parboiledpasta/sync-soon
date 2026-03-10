
from semabridge.core.validation.global_validator import GlobalValidator
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.behavior import SnowflakeBehavior
from dataclasses import dataclass

@dataclass
class MockModel:
    unique_name: str = "continent"
    datasets: list = None
    metrics: list = None
    relationships: list = None

@dataclass
class MockDataset:
    unique_name: str = "continent 1"
    columns: list = None

@dataclass
class MockColumn:
    unique_name: str = "Column1"
    is_key: bool = False
    data_type: str = "STRING"

@dataclass
class MockMetric:
    unique_name: str = "Measure"
    dataset: str = "continent 1"
    source_column: str = None
    aggregation: str = None
    expression: str = None

model = MockModel(
    datasets=[
        MockDataset(
            columns=[MockColumn()]
        )
    ],
    metrics=[
        MockMetric()
    ],
    relationships=[]
)

id_sanitizer = IdentifierSanitizer()
sf_behavior = SnowflakeBehavior()

validator = GlobalValidator(id_sanitizer, sf_behavior)
report = validator.validate(model, halt_on_error=False)

print(f"Warnings: {len(report.issues)}")
for issue in report.issues:
    print(issue)
