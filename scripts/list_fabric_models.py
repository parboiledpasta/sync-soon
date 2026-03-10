
import sys
from pathlib import Path

# Add project root to path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.utils.logger import setup_logging

setup_logging(level="INFO")

def list_models():
    settings = get_settings()
    extractor = FabricExtractor(settings.fabric)
    models = extractor.list_semantic_models()
    
    print("\nAvailable Semantic Models:")
    print("-" * 60)
    for m in models:
        print(f"ID: {m.get('id')} | Name: {m.get('displayName')}")
    print("-" * 60)

if __name__ == "__main__":
    list_models()
