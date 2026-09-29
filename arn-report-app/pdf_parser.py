"""
Parser for ARN "Post Times" PDFs.

The report is positional text, not a ruled table. Each page carries a two-line
header (client, then package with the page number appended), and spot rows are
laid out in fixed columns:

    Contract | Station | Spot Class | Daypart | Start Time | End Time |
    Aired Date | Aired Time | Dur | AiredDur+Key | PIB | Spot Rate | Ratecard Rate

A row that aired carries an Aired Date and Aired Time. Rows still scheduled have
those two columns blank — they sit at the end of the report and are excluded.

Spot type comes from the Daypart column, not the Spot Class column (which is
"Commercial" for everything): BMAD is the paid 6AM-7PM rotation, Bonus BTA /
ACQ Bonus / Filler are added-value spots carried at $0.00.
"""
import pdfplumber
import re
from datetime import datetime, date

# Column boundaries in PDF points, taken from the header row of a real post log.
# A word's x0 decides its column, so small horizontal drift between reports is fine.
COLUMNS = [
    ('contract',    0,   80),
    ('station',     80,  133),
    ('spot_class',  133, 190),
    ('daypart',     190, 250),
    ('start_time',  250, 302),
    ('end_time',    302, 352),
    ('aired_date',  352, 402),
    ('aired_time',  402, 490),
    ('dur',         490, 540),
    ('adur_key',    540, 645),
    ('pib',         645, 675),
    ('rate',        675, 726),
    ('rc_rate',     726, 10_000),
]

DATE_RE = re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$')
TIME_RE = re.compile(r'^(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)?$', re.I)
MONEY_RE = re.compile(r'\$?\s*([\d,]+(?:\.\d+)?)')

# Daypart value -> the spot type shown in the report.
SPOT_TYPES = [
    ('LIVE READ',  'Live Read'),
    ('LIVEREAD',   'Live Read'),
    ('BONUS BTA',  'Bonus BTA'),
    ('ACQ BONUS',  'ACQ Bonus'),
    ('ACQ',        'ACQ Bonus'),
    ('FILLER',     'Filler'),
    ('BMAD',       'BMAD'),
]
# Types carried at no cost to the client; their ratecard value is imputed.
BONUS_TYPES = ('Bonus BTA', 'ACQ Bonus', 'Filler')


# ── Extraction ────────────────────────────────────────────────────────────────
def _page_rows(page):
    """Group a page's words into rows, then bucket each row's words by column."""
    words = page.extract_words()
    lines = {}
    for w in words:
        # Round the baseline so words on the same visual line group together.
        key = round(w['top'] / 3)
        lines.setdefault(key, []).append(w)

    out = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w['x0'])
        cells = {name: [] for name, _, _ in COLUMNS}
        for w in ws:
            for name, lo, hi in COLUMNS:
                if lo <= w['x0'] < hi:
                    cells[name].append(w['text'])
                    break
        out.append({k: ' '.join(v).strip() for k, v in cells.items()})
    return out


def _clean_station(s: str) -> str:
    """Tidy the station name ('PowerFM -' renders with a trailing dash)."""
    s = s.strip().rstrip('-').strip()
    if not s:
        return ''
    if re.fullmatch(r'power\s*fm', s, re.I):
        return 'Power FM'
    return s


def _spot_type(daypart: str) -> str:
    d = daypart.upper()
    for needle, name in SPOT_TYPES:
        if needle in d:
            return name
    return daypart.strip() or 'Other'


def _money(s: str) -> float:
    m = MONEY_RE.search(s or '')
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(',', ''))
    except ValueError:
        return 0.0


def _hour(s: str):
    """Hour of day (0-23) from an 'H:MM:SS AM/PM' cell, or None."""
    m = TIME_RE.match((s or '').strip())
    if not m:
        return None
    h = int(m.group(1))
    ampm = (m.group(4) or '').upper()
    if ampm == 'PM' and h != 12:
        h += 12
    elif ampm == 'AM' and h == 12:
        h = 0
    return h


def extract_metadata(pdf_path: str) -> dict:
    """
    Client and package come from the repeating two-line page header; the
    contract number comes from the rows themselves.
    """
    meta = {'client_name': '', 'package_name': '', 'contract_number': ''}
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) < 1:
            return meta
        # Page 2+ starts directly with the header pair, so it's the cleanest
        # source. Fall back to page 1, which is prefixed by the report title.
        page = pdf.pages[1] if len(pdf.pages) > 1 else pdf.pages[0]
        lines = [l.strip() for l in (page.extract_text() or '').split('\n') if l.strip()]

    header = []
    for l in lines:
        # Stop at the first data row or date separator.
        if re.match(r'^\d{6}\s', l) or DATE_RE.match(l):
            break
        if l.startswith('ARN Post Times') or l.startswith('Printed:'):
            continue
        if re.fullmatch(r'\d+', l):      # bare page number
            continue
        if l.startswith('Contract ') or l == 'Ratecard':
            continue
        header.append(l)

    if header:
        meta['client_name'] = header[0]
    if len(header) > 1:
        # The package line carries the page number appended — strip it.
        meta['package_name'] = re.sub(r'\s+\d{1,3}$', '', header[1]).strip()
    return meta


