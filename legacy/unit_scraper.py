import json
import requests
import time
import os

# --- Configuration ---
INPUT_JSON_FILE = 'units_basic.json'
OUTPUT_JSON_FILE = 'unit_data_output.json'
API_BASE_URL = 'https://gex.honu.pw/api/unit/def-name/'

REQUEST_DELAY = 1
MAX_RETRIES = 3
RETRY_DELAY = 2

def fetch_all_unit_data():
    """
    Parses an input JSON, makes API calls with delays and retries,
    and saves the combined results to a file incrementally.
    """
    
    all_results = []
    fetched_def_names = set()

    if os.path.exists(OUTPUT_JSON_FILE):
        try:
            with open(OUTPUT_JSON_FILE, 'r') as f:
                all_results = json.load(f)
                for item in all_results:
                    if 'definitionName' in item:
                        fetched_def_names.add(item['definitionName'])
                print(f"Resuming. Loaded {len(all_results)} existing records from '{OUTPUT_JSON_FILE}'.")
        except (json.JSONDecodeError, IOError):
            print(f"Warning: Could not read existing output file '{OUTPUT_JSON_FILE}'. Starting fresh.")
            all_results = []
    try:
        with open(INPUT_JSON_FILE, 'r') as f:
            input_data = json.load(f)
            print(f"➡️  Found {len(input_data)} total items to process in '{INPUT_JSON_FILE}'.")
    except FileNotFoundError:
        print(f"ERROR: The file '{INPUT_JSON_FILE}' was not found.")
        return
    except json.JSONDecodeError:
        print(f"ERROR: The file '{INPUT_JSON_FILE}' is not a valid JSON file.")
        return

    for index, item in enumerate(input_data):
        def_name = item.get("definitionName")
        
        if not def_name:
            print(f"WARNING: Skipping an item because it has no 'definitionName': {item}")
            continue

        if def_name in fetched_def_names:
            continue

        print(f"\n[{index + 1}/{len(input_data)}] Processing '{def_name}'...")
        
        for attempt in range(MAX_RETRIES):
            try:
                request_url = f"{API_BASE_URL}{def_name}"
                response = requests.get(request_url)
                response.raise_for_status()
                
                # Success!
                unit_data = response.json()
                all_results.append(unit_data)
                
                with open(OUTPUT_JSON_FILE, 'w') as f:
                    json.dump(all_results, f, indent=4)
                
                print(f"✅ Success. Saved data for '{def_name}'.")
                break
                
            except requests.exceptions.RequestException as e:
                print(f"{attempt + 1}/{MAX_RETRIES} failed for '{def_name}': {e}")
                if attempt < MAX_RETRIES - 1:
                    print(f"Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"FAILED to fetch '{def_name}' after {MAX_RETRIES} attempts.")
        
        time.sleep(REQUEST_DELAY)

    print(f"\nAll done! Final data is in '{OUTPUT_JSON_FILE}'.")

if __name__ == "__main__":
    fetch_all_unit_data()