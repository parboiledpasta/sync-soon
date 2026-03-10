
import sys
import json
from pathlib import Path

# Add project root to path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.utils.logger import setup_logging

setup_logging(level="DEBUG")

def test_conversion(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    
    print(f"\nAttempting to convert metadata from {json_path}")
    
    try:
        tmsl_converter = TMSLToOSIConverter()
        print("Converting TMSL to OSI...")
        osi_model = tmsl_converter.to_osi({
            "tmsl": metadata,
            "dataset_id": "test_model_id",
        })
        print("OSI Conversion successful.")
        
        sml_converter = OSIToSMLConverter()
        print("Converting OSI to SML...")
        sml_model = sml_converter.from_osi(osi_model)
        print("SML Conversion successful.")
        
        print(f"SML Model: {sml_model.unique_name}")
        print(f"Datasets: {len(sml_model.datasets)}")
        print(f"Metrics: {len(sml_model.metrics)}")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_conversion("output/debug/raw_fabric_model.json")
