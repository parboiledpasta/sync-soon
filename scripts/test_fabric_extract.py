
import sys
import json
from pathlib import Path

# Add project root to path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.utils.logger import setup_logging

setup_logging(level="DEBUG")

def test_extract(dataset_id):
    settings = get_settings()
    extractor = FabricExtractor(settings.fabric)
    
    try:
        print(f"\nAttempting to extract definition for ID: {dataset_id}")
        definition = extractor.get_model_definition(dataset_id)
        print("Success! Definition extracted.")
        print(f"BIM Keys: {list(definition.keys()) if isinstance(definition, dict) else 'Not a dict'}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_extract.py <dataset_id>")
        sys.exit(1)
    test_extract(sys.argv[1])
