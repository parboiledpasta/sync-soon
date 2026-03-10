
import httpx
import json

def list_pbix_files():
    url = "https://api.github.com/repos/microsoft/powerbi-desktop-samples/contents/powerbi-service-samples"
    print(f"Fetching {url}...")
    
    try:
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        
        files = response.json()
        pbix_files = [f for f in files if f['name'].endswith('.pbix')]
        
        print(f"\nFound {len(pbix_files)} .pbix files:")
        for f in pbix_files:
            print(f"- {f['name']} ({f['download_url']})")
            
        # Save to a file for the next step
        with open("pbix_files_list.json", "w") as f:
            json.dump(pbix_files, f, indent=2)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_pbix_files()
