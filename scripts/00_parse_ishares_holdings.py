"""
Step 0: Parse iShares Holdings Files

iShares' "Data Download" files (despite the .xls extension) are actually
SpreadsheetML XML, not binary Excel - standard libraries like xlrd/openpyxl
cannot read them. This script parses the "Holdings" worksheet directly from
the XML and writes a clean CSV.

Used for three funds in this project, all downloaded manually from
ishares.com (the files are served from blackrock.com, which isn't reachable
from an automated/sandboxed environment - see README for direct links):

  - LQD  (iShares iBoxx $ Investment Grade Corporate Bond ETF)  - the benchmark
  - QLTA (iShares Aaa-A Rated Corporate Bond ETF)   - used to infer ratings
  - LQDB (iShares BBB Rated Corporate Bond ETF)     - used to infer ratings

Usage:
    python 00_parse_ishares_holdings.py <input.xls> <output.csv>
"""
import re
import sys
import pandas as pd


def parse_ishares_xls(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    m = re.search(r'<ss:Worksheet ss:Name="Holdings">(.*?)</ss:Worksheet>', content, re.DOTALL)
    if m is None:
        raise ValueError(f"Could not find a 'Holdings' worksheet in {path} - "
                          "is this really an iShares Data Download file?")
    holdings_xml = m.group(1)
    rows = re.findall(r'<ss:Row[^>]*>(.*?)</ss:Row>', holdings_xml, re.DOTALL)

    def parse_row(r):
        return re.findall(r'<ss:Data ss:Type="[^"]*">(.*?)</ss:Data>', r, re.DOTALL)

    # The header row is the first row with many header-styled cells; several
    # metadata/title rows precede it (fund name, as-of date, disclaimers).
    header_idx = None
    for i, r in enumerate(rows):
        if r.count('headerstyle') > 5:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not locate the header row in {path}")

    header = parse_row(rows[header_idx])
    data_rows = [parse_row(r) for r in rows[header_idx + 1:]]
    data_rows = [r for r in data_rows if len(r) == len(header)]  # drop footnote/blank rows

    df = pd.DataFrame(data_rows, columns=header)

    # un-escape XML entities
    for col in df.columns:
        df[col] = (df[col].str.replace('&amp;', '&')
                           .str.replace('&lt;', '<')
                           .str.replace('&gt;', '>')
                           .str.replace('&apos;', "'")
                           .str.replace('&quot;', '"'))

    # cast the numeric/date columns we know appear in these files
    numeric_cols = ['Market Value', 'Weight (%)', 'Notional Value', 'Par Value', 'Price',
                     'Duration', 'YTM (%)', 'FX Rate', 'Coupon (%)', 'Mod. Duration',
                     'Yield to Call (%)', 'Yield to Worst (%)', 'Real Duration', 'Real YTM (%)']
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].str.replace(',', ''), errors='coerce')

    date_cols = ['Maturity', 'Accrual Date', 'Effective Date']
    for c in date_cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], format='%b %d, %Y', errors='coerce')

    return df


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    df = parse_ishares_xls(input_path)
    df.to_csv(output_path, index=False)
    print(f"Parsed {len(df)} rows ({df['Asset Class'].value_counts().to_dict()}) -> {output_path}")
