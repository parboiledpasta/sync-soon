
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import msal
from pydantic import SecretStr

# Add project root to path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from semabridge.core.settings import get_settings
from semabridge.utils.logger import setup_logging, get_logger

setup_logging(level="INFO")
logger = get_logger(__name__)

def get_access_token(config) -> str:
    """Acquire access token for Fabric/Power BI."""
    app = msal.ConfidentialClientApplication(
        client_id=config.client_id,
        client_credential=config.client_secret.get_secret_value(),
        authority=f"https://login.microsoftonline.com/{config.tenant_id}",
    )
    
    # Power BI API needs this scope
    scope = ["https://analysis.windows.net/powerbi/api/.default"]
    result = app.acquire_token_for_client(scopes=scope)
    
    if "access_token" not in result:
        error = result.get("error_description", "Unknown authentication error")
        raise Exception(f"Failed to acquire access token: {error}")
    
    return result["access_token"]

def upload_pbix(file_path: Path, workspace_id: str, display_name: Optional[str] = None):
    """Upload a .pbix file to a Power BI workspace."""
    settings = get_settings()
    config = settings.fabric
    
    if not workspace_id:
        workspace_id = config.workspace_id
        
    token = get_access_token(config)
    
    name = display_name or file_path.stem
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/imports?datasetDisplayName={name}"
    
    headers = {
        "Authorization": f"Bearer {token}",
    }
    
    logger.info(f"Uploading {file_path.name} to workspace {workspace_id}...")
    
    with open(file_path, "rb") as f:
        files = {
            "file": (file_path.name, f, "application/octet-stream")
        }
        
        with httpx.Client(timeout=300) as client:
            response = client.post(url, headers=headers, files=files)
            
            if response.status_code in (200, 201, 202):
                result = response.json()
                import_id = result.get("id")
                logger.info(f"Upload initiated. Import ID: {import_id}")
                
                # Wait for completion
                return poll_import_status(import_id, workspace_id, token)
            else:
                logger.error(f"Upload failed: {response.status_code} - {response.text}")
                return False

def poll_import_status(import_id: str, workspace_id: str, token: str, max_wait: int = 300):
    """Poll for the status of an import."""
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/imports/{import_id}"
    headers = {"Authorization": f"Bearer {token}"}
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        with httpx.Client(timeout=30) as client:
            response = client.get(url, headers=headers)
            if response.status_code == 200:
                status = response.json().get("importState", "").lower()
                if status == "succeeded":
                    logger.info("Import completed successfully!")
                    return True
                elif status == "failed":
                    logger.error("Import failed.")
                    return False
                else:
                    logger.info(f"Status: {status}...")
            else:
                logger.warning(f"Failed to check status: {response.status_code}")
                
        time.sleep(5)
        
    logger.error("Timed out waiting for import.")
    return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python upload_pbix_to_fabric.py <file_path> [workspace_id]")
        sys.exit(1)
        
    pbix_file = Path(sys.argv[1])
    ws_id = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not pbix_file.exists():
        print(f"File not found: {pbix_file}")
        sys.exit(1)
        
    success = upload_pbix(pbix_file, ws_id)
    sys.exit(0 if success else 1)
