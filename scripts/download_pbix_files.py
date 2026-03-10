
import os
import json
import httpx
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

def download_file(file_info, output_dir):
    name = file_info['name']
    url = file_info['download_url']
    dest = output_dir / name
    
    if dest.exists():
        print(f"Skipping {name} (already exists)")
        return
    
    print(f"Downloading {name}...")
    try:
        response = httpx.get(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
        
        with open(dest, "wb") as f:
            f.write(response.content)
        print(f"Downloaded {name}")
    except Exception as e:
        print(f"Failed to download {name}: {e}")

def main():
    with open("pbix_files_list.json", "r") as f:
        files = json.load(f)
    
    output_dir = Path("temp/pbix_samples")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Downloading {len(files)} files to {output_dir}...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(lambda f: download_file(f, output_dir), files)

if __name__ == "__main__":
    main()
