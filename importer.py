"""
importer.py — Parse Bitget CSV exports and load them into SQLite.

Bitget exports four CSVs for USDT-M Futures:
  1. position_history  — one row per closed position  → positions table (PRIMARY)
  2. order_history     — one order per row            → orders table
  3. order_details     — individual fills             → ignored (redundant with above)
  4. transactions      — wallet balance events        → wallet_snapshots table

Call:  python3 importer.py /path/to/export/folder
Or use the /api/import endpoint which calls import_folder() directly.
"""

import csv
import os
import re
import sqlite3
from datetime import datetime

from database import get_conn, init_db


# ── helpers ────────────────────────────────────────────────────────────────────

def _clean_float(val):
    """Convert a string like '  -0.02924180448' or '19.35USDT' or '' to float or None."""
    if val is None:
        return None
    import re as _re
    val = str(val).strip().lstrip('﻿')
    # Strip trailing units like 'USDT', 'BTC', 'BOME', etc.
    val = _re.sub(r'[A-Za-z]+$', '', val).strip()
    if val == '' or val == '-':
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _clean_str(val):
    return str(val).strip().lstrip('﻿') if val else ''


def _duration_minutes(open_str, close_str):
    """Return integer minutes between two ISO datetime strings."""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        dt_open  = datetime.strptime(open_str.strip(),  fmt)
        dt_close = datetime.strptime(close_str.strip(), fmt)
        return int((dt_close - dt_open).total_seconds() / 60)
    except Exception:
        return None


def _parse_futures_field(futures_str):
    """
    Parse Bitget's 'Futures' column like 'BOMEUSDT Long·Cross'
    into (symbol, direction, margin_mode, base_asset).
    """
    s = _clean_str(futures_str)
    # direction
    direction = 'Long' if 'Long' in s else ('Short' if 'Short' in s else '')
    # margin mode
    margin_mode = 'Cross' if 'Cross' in s else ('Isolated' if 'Isolated' in s else '')
    # symbol: everything before the first space
    symbol = s.split(' ')[0] if s else ''
    # base asset: strip 'USDT' suffix
    base_asset = re.sub(r'USDT$', '', symbol)
    return symbol, direction, margin_mode, base_asset


# ── position history (PRIMARY import) ─────────────────────────────────────────

