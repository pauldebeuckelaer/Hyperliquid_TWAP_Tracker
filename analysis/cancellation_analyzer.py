import pandas as pd
import json
import matplotlib.pyplot as plt
import glob
import os

# --- CONFIGURATION ---
# IMPORTANT: Update this path if you want to analyze a different coin or all coins
FILE_PATTERN = 'allcoins_json_logs/HYPE/*.jsonl'
CANCELLATION_THRESHOLD = 50000  # Only log individual canceled orders > $50,000 USD value


def load_and_analyze_cancellations(file_pattern):
    files = glob.glob(file_pattern)
    if not files:
        print(f"No files found matching: {file_pattern}")
        return None, []

    all_data = []
    large_cancellations = []

    print(f"Found {len(files)} files. Analyzing cancellations...")

    for file in files:
        print(f"Reading {os.path.basename(file)}...")
        with open(file, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    timestamp = entry.get('timestamp')
                    symbol = entry.get('symbol')
                    price = entry.get('current_price')

                    # We need a valid price to calculate USD value of the canceled orders
                    if not price:
                        continue

                        # --- 1. Aggregate Cancellation Data per Snapshot ---
                    total_canceled_volume = 0.0
                    largest_canceled_order_usd = 0.0

                    # Canceled orders are logged in the 'canceled_orders' array
                    if 'canceled_orders' in entry and entry['canceled_orders']:
                        for order in entry['canceled_orders']:
                            size = order.get('size', 0)
                            side = order.get('side', 'UNKNOWN')
                            usd_value = size * price

                            total_canceled_volume += size  # Total coin volume canceled

                            # Track the largest single canceled order
                            if usd_value > largest_canceled_order_usd:
                                largest_canceled_order_usd = usd_value

                            # --- 2. Log large, individual cancellation events (Whale-level) ---
                            if usd_value >= CANCELLATION_THRESHOLD:
                                large_cancellations.append({
                                    'timestamp': timestamp,
                                    'symbol': symbol,
                                    'side': side,
                                    'size': size,
                                    'usd_value': round(usd_value, 2),
                                    'address': order.get('address'),
                                    'price_at_time': price
                                })

                    # Store the aggregated data for plotting
                    all_data.append({
                        'timestamp': timestamp,
                        'symbol': symbol,
                        'price': price,
                        'canceled_volume_coin': total_canceled_volume,
                        'largest_canceled_usd': largest_canceled_order_usd
                    })

                except json.JSONDecodeError:
                    continue

                    # Create DataFrame for plotting
    df = pd.DataFrame(all_data)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df['price'] = df['price'].ffill()  # Forward fill price gaps

    # Sum up the canceled volume for a cumulative view
    df['cumulative_canceled_volume'] = df['canceled_volume_coin'].cumsum()

    return df, large_cancellations


def plot_cancellation_analysis(df):
    if df is None or df.empty:
        print("No data to plot.")
        return

    # Create a plot with three y-axes (Price, Cumulative Canceled Volume, Largest Canceled Order)
    fig, ax1 = plt.subplots(figsize=(14, 7))

    # --- AXIS 1: Price (Blue) ---
    color = 'tab:blue'
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Price', color=color)
    ax1.plot(df['timestamp'], df['price'], color=color, label='Price')
    ax1.tick_params(axis='y', labelcolor=color)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')

    # --- AXIS 2: Cumulative Canceled Volume (Orange/Red) ---
    ax2 = ax1.twinx()
    color = 'tab:orange'
    ax2.set_ylabel('Cumulative Canceled Volume (Coins)', color=color)
    ax2.plot(df['timestamp'], df['cumulative_canceled_volume'], color=color, linestyle=':',
             label='Cum. Canceled Volume')
    ax2.tick_params(axis='y', labelcolor=color)
    ax2.legend(loc='upper right', bbox_to_anchor=(1, 0.9))

    # --- AXIS 3: Largest Single Canceled Order (Green Dots/Markers) ---
    # We use ax1 again and offset the scale/color if needed, but here we just plot points
    ax3 = ax1.twinx()
    # Move the third axis over to the right slightly (less clean, but shows the data)
    ax3.spines['right'].set_position(('outward', 60))

    # We only want to plot non-zero large cancellations
    df_large = df[df['largest_canceled_usd'] > 0].copy()

    # Create markers based on size for visualization
    df_large['marker_size'] = (df_large['largest_canceled_usd'] / 100000) * 15  # Scale for visibility

    color_3 = 'tab:green'
    # Plotting the raw value, but we need a visible y-axis for it
    ax3.set_ylabel('Largest Single Canceled Order (USD)', color=color_3)
    ax3.scatter(df_large['timestamp'], df_large['largest_canceled_usd'],
                s=df_large['marker_size'], color=color_3, alpha=0.6, label='Largest Single Cancel')
    ax3.tick_params(axis='y', labelcolor=color_3)
    ax3.legend(loc='upper right', bbox_to_anchor=(1, 0.8))

    plt.title(f"Cancellation Pattern Analysis ({df['symbol'].iloc[0]})")
    fig.tight_layout()
    plt.show()


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    df, large_cancellations = load_and_analyze_cancellations(FILE_PATTERN)

    if df is not None:
        # Plotting the aggregated cancellation metrics
        plot_cancellation_analysis(df)

        # Printing the specific whale-level cancellation events
        print("\n--- Top Individual Cancellation Events (Spoofing/Iceberg Detection) ---")
        if large_cancellations:
            # Sort and display the top 20 largest cancellations by USD value
            top_cancellations = sorted(large_cancellations, key=lambda x: x['usd_value'], reverse=True)[:20]

            # Convert to DataFrame for pretty printing
            df_top = pd.DataFrame(top_cancellations)
            df_top = df_top[['timestamp', 'symbol', 'side', 'usd_value', 'size', 'address']]

            print(df_top.to_string(index=False), flush=True)

            print(f"\nTotal unique snapshots with large cancellations: {len(large_cancellations)}", flush=True)
        else:
            print("No individual canceled orders found above the $50,000 threshold.")