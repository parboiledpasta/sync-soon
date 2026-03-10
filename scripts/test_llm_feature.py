from dotenv import load_dotenv
load_dotenv()

from semabridge.core.llm_config import LLMConfigManager
from semabridge.converter.llm_converter import LLMConverter


def run_test(dax_expression):
    project_config = LLMConfigManager.load_config("config/config.yaml")

    if not project_config.llm.enabled:
        print("❌ LLM is disabled in config.yaml")
        return

    converter = LLMConverter(project_config.llm)

    print("\n🔹 DAX INPUT:")
    print(dax_expression)

    sql = converter.convert(dax_expression)

    print("\n🔹 SQL OUTPUT:")
    print(sql)


if __name__ == "__main__":
    test_cases = [
        "SUM('Sales'[Amount])",
        "CALCULATE(SUM('Sales'[Amount]), 'Product'[Category] = \"Electronics\")",
        "TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])",
    ]

    for dax in test_cases:
        run_test(dax)