"""
Step 1: Benchmark Construction
Treats LQD's full holdings as the benchmark portfolio and computes
aggregate characteristics: sector allocation, maturity profile,
duration, yield, and issuer concentration.

Credit rating distribution is deferred - not available in the public
iShares holdings file (no rating column, no CUSIP to join externally).
"""
import pandas as pd
import numpy as np

AS_OF_DATE = pd.Timestamp('2026-07-22')  # "Fund Holdings as of" from the source file

df = pd.read_csv('data/lqd_holdings_raw.csv', parse_dates=['Maturity', 'Accrual Date', 'Effective Date'])

# Isolate actual bonds - exclude cash/money market sleeve
bonds = df[df['Asset Class'] == 'Fixed Income'].copy()
cash_weight = df.loc[df['Asset Class'] != 'Fixed Income', 'Weight (%)'].sum()

# Renormalize weights to sum to 100% across the bond sleeve only
# (keeps the benchmark focused on the investable bond universe, consistent
# with how we'll construct sampled portfolios later)
bonds['Weight_Renorm'] = bonds['Weight (%)'] / bonds['Weight (%)'].sum() * 100

n_bonds = len(bonds)
n_issuers = bonds['Name'].nunique()

print(f"=== Benchmark Universe ===")
print(f"As of: {AS_OF_DATE.date()}")
print(f"Total bond holdings (rows): {n_bonds}")
print(f"Unique issuers: {n_issuers}")
print(f"Cash/derivatives weight (excluded from bond-only benchmark): {cash_weight:.3f}%")
print()

# ---------- 1. Sector Allocation ----------
sector_alloc = (bonds.groupby('Sector')['Weight_Renorm'].sum()
                 .sort_values(ascending=False))
print("=== Sector Allocation (% of bond sleeve) ===")
print(sector_alloc.round(2))
print()

# ---------- 2. Maturity Profile ----------
bonds['Years_to_Maturity'] = (bonds['Maturity'] - AS_OF_DATE).dt.days / 365.25

bins = [0, 1, 2, 3, 5, 7, 10, 15, 20, 100]
labels = ['0-1yr', '1-2yr', '2-3yr', '3-5yr', '5-7yr', '7-10yr', '10-15yr', '15-20yr', '20yr+']
bonds['Maturity_Bucket'] = pd.cut(bonds['Years_to_Maturity'], bins=bins, labels=labels)

maturity_profile = (bonds.groupby('Maturity_Bucket', observed=True)['Weight_Renorm'].sum()
                     .reindex(labels))
print("=== Maturity Profile (% of bond sleeve, by years to maturity) ===")
print(maturity_profile.round(2))
print()

weighted_avg_maturity = (bonds['Years_to_Maturity'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
print(f"Weighted average years to maturity: {weighted_avg_maturity:.2f}")
print()

# ---------- 3. Duration ----------
weighted_duration = (bonds['Duration'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
weighted_mod_duration = (bonds['Mod. Duration'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
print(f"=== Duration ===")
print(f"Weighted average effective duration: {weighted_duration:.2f} yrs")
print(f"Weighted average modified duration:  {weighted_mod_duration:.2f} yrs")
print()

# ---------- 4. Yield ----------
weighted_ytm = (bonds['YTM (%)'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
weighted_ytw = (bonds['Yield to Worst (%)'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
weighted_coupon = (bonds['Coupon (%)'] * bonds['Weight_Renorm']).sum() / bonds['Weight_Renorm'].sum()
print(f"=== Yield ===")
print(f"Weighted average YTM:            {weighted_ytm:.3f}%")
print(f"Weighted average Yield to Worst: {weighted_ytw:.3f}%")
print(f"Weighted average coupon:         {weighted_coupon:.3f}%")
print()

# ---------- 5. Issuer Concentration ----------
issuer_weights = (bonds.groupby('Name')['Weight_Renorm'].sum()
                   .sort_values(ascending=False))
top10 = issuer_weights.head(10)
hhi = (issuer_weights ** 2).sum()  # Herfindahl-Hirschman Index (weights in %, so HHI on 0-10000 scale)

print("=== Issuer Concentration ===")
print(f"Top 10 issuers (% of bond sleeve):")
print(top10.round(3))
print(f"\nTop 10 issuer weight sum: {top10.sum():.2f}%")
print(f"Herfindahl-Hirschman Index (issuer): {hhi:.1f}  (max=10,000 fully concentrated)")
print(f"Effective number of issuers (10000/HHI): {10000/hhi:.1f}")
print()

# ---------- Save everything ----------
summary = {
    'as_of_date': str(AS_OF_DATE.date()),
    'n_bond_holdings': int(n_bonds),
    'n_unique_issuers': int(n_issuers),
    'cash_derivatives_weight_pct': round(float(cash_weight), 4),
    'weighted_avg_years_to_maturity': round(float(weighted_avg_maturity), 3),
    'weighted_avg_effective_duration': round(float(weighted_duration), 3),
    'weighted_avg_mod_duration': round(float(weighted_mod_duration), 3),
    'weighted_avg_ytm_pct': round(float(weighted_ytm), 4),
    'weighted_avg_ytw_pct': round(float(weighted_ytw), 4),
    'weighted_avg_coupon_pct': round(float(weighted_coupon), 4),
    'top10_issuer_weight_pct': round(float(top10.sum()), 3),
    'issuer_hhi': round(float(hhi), 2),
    'effective_n_issuers': round(float(10000/hhi), 2),
    'rating_distribution': 'NOT AVAILABLE - deferred (no rating field in source data)',
}

import json
with open('output/benchmark_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)

sector_alloc.to_csv('output/benchmark_sector_allocation.csv', header=['weight_pct'])
maturity_profile.to_csv('output/benchmark_maturity_profile.csv', header=['weight_pct'])
issuer_weights.to_csv('output/benchmark_issuer_weights.csv', header=['weight_pct'])
bonds.to_csv('output/lqd_bonds_clean.csv', index=False)

print("Saved: output/benchmark_summary.json")
print("Saved: output/benchmark_sector_allocation.csv")
print("Saved: output/benchmark_maturity_profile.csv")
print("Saved: output/benchmark_issuer_weights.csv")
print("Saved: output/lqd_bonds_clean.csv  (cleaned bond-level dataset, deliverable #1)")