def import_position_history(filepath, conn):
    """
    Parse 'Exported USDT-M Futures position history ...' CSV.

    Expected columns (Bitget export, May 2026):
      Futures, Opening time, Average entry price, Average closing price,
      Closed amount, Closed value, Position Pnl, Realized PnL,
      Fees, Opening fee, Closing fee, Closed time
    """
    cur = conn.cursor()
    imported = 0
    skipped  = 0

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            futures_raw = _clean_str(row.get('Futures', ''))
            if not futures_raw:
                continue

            symbol, direction, margin_mode, base_asset = _parse_futures_field(futures_raw)
            open_time  = _clean_str(row.get('Opening time', ''))
            close_time = _clean_str(row.get('Closed time',  ''))

            # check for duplicate (same symbol + open + close time)
            existing = cur.execute(
                "SELECT id FROM positions WHERE symbol=? AND open_time=? AND close_time=?",
                (symbol, open_time, close_time)
            ).fetchone()
            if existing:
                skipped += 1
                continue

            entry_price  = _clean_float(row.get('Average entry price'))
            close_price  = _clean_float(row.get('Average closing price'))
            size_usdt    = _clean_float(row.get('Closed value'))
            position_pnl = _clean_float(row.get('Position Pnl'))
            realized_pnl = _clean_float(row.get('Realized PnL'))
            opening_fee  = _clean_float(row.get('Opening fee'))
            closing_fee  = _clean_float(row.get('Closing fee'))
            total_fees   = _clean_float(row.get('Fees'))
            size_raw     = _clean_str(row.get('Closed amount', ''))
            duration     = _duration_minutes(open_time, close_time)

            cur.execute("""
                INSERT INTO positions
                  (symbol, base_asset, direction, margin_mode,
                   open_time, close_time, duration_minutes,
                   entry_price, close_price,
                   size_contracts, size_usdt,
                   position_pnl, realized_pnl,
                   opening_fee, closing_fee, total_fees)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, base_asset, direction, margin_mode,
                open_time, close_time, duration,
                entry_price, close_price,
                size_raw, size_usdt,
                position_pnl, realized_pnl,
                opening_fee, closing_fee, total_fees
            ))
            imported += 1

    conn.commit()
    print(f"[Import] position_history: {imported} imported, {skipped} skipped (duplicates)")
    return imported


# ── order history ──────────────────────────────────────────────────────────────

def import_order_history(filepath, conn):
    """
    Parse 'Exported USDT-M Futures order history ...' CSV.

    Expected columns:
      Date, Order ID, Direction, Coin, Futures, order source,
      Transaction type, Price, Average Price, Order amount,
      Executed, Trading volume, Realized P/L, NetProfits, Status
    """
    cur = conn.cursor()
    imported = 0
    skipped  = 0

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            order_id = _clean_str(row.get('Order ID', '')).strip()
            if not order_id:
                continue

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO orders
                      (order_id, date, direction, symbol, order_source,
                       transaction_type, price, avg_price,
                       order_amount, executed, trading_volume,
                       realized_pnl, net_profits, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    order_id,
                    _clean_str(row.get('Date')),
                    _clean_str(row.get('Direction')),
                    _clean_str(row.get('Futures')),
                    _clean_str(row.get('order source')),
                    _clean_str(row.get('Transaction type')),
                    _clean_float(row.get('Price')),
                    _clean_float(row.get('Average Price')),
                    _clean_float(row.get('Order amount')),
                    _clean_float(row.get('Executed')),
                    _clean_float(row.get('Trading volume')),
                    _clean_float(row.get('Realized P/L')),
                    _clean_float(row.get('NetProfits')),
                    _clean_str(row.get('Status')),
                ))
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    print(f"[Import] order_history: {imported} imported, {skipped} skipped")
    return imported


# ── transactions (wallet balance history) ─────────────────────────────────────

def import_transactions(filepath, conn):
    """
    Parse 'Exported USDT-M Futures transactions ...' CSV.

    Expected columns:
      Order, Date, Coin, Futures, Margin Mode, Type, Amount, Fee, Wallet balance
    """
    cur = conn.cursor()
    imported = 0

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur.execute("""
                INSERT INTO wallet_snapshots
                  (order_ref, date, symbol, futures, margin_mode,
                   type, amount, fee, wallet_balance)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                _clean_str(row.get('Order')),
                _clean_str(row.get('Date')),
                _clean_str(row.get('Coin')),
                _clean_str(row.get('Futures')),
                _clean_str(row.get('Margin Mode')),
                _clean_str(row.get('Type')),
                _clean_float(row.get('Amount')),
                _clean_float(row.get('Fee')),
                _clean_float(row.get('Wallet balance')),
            ))
            imported += 1

    conn.commit()
    print(f"[Import] transactions: {imported} imported")
    return imported


# ── auto-detect and import a whole folder ─────────────────────────────────────

def import_folder(folder_path, conn=None):
    """
    Scan a folder for Bitget CSV exports and import each one.
    Returns a dict with import counts per file type.
    """
    if conn is None:
        conn = get_conn()

    results = {}
    cur = conn.cursor()

    for fname in os.listdir(folder_path):
        if not fname.lower().endswith('.csv'):
            continue
        fpath = os.path.join(folder_path, fname)
        flower = fname.lower()

        if 'position history' in flower:
            n = import_position_history(fpath, conn)
            results['positions'] = n
            file_type = 'positions'
        elif 'order history' in flower:
            n = import_order_history(fpath, conn)
            results['orders'] = n
            file_type = 'orders'
        elif 'transactions' in flower:
            n = import_transactions(fpath, conn)
            results['transactions'] = n
            file_type = 'transactions'
        elif 'order details' in flower:
            # order details are redundant with order history; skip
            print(f"[Import] Skipping order details file (redundant): {fname}")
            continue
        else:
            print(f"[Import] Unknown file, skipping: {fname}")
            continue

        cur.execute(
            "INSERT INTO import_log (filename, file_type, rows_imported) VALUES (?,?,?)",
            (fname, file_type, n)
        )
        conn.commit()

    return results


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 importer.py /path/to/bitget/export/folder")
        sys.exit(1)

    init_db()
    conn = get_conn()
    results = import_folder(sys.argv[1], conn)
    conn.close()
    print(f"\n[Done] Import summary: {results}")
