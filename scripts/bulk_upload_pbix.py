
import sys
import os
import subprocess
from pathlib import Path

# Add project root to path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from semabridge.core.settings import get_settings

def bulk_upload():
    # Use configured workspace or fallback to the one used previously
    workspace_id = "1a5e9594-c112-43d0-8cdd-012f7746c1b1"
    
    samples_dir = Path("temp/pbix_samples")
    if not samples_dir.exists():
        print(f"Directory not found: {samples_dir}")
        return

    pbix_files = list(samples_dir.glob("*.pbix"))
    print(f"Found {len(pbix_files)} files to upload to workspace {workspace_id}")

    for i, file_path in enumerate(pbix_files, 1):
        print(f"\n[{i}/{len(pbix_files)}] Uploading {file_path.name}...")
        
        # Call the existing upload script
        cmd = [
            sys.executable,
            "scripts/upload_pbix_to_fabric.py",
            str(file_path),
            workspace_id
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"SUCCESS: {file_path.name}")
        else:
            print(f"FAILURE: {file_path.name}")
            print(result.stdout)
            print(result.stderr)

if __name__ == "__main__":
    bulk_upload()
