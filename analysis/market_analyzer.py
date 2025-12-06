import pandas as pd
import json
import matplotlib.pyplot as plt
import glob
import os

# --- CONFIGURATION ---
# Update this path to point to your specific folder
# Example: If your project is in C:/Projects, this might be 'allcoins_json_logs/BTC/*.jsonl'
FILE_PATTERN = 'allcoins_json_logs/HYPE/*.jsonl'


def load_and_process_logs(file_pattern):
    files = glob.glob(file_pattern)

    if not files:
        print(f"No files found matching: {file_pattern}")
        return None

    all_data = []

    print(f"Found {len(files)} files. Processing...")

    for file in files:
        print(f"Reading {os.path.basename(file)}...")
        with open(file, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)

                    # specific cleaning for your data structure
                    # We extract specific fields to keep memory usage low

                    row = {
                        'timestamp': entry.get('timestamp'),
                        'symbol': entry.get('symbol'),
                        'price': entry.get('current_price'),
                        # Flatten the summary dictionary directly here
                        'net_flow': entry['summary'].get('net_flow', 0),
                        'buy_vol': entry['summary'].get('buy_volume', 0),
                        'sell_vol': entry['summary'].get('sell_volume', 0),
                        'buy_pressure_min': entry['summary'].get('buy_pressure_per_min', 0),
                        'sell_pressure_min': entry['summary'].get('sell_pressure_per_min', 0),
                    }
                    all_data.append(row)
                except json.JSONDecodeError:
                    continue  # Skip broken lines

    # Create DataFrame
    df = pd.DataFrame(all_data)

    # Convert timestamp to datetime objects
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Sort by time just in case files were read out of order
    df = df.sort_values('timestamp')

    # Handle missing prices (Forward fill: carry last known price forward)
    df['price'] = df['price'].ffill()

    # Calculate Cumulative Net Flow (to see the trend over the day)
    df['cumulative_flow'] = df['net_flow'].cumsum()

    return df


def plot_analysis(df):
    if df is None or df.empty:
        print("No data to plot.")
        return

    # Create a plot with two y-axes (Price vs Cumulative Flow)
    fig, ax1 = plt.subplots(figsize=(12, 6))

    color = 'tab:blue'
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Price', color=color)
    ax1.plot(df['timestamp'], df['price'], color=color, label='Price')
    ax1.tick_params(axis='y', labelcolor=color)
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()  # instantiate a second axes that shares the same x-axis

    color = 'tab:green'
    ax2.set_ylabel('Cumulative Net Flow', color=color)  # we already handled the x-label with ax1
    ax2.plot(df['timestamp'], df['cumulative_flow'], color=color, linestyle='--', label='Cum. Net Flow')
    ax2.tick_params(axis='y', labelcolor=color)

    # Add a zero line for flow
    ax2.axhline(0, color='gray', linewidth=0.8)

    plt.title(f"Price vs Net Flow Analysis ({df['symbol'].iloc[0]})")
    fig.tight_layout()  # otherwise the right y-label is slightly clipped
    plt.show()


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # 1. Load Data
    df = load_and_process_logs(FILE_PATTERN)

    if df is not None:
        # 2. Show basic stats
        print("\n--- Data Summary ---")
        print(df.describe())

        # 3. Plot
        plot_analysis(df)