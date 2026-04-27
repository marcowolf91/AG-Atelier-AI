import json
try:
    with open('darkroom_cache.json', 'r') as f:
        data = json.load(f)
        files = data.get('raw_files', [])
        print(f"Total files in cache: {len(files)}")
        if files:
            print(f"First file name: {files[0].get('name')}")
            print(f"First file keys: {list(files[0].keys())}")
        else:
            print("Cache is empty (raw_files is empty list)")
except Exception as e:
    print(f"Error reading cache: {e}")