def parse_arn_pdf(pdf_path: str) -> dict:
    """Parse the post log into aired spots plus aggregate statistics."""
    today = date.today()
    spots = []
    contract = ''

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for cells in _page_rows(page):
                if not re.fullmatch(r'\d{4,}', cells['contract'] or ''):
                    continue  # header, page number, date separator, or footer
                contract = contract or cells['contract']

                # An aired spot has both an aired date and an aired time.
                # Rows missing them are still scheduled — exclude them.
                adate, atime = cells['aired_date'], cells['aired_time']
                if not DATE_RE.match(adate or '') or not atime:
                    continue
                try:
                    d = datetime.strptime(adate, '%d/%m/%Y').date()
                except ValueError:
                    continue
                if d > today:
                    continue  # defensive: a future date is not "aired"

                stype = _spot_type(cells['daypart'])
                spots.append({
                    'aired_date': d,
                    'hour': _hour(atime),
                    'station': _clean_station(cells['station']),
                    'spot_type': stype,
                    'duration': int(cells['dur']) if cells['dur'].isdigit() else 30,
                    'rate': _money(cells['rate']),
                    'rc_rate': _money(cells['rc_rate']),
                    'month': d.strftime('%B %Y'),
                })

    result = _aggregate(spots)
    meta = extract_metadata(pdf_path)
    meta['contract_number'] = contract or meta.get('contract_number', '')
    result['meta'] = meta
    return result


# ── Aggregation ───────────────────────────────────────────────────────────────
def _aggregate(spots: list) -> dict:
    if not spots:
        return _empty_aggregate()

    # The ratecard rate to value bonus spots at: post logs list $0.00 ratecard
    # against a $0.00 bonus spot, so use the paid rotation's published rate.
    paid_rcs = [s['rc_rate'] for s in spots
                if s['spot_type'] not in BONUS_TYPES and s['rc_rate'] > 0]
    imputed_rc = max(set(paid_rcs), key=paid_rcs.count) if paid_rcs else 0.0

    type_data, station_data, station_type, months, hours = {}, {}, {}, {}, {}
    stations, aired_dates = set(), set()
    total_duration = 0

    for s in spots:
        st, dur, rate = s['spot_type'], s['duration'], s['rate']
        station = s['station'] or 'Unspecified'
        # Value bonus spots at the paid ratecard rate when the log shows $0.00.
        rc = s['rc_rate'] if s['rc_rate'] > 0 else (
            imputed_rc if st in BONUS_TYPES else rate
        )

        aired_dates.add(s['aired_date'])
        if s['station']:
            stations.add(s['station'])
        total_duration += dur

        td = type_data.setdefault(st, {
            'count': 0, 'duration': 0, 'rate': rate, 'rc_rate': rc,
            'cost': 0.0, 'rc_value': 0.0,
        })
        td['count'] += 1
        td['duration'] += dur
        td['cost'] += rate
        td['rc_value'] += rc
        if rate > td['rate']:
            td['rate'] = rate
        if rc > td['rc_rate']:
            td['rc_rate'] = rc

        sd = station_data.setdefault(station, {
            'count': 0, 'duration': 0, 'cost': 0.0, 'rc_value': 0.0})
        sd['count'] += 1
        sd['duration'] += dur
        sd['cost'] += rate
        sd['rc_value'] += rc

        stt = station_type.setdefault(station, {}).setdefault(st, {
            'count': 0, 'duration': 0, 'cost': 0.0, 'rc_value': 0.0})
        stt['count'] += 1
        stt['duration'] += dur
        stt['cost'] += rate
        stt['rc_value'] += rc

        if s['hour'] is not None:
            hours[s['hour']] = hours.get(s['hour'], 0) + 1

        m = months.setdefault(s['month'], {}).setdefault(st, {
            'count': 0, 'duration': 0, 'cost': 0.0})
        m['count'] += 1
        m['duration'] += dur
        m['cost'] += rate

    return {
        'spots': spots,
        'type_data': type_data,
        'stations': sorted(stations),
        'station_data': station_data,
        'station_type': station_type,
        'dayparts': _dayparts(hours),
        'months': months,
        'total_spots': len(spots),
        'total_duration': total_duration,
        'total_cost': sum(d['cost'] for d in type_data.values()),
        'total_rc': sum(d['rc_value'] for d in type_data.values()),
        'broadcast_days': len(aired_dates),
        'first_date': min(aired_dates),
        'last_date': max(aired_dates),
        'imputed_rc_rate': imputed_rc,
    }


# ARN daypart bands, in broadcast order.
DAYPARTS = [
    ('Breakfast',   6,  9),
    ('Morning',     9,  12),
    ('Afternoon',  12,  16),
    ('Drive',      16,  19),
    ('Evening',    19,  24),
    ('Overnight',   0,  6),
]


def _dayparts(hours: dict) -> dict:
    if not hours:
        return {}
    out = {}
    for name, start, end in DAYPARTS:
        n = sum(c for h, c in hours.items() if start <= h < end)
        if n:
            out[name] = n
    return out


def _empty_aggregate():
    return {
        'spots': [], 'type_data': {}, 'stations': [], 'station_data': {},
        'station_type': {}, 'dayparts': {}, 'months': {}, 'total_spots': 0,
        'total_duration': 0, 'total_cost': 0.0, 'total_rc': 0.0,
        'broadcast_days': 0, 'first_date': None, 'last_date': None,
        'imputed_rc_rate': 0.0,
    }


def fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
