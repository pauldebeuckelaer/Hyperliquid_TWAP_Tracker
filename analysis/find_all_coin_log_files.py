import os
import json
from typing import List, Dict, Any
from pathlib import Path

# --- Configuration ---
# Set this to the root directory where your logs are stored.
LOG_ROOT_DIR = '/allcoins_json_logs'


def find_all_coin_log_files(root_dir: str, file_extension: str = '.json') -> List[str]:
    """
    Finds all files with the specified extension recursively using os.walk.
    This method iterates through all directories and files explicitly,
    avoiding the glob module's pattern matching.

    Args:
        root_dir: The starting directory path.
        file_extension: The extension to match (e.g., '.json').

    Returns:
        A list of full file paths for all matching coin log files.
    """
    all_log_paths = []

    # os.walk yields a 3-tuple (dirpath, dirnames, filenames) for each directory
    for dirpath, _, filenames in os.walk(root_dir):
        # We don't need 'dirnames' (list of subdirs), so we use '_'

        # 1. Print the folder being checked (as requested by the user)
        print(f"Checking directory: {dirpath}")

        # 2. Iterate through all files in the current folder
        for filename in filenames:
            if filename.endswith(file_extension):
                # Construct the full file path
                full_path = os.path.join(dirpath, filename)
                all_log_paths.append(full_path)

    return all_log_paths


def analyze_twap_data(log_files: List[str]):
    """
    A placeholder function to show where the analysis logic would go.
    """
    print(f"\n--- Starting Analysis on {len(log_files)} Files ---")
    if not log_files:
        print("No log files found to analyze.")
        return

    # Simulate processing the files found
    for path in log_files:
        try:
            # Example: extracting the coin symbol from the filename
            coin_symbol = os.path.basename(path).split('.')[0]
            print(f"Processing TWAP data for coin: {coin_symbol} (Found at: {path})")

            # Here you would typically open and parse the JSON log file:
            # with open(path, 'r') as f:
            #     data = json.load(f)
            #     # ... perform TWAP calculation ...

        except Exception as e:
            print(f"Error processing file {path}: {e}")


# --- Main Execution ---
if __name__ == '__main__':
    # NOTE: When running this script locally on your machine, you must
    # DELETE the following "MOCKING SECTION" entirely.

    # MOCKING SECTION START: REQUIRED ONLY FOR DEMONSTRATION IN THIS ENVIRONMENT
    # This block creates a fake directory structure and files because this
    # environment cannot access your real local files.
    if not os.path.exists(LOG_ROOT_DIR):
        print(
            f"Directory '{LOG_ROOT_DIR}' not found. Creating a generic mock structure for demonstration of recursion.")

        # Using generic folder names to avoid making assumptions about your data structure
        folder_a = os.path.join(LOG_ROOT_DIR, 'data_group_1')
        folder_b = os.path.join(LOG_ROOT_DIR, 'data_group_2')
        os.makedirs(folder_a, exist_ok=True)
        os.makedirs(folder_b, exist_ok=True)

        # Create mock files, including USOL
        Path(os.path.join(folder_a, 'BTC.json')).touch()
        Path(os.path.join(folder_b, 'USOL.json')).touch()  # The file the user was concerned about
        Path(os.path.join(folder_b, 'OTHER.json')).touch()
        Path(os.path.join(LOG_ROOT_DIR, 'ETH.json')).touch()  # Top-level file

        print("Mock files created across different folders: BTC.json, USOL.json, OTHER.json, ETH.json")
    # MOCKING SECTION END

    # 1. Find all log files recursively
    all_coin_files = find_all_coin_log_files(LOG_ROOT_DIR)

    # 2. Run the analysis
    analyze_twap_data(all_coin_files)

    print("\n--- Summary ---")
    print(f"Successfully located {len(all_coin_files)} files for analysis.")
    if any("USOL" in path for path in all_coin_files):
        print("Confirmation: USOL.json was successfully located.")
    else:
        print("Warning: USOL.json was NOT found based on the mocked structure/search.")