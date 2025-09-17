import json

INPUT_FILE = 'unit_data_output.json'
OUTPUT_FILE = 'unit_data_as_dict.json'

def convert_list_to_dict():
    try:
        with open(INPUT_FILE, 'r') as f:
            data_list = json.load(f)
        print(f"Successfully loaded {len(data_list)} items from '{INPUT_FILE}'.")

    except FileNotFoundError:
        print(f"ERROR: The file '{INPUT_FILE}' was not found.")
        return
    except json.JSONDecodeError:
        print(f"ERROR: The file '{INPUT_FILE}' is not a valid JSON file.")
        return
    except TypeError:
        print(f"ERROR: The data in '{INPUT_FILE}' does not appear to be a list. It might already be a dictionary.")
        return

    new_dict = {}
    for item in data_list:
        key = item.get("definitionName")
        if key:
            new_dict[key] = item
        else:
            print(f"Warning: Skipping an item because it's missing 'definitionName': {item}")
            
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(new_dict, f, indent=4)
        
    print(f"\nSuccess! Converted the list into a dictionary with {len(new_dict)} entries.")
    print(f"The new file is saved as '{OUTPUT_FILE}'.")

if __name__ == "__main__":
    convert_list_to_dict()