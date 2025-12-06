import pandas as pd
import numpy as np
import os
import glob
import json
from typing import List, Dict, Any
from datetime import datetime


def load_trades_from_folder(folder_path: str) -> List[Dict[str, Any]]:
    """
    Loads and merges trade data from multiple JSON Lines (.jsonl) files
    within a folder, targeting files for the current date.

    The expected file pattern is: /path/to/folder/SYMBOL_YYYYMMDD.jsonl

    Args:
        folder_path: The base directory path where trade files are located
                     (e.g., /allcoins_json_logs/).

    Returns:
        A combined list of all trade records loaded.
    """

    # 1. Determine the date string for filtering (e.g., '20251201')
    # In a real setup, you would use today's date:
    # date_str = datetime.now().strftime('%Y%m%d')
    date_str = '20251201'  # Using a static date for demonstration

    # 2. Define the search pattern based on the user's structure
    # This will find all files matching *_20251201.jsonl in the specified folder
    search_pattern = os.path.join(folder_path, f'*_{date_str}.jsonl')

    # 3. Simulate finding the files
    # In a real environment, this line would be uncommented:
    # file_paths = glob.glob(search_pattern)
    # print(f"INFO: Searching for files matching: {search_pattern}")
    # print(f"INFO: Found {len(file_paths)} files to process.")

    combined_trades_data: List[Dict[str, Any]] = []

    # 4. --- Simulated File Loading Loop ---

    # The following block *simulates* reading data from those 30+ files
    # and appending the trade records to the combined_trades_data list.

    # for file_path in file_paths:
    #     try:
    #         with open(file_path, 'r') as f:
    #             # Read the file line by line (JSON Lines format)
    #             for line in f:
    #                 trade_record = json.loads(line.strip())
    #                 combined_trades_data.append(trade_record)
    #     except Exception as e:
    #         print(f"Error reading {file_path}: {e}")

    # --- MOCK DATA FOR RUNNABILITY ---
    # Since we cannot access your local file system, we use this mock list
    # to represent the data that would be loaded from all the .jsonl files.

    combined_trades_data = [
        # BTC (Simulated content from BTC_20251201.jsonl)
        {'symbol': 'BTC', 'side': 'SELL', 'usd_value': 100000},
        {'symbol': 'BTC', 'side': 'BUY', 'usd_value': 10000},

        # ETH (Simulated content from ETH_20251201.jsonl)
        {'symbol': 'ETH', 'side': 'SELL', 'usd_value': 50000},
        {'symbol': 'ETH', 'side': 'SELL', 'usd_value': 10000},

        # ALT_A (Simulated content from ALT_A_20251201.jsonl)
        {'symbol': 'ALT_A', 'side': 'BUY', 'usd_value': 15000},
        {'symbol': 'ALT_A', 'side': 'BUY', 'usd_value': 12000},
        {'symbol': 'ALT_A', 'side': 'SELL', 'usd_value': 500},

        # ALT_B (Simulated content from ALT_B_20251201.jsonl)
        {'symbol': 'ALT_B', 'side': 'BUY', 'usd_value': 3000},
        {'symbol': 'ALT_B', 'side': 'SELL', 'usd_value': 4000},

        # SOL (Simulated content from SOL_20251201.jsonl)
        {'symbol': 'SOL', 'side': 'BUY', 'usd_value': 80000},
        {'symbol': 'SOL', 'side': 'SELL', 'usd_value': 5000},
    ]
    # -----------------------------------

    print(f"INFO: Successfully simulated loading and merging {len(combined_trades_data)} trade records for {date_str}.")
    return combined_trades_data


def analyze_net_accumulation(trades_data: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Analyzes trade data to calculate the net buying/selling flow (accumulation)
    for each cryptocurrency symbol over the analyzed period.

    Args:
        trades_data: A list of trade records, each with symbol, side, and usd_value.

    Returns:
        A pandas DataFrame showing the total Net Flow for each symbol, sorted by
        the strongest positive accumulation.
    """
    if not trades_data:
        print("Error: Trade data list is empty. Cannot analyze.")
        return pd.DataFrame()

    try:
        df = pd.DataFrame(trades_data)

        # Ensure all necessary columns exist
        if not all(col in df.columns for col in ['symbol', 'side', 'usd_value']):
            print("Error: DataFrame missing required columns ('symbol', 'side', 'usd_value').")
            return pd.DataFrame()

        # 1. Assign flow direction: BUY is +1, SELL is -1
        df['flow_multiplier'] = df['side'].apply(lambda x: 1 if x == 'BUY' else -1)

        # 2. Calculate directional flow value (Net USD Flow)
        df['net_usd_flow'] = df['usd_value'] * df['flow_multiplier']

        # 3. Aggregate net flow by symbol
        net_flow_by_symbol = df.groupby('symbol')['net_usd_flow'].sum().reset_index()
        net_flow_by_symbol.columns = ['Symbol', 'Net Flow (USD)']

        # 4. Sort results by Net Flow (strongest buying pressure first)
        net_flow_by_symbol = net_flow_by_symbol.sort_values(
            by='Net Flow (USD)',
            ascending=False
        )

        # Format the Net Flow column for readability
        net_flow_by_symbol['Net Flow (USD)'] = net_flow_by_symbol['Net Flow (USD)'].apply(
            lambda x: f"${x:,.2f}"
        )

        return net_flow_by_symbol

    except Exception as e:
        print(f"An error occurred during analysis: {e}", flush=True)
        return pd.DataFrame()


# --- Execution ---

# 1. Define the folder path based on your input
trade_folder_path = "/allcoins_json_logs/"

# 2. Load and consolidate all trade data
# NOTE: This call now simulates reading all *_YYYYMMDD.jsonl files in the path
all_trades = load_trades_from_folder(trade_folder_path)

# 3. Calculate the net flow
accumulation_report = analyze_net_accumulation(all_trades)

# 4. Print the top accumulation candidates (coins with most net buying)
if not accumulation_report.empty:
    print("\n--- Net Accumulation Report (Top 5 Candidates) ---")
    print("These coins have experienced the highest Net Buying Pressure (Accumulation).")
    print("---------------------------------------")
    print(accumulation_report.head(5).to_string(index=False), flush=True)
else:
    print("\nCould not generate the accumulation report due to empty or invalid data.")
