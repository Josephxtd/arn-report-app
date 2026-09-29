"""
ARN Post-Campaign Report Generator
Generates a 3-page A4 PDF using ReportLab.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus.flowables import Flowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from datetime import date, datetime
import os

from pdf_parser import parse_arn_pdf, fmt_duration, DAYPARTS, BONUS_TYPES

# ── Colours ──────────────────────────────────────────────────────────────────
PURPLE       = colors.HexColor('#5B2C6F')
PURPLE2      = colors.HexColor('#8E44AD')
LIGHT_BG     = colors.HexColor('#F5EEF8')
BORDER_COL   = colors.HexColor('#D2B4DE')
WHITE        = colors.white
GREY_TEXT    = colors.HexColor('#666666')
ALT_ROW      = colors.HexColor('#FAF5FF')
TOTAL_ROW    = colors.HexColor('#EAD6F5')

W, H = A4
MARGIN = 18 * mm
CONTENT_W = W - 2 * MARGIN

# ── Styles ────────────────────────────────────────────────────────────────────
def _styles():
    base = dict(fontName='Helvetica', fontSize=9, leading=13, textColor=colors.black)
    s = lambda name, **kw: ParagraphStyle(name, **{**base, **kw})
    return {
        'title':      s('title',     fontName='Helvetica-Bold', fontSize=22, textColor=WHITE, leading=28),
        'subtitle':   s('subtitle',  fontName='Helvetica',      fontSize=12, textColor=colors.HexColor('#E8D5F5'), leading=16),
        'meta':       s('meta',      fontName='Helvetica',      fontSize=8.5, textColor=WHITE, leading=12),
        'section':    s('section',   fontName='Helvetica-Bold', fontSize=11, textColor=PURPLE, leading=16, spaceBefore=8),
        'body':       s('body',      fontSize=9, leading=14, spaceBefore=3),
        'body_bold':  s('body_bold', fontName='Helvetica-Bold', fontSize=9, leading=14),
        'footnote':   s('footnote',  fontSize=7.5, textColor=GREY_TEXT, leading=11),
        'kpi_label':  s('kpi_label', fontName='Helvetica-Bold', fontSize=7.5, textColor=GREY_TEXT, leading=10),
        'kpi_val':    s('kpi_val',   fontName='Helvetica-Bold', fontSize=15, textColor=PURPLE, leading=18),
        'kpi_sub':    s('kpi_sub',   fontName='Helvetica',      fontSize=7.5, textColor=PURPLE2, leading=10),
        'roi_text':   s('roi_text',  fontName='Helvetica-Bold', fontSize=10, textColor=PURPLE, leading=15),
        'footer':     s('footer',    fontSize=7.5, textColor=GREY_TEXT, leading=10, alignment=TA_CENTER),
        'radio_bold': s('radio_bold',fontName='Helvetica-Bold', fontSize=9, leading=14),
        'rec_num':    s('rec_num',   fontName='Helvetica-Bold', fontSize=9, textColor=PURPLE, leading=14),
        'summary':    s('summary',   fontSize=9, leading=14, spaceBefore=4),
    }


# ── Helper flowables ──────────────────────────────────────────────────────────
class HeaderBanner(Flowable):
    """Full-width purple header banner rendered as a Flowable."""
    def __init__(self, title, subtitle, meta_items, width, height=46*mm):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.meta_items = meta_items  # list of "Label: value" strings
        self._w = width
        self._h = height

    def wrap(self, *args):
        return self._w, self._h

    def _fit(self, text, font, size, max_w):
        """Shrink font until text fits max_w; return the size that fits."""
        while size > 6 and pdfmetrics.stringWidth(text, font, size) > max_w:
            size -= 0.25
        return size

    def draw(self):
        c = self.canv
        # Background
        c.setFillColor(PURPLE)
        c.rect(0, 0, self._w, self._h, fill=1, stroke=0)
        # Accent stripe
        c.setFillColor(PURPLE2)
        c.rect(0, 0, self._w, 4, fill=1, stroke=0)

        pad = 6 * mm
        avail = self._w - 2 * pad

        # Title
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 22)
        c.drawString(pad, self._h - 14*mm, self.title)

        # Subtitle — shrink to fit, leave room for the ARN lockup on the right
        sub_w = avail - 42*mm
        sub_size = self._fit(self.subtitle, 'Helvetica', 12, sub_w)
        c.setFont('Helvetica', sub_size)
        c.setFillColor(colors.HexColor('#E8D5F5'))
        c.drawString(pad, self._h - 22*mm, self.subtitle)

        # Meta — split across two lines so nothing is clipped
        mid = (len(self.meta_items) + 1) // 2
        line1 = '    |    '.join(self.meta_items[:mid])
        line2 = '    |    '.join(self.meta_items[mid:])
        c.setFillColor(WHITE)
        for i, line in enumerate([line1, line2]):
            if not line:
                continue
            size = self._fit(line, 'Helvetica', 8.5, avail)
            c.setFont('Helvetica', size)
            c.drawString(pad, self._h - (30 + i * 5.5)*mm, line)

        # ARN logo text top-right
        c.setFont('Helvetica-Bold', 14)
        c.setFillColor(WHITE)
        c.drawRightString(self._w - pad, self._h - 12*mm, 'ARN')
        c.setFont('Helvetica', 8)
        c.drawRightString(self._w - pad, self._h - 17*mm, 'Australian Radio Network')


class KPIRow(Flowable):
    """4 KPI cards in a row."""
    def __init__(self, cards, width, height=28*mm):
        super().__init__()
        self.cards = cards  # list of (label, value, sub)
        self._w = width
        self._h = height

    def wrap(self, *args):
        return self._w, self._h

    def draw(self):
        c = self.canv
        n = len(self.cards)
        card_w = self._w / n
        gap = 3 * mm

        for i, (label, value, sub) in enumerate(self.cards):
            x = i * card_w
            # Card background
            c.setFillColor(LIGHT_BG)
            c.setStrokeColor(BORDER_COL)
            c.roundRect(x + gap/2, 1*mm, card_w - gap, self._h - 2*mm, 3, fill=1, stroke=1)

            cx = x + card_w / 2
            # Label
            c.setFillColor(GREY_TEXT)
            c.setFont('Helvetica-Bold', 7.5)
            c.drawCentredString(cx, self._h - 8*mm, label.upper())
            # Value
            c.setFillColor(PURPLE)
            c.setFont('Helvetica-Bold', 15)
            c.drawCentredString(cx, self._h - 17*mm, value)
            # Sub-label
            c.setFillColor(PURPLE2)
            c.setFont('Helvetica', 7.5)
            c.drawCentredString(cx, self._h - 23*mm, sub)


class ROIBox(Flowable):
    """Purple-bordered highlight box: a headline ROI line plus a supporting line."""
    def __init__(self, text, width, height=18*mm, subtext=None):
        super().__init__()
        self.text = text
        self.subtext = subtext
        self._w = width
        self._h = height

    def wrap(self, *args):
        return self._w, self._h

    def _fit(self, text, font, size, max_w):
        while size > 6 and pdfmetrics.stringWidth(text, font, size) > max_w:
            size -= 0.25
        return size

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT_BG)
        c.setStrokeColor(PURPLE)
        c.setLineWidth(2)
        c.roundRect(0, 0, self._w, self._h, 4, fill=1, stroke=1)

        avail = self._w - 12 * mm
        cx = self._w / 2

        if self.subtext:
            size = self._fit(self.text, 'Helvetica-Bold', 10.5, avail)
            c.setFillColor(PURPLE)
            c.setFont('Helvetica-Bold', size)
            c.drawCentredString(cx, self._h / 2 + 2.0 * mm, self.text)

            sub_size = self._fit(self.subtext, 'Helvetica', 9, avail)
            c.setFillColor(PURPLE2)
            c.setFont('Helvetica', sub_size)
            c.drawCentredString(cx, self._h / 2 - 3.2 * mm, self.subtext)
        else:
            size = self._fit(self.text, 'Helvetica-Bold', 9.5, avail)
            c.setFillColor(PURPLE)
            c.setFont('Helvetica-Bold', size)
            c.drawCentredString(cx, self._h / 2 - 3, self.text)


def _esc(text: str) -> str:
    """
    Make model-written text safe for ReportLab's mini-HTML paragraph parser,
    then restore the handful of inline tags we actually want to honour.
    """
    if not text:
        return ''
    out = (text.replace('&', '&amp;')
               .replace('<', '&lt;')
               .replace('>', '&gt;'))
    # Allow <b>/<i> if the model used them; everything else stays literal.
    for tag in ('b', 'i'):
        out = out.replace(f'&lt;{tag}&gt;', f'<{tag}>').replace(f'&lt;/{tag}&gt;', f'</{tag}>')
    return out


def _table_style(has_total=True):
    base = [
        ('BACKGROUND',  (0, 0), (-1, 0),  PURPLE),
        ('TEXTCOLOR',   (0, 0), (-1, 0),  WHITE),
        ('FONTNAME',    (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, -1), 8.5),
        ('FONTNAME',    (0, 1), (-1, -1), 'Helvetica'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2 if has_total else -1), [WHITE, ALT_ROW]),
        ('ALIGN',       (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN',       (0, 1), (0, -1),  'LEFT'),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',        (0, 0), (-1, -1), 0.3, BORDER_COL),
        ('TOPPADDING',  (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0,0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',(0, 0), (-1, -1), 5),
    ]
    if has_total:
        base += [
            ('BACKGROUND',  (0, -1), (-1, -1), TOTAL_ROW),
            ('FONTNAME',    (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]
    return TableStyle(base)


# ── Main generator ────────────────────────────────────────────────────────────
def generate_report(pdf_path: str, output_path: str,
                    context: dict = None, use_ai: bool = True) -> dict:
    """
    Build the 3-page report entirely from the uploaded ARN Post Times PDF.
    Returns the metadata that was detected, so the caller can name the file
    and report back what it found.

    Tables and figures always come from the PDF. The prose is written per client
    by Claude when credentials are available; otherwise the built-in template
    copy is used and the report still generates.
    """
    data = parse_arn_pdf(pdf_path)
    meta = data.get('meta', {})

    if not data['total_spots']:
        raise ValueError(
            'No aired spots found in this PDF. Check that it is an ARN Post Times '
            'report containing aired dates in dd/mm/yyyy format.'
        )

    client     = meta.get('client_name') or 'Client'
    package    = meta.get('package_name') or ''
    contract   = meta.get('contract_number') or 'N/A'
    st_list    = data['stations']
    stations_f = ' / '.join(st_list) if st_list else 'ARN'
    dual       = len(st_list) > 1

    today_str  = date.today().strftime('%d %B %Y')

    # ── Derived metrics — computed once, used by both the prose and the tables ──
    # BONUS_TYPES comes from the parser so the classification can't drift apart.
    dur_fmt     = fmt_duration(data['total_duration'])
    rc_total    = sum(d['rc_value'] for d in data['type_data'].values())
    invest_ex   = data['total_cost']
    invest_gst  = invest_ex * 1.1
    bonus_val   = sum(d['rc_value'] for t, d in data['type_data'].items() if t in BONUS_TYPES)
    bonus_spots = sum(d['count']    for t, d in data['type_data'].items() if t in BONUS_TYPES)
    paid_spots  = data['total_spots'] - bonus_spots
    roi_mult    = rc_total / invest_ex if invest_ex else 0
    saving      = rc_total - invest_ex
    eff_cps     = invest_ex / data['total_spots'] if data['total_spots'] else 0
    rc_cps      = rc_total  / data['total_spots'] if data['total_spots'] else 0
    disc_pct    = (1 - invest_ex / rc_total) * 100 if rc_total else 0
    cost_per_day = invest_ex / data['broadcast_days'] if data['broadcast_days'] else 0

    # Bonus uplift — the spots carried free, expressed against what was paid for.
    paid_rate = invest_ex / paid_spots if paid_spots else 0
    rc_rate   = data.get('imputed_rc_rate') or rc_cps
    bonus_pct = (bonus_spots / paid_spots * 100) if paid_spots else 0
    if bonus_spots and paid_spots:
        per_paid = paid_spots / bonus_spots
        bonus_ratio_txt = f"1 free spot for every {per_paid:.1f} paid"
    else:
        bonus_ratio_txt = 'no bonus spots in this campaign'

    months_set = sorted(data['months'].keys(), key=lambda m: datetime.strptime(m, '%B %Y'))
    period = f"{months_set[0]} – {months_set[-1]}" if len(months_set) > 1 \
        else (months_set[0] if months_set else 'the campaign period')
    first_date = data['first_date']
    last_date  = data['last_date']
    span_days  = (last_date - first_date).days + 1

    # ── Per-client prose from Claude (falls back to template copy) ─────────────
    ai = None
    if use_ai:
        try:
            from narrative import generate_narrative
            ai = generate_narrative({
                'client': client, 'package': package, 'stations': stations_f,
                'contract': contract, 'period': period,
                'first_date': first_date.strftime('%d %B %Y'),
                'last_date': last_date.strftime('%d %B %Y'),
                'total_spots': data['total_spots'],
                'broadcast_days': data['broadcast_days'],
                'airtime': dur_fmt,
                'invest_ex': invest_ex, 'rc_total': rc_total,
                'bonus_val': bonus_val, 'roi': roi_mult,
                'eff_cps': eff_cps, 'rc_cps': rc_cps,
                'paid_spots': paid_spots, 'bonus_spots': bonus_spots,
                'bonus_pct': bonus_pct, 'paid_rate': paid_rate, 'rc_rate': rc_rate,
                'disc_pct': disc_pct,
                'type_data': data['type_data'],
                'station_split': data['station_data'] if dual else {},
                'dayparts': data['dayparts'],
                'months': {m: sum(v['count'] for v in data['months'][m].values())
                           for m in months_set},
                'context': {k: v for k, v in (context or {}).items() if v},
            })
        except Exception:
            # Never let the prose layer block a report.
            ai = None
    nar = ai['narrative'] if ai else None

    styles = _styles()
    story  = []

    # ── PAGE 1 ────────────────────────────────────────────────────────────────
    # Header banner
    subtitle_text = '  |  '.join(x for x in [client, package, stations_f] if x)
    meta_items = [
        f"Contract: {contract}",
        f"Station(s): {stations_f}",
        f"Spots Aired To Date: {data['total_spots']:,}",
        f"Report Date: {today_str}",
    ]
    story.append(HeaderBanner(
        title='Post-Campaign Report',
        subtitle=subtitle_text,
        meta_items=meta_items,
        width=CONTENT_W,
    ))
    story.append(Spacer(1, 5*mm))

    # Campaign Overview
    story.append(Paragraph('Campaign Overview', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    pkg_phrase = f" under the <b>{package}</b> package" if package else ""

    if nar:
        para1, para2 = _esc(nar.overview_para1), _esc(nar.overview_para2)
    else:
        para1 = (
            f"{client} partnered with ARN's {stations_f}{pkg_phrase}, "
            f"delivering a sustained radio campaign across {period}. "
            f"The schedule ran from {first_date.strftime('%d %B %Y')} to "
            f"{last_date.strftime('%d %B %Y')} — a campaign window of {span_days} days — "
            f"leveraging the engaged local listenership of {stations_f} to build "
            f"brand awareness and drive consistent presence in market."
        )
        para2 = (
            f"Over the campaign period, ARN delivered a total of <b>{data['total_spots']:,} spots</b> "
            f"across <b>{data['broadcast_days']} broadcast days</b>, "
            f"accumulating <b>{dur_fmt}</b> of total airtime. "
            f"Spot types included {', '.join(data['type_data'].keys()) or 'various formats'}, "
            f"strategically scheduled across key dayparts to maximise reach and frequency. "
            f"The campaign achieved strong delivery consistency and significant added value "
            f"through bonus and filler placements."
        )
    story.append(Paragraph(para1, styles['body']))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(para2, styles['body']))
    story.append(Spacer(1, 5*mm))

    # KPI Cards
    story.append(Paragraph('Campaign Snapshot', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    kpi_cards = [
        ('Spots Aired',     f"{data['total_spots']:,}",         'confirmed aired spots'),
        ('Total Airtime',   dur_fmt,                             'across all spot types'),
        ('Broadcast Days',  str(data['broadcast_days']),         'days on air'),
        ('Total RC Value',  f"${rc_total:,.2f}",                 'at ratecard ex GST'),
    ]
    story.append(KPIRow(kpi_cards, CONTENT_W, height=28*mm))
    story.append(Spacer(1, 5*mm))

    # Spot Delivery Breakdown table
    story.append(Paragraph('Spot Delivery Breakdown', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    # With more than one station, each spot type also shows its per-station split.
    stn_cols = st_list if dual else []
    header_row = ['Spot Type', 'Count', '%'] + [f'{s} (%)' for s in stn_cols] + \
                 ['Airtime', 'Rate', 'Cost', 'RC Value *']
    tbl_data = [header_row]

    for st, d in data['type_data'].items():
        pct = f"{d['count']/data['total_spots']*100:.1f}%" if data['total_spots'] else '0%'
        row = [st, str(d['count']), pct]
        for stn in stn_cols:
            n = data['station_type'].get(stn, {}).get(st, {}).get('count', 0)
            # Share of THIS spot type carried by this station
            share = n / d['count'] * 100 if d['count'] else 0
            row.append(f"{n} ({share:.0f}%)")
        row += [
            fmt_duration(d['duration']),
            f"${d['rate']:.2f}" if d['rate'] else '$0.00',
            f"${d['cost']:.2f}",
            f"${d['rc_value']:.2f}",
        ]
        tbl_data.append(row)

    # Total row
    total_row = ['TOTAL', str(data['total_spots']), '100%']
    for stn in stn_cols:
        n = data['station_data'].get(stn, {}).get('count', 0)
        share = n / data['total_spots'] * 100 if data['total_spots'] else 0
        total_row.append(f"{n} ({share:.0f}%)")
    total_row += [
        fmt_duration(data['total_duration']),
        '',
        f"${data['total_cost']:.2f}",
        f"${rc_total:.2f}",
    ]
    tbl_data.append(total_row)

    if dual:
        # Narrower fixed columns for the numeric tail, station columns share the rest.
        fixed = [0.155, 0.070, 0.070, 0.105, 0.115, 0.105, 0.110]
        stn_share = (1.0 - sum(fixed)) / len(stn_cols)
        col_w = [CONTENT_W * p for p in fixed[:3]] \
              + [CONTENT_W * stn_share] * len(stn_cols) \
              + [CONTENT_W * p for p in fixed[3:]]
    else:
        col_w = [CONTENT_W * p for p in [0.18, 0.08, 0.08, 0.14, 0.17, 0.13, 0.14]]
    t = Table(tbl_data, colWidths=col_w, repeatRows=1)
    t.setStyle(_table_style(has_total=True))
    story.append(t)
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        '* RC Value = Ratecard equivalent value of all spots, including bonus and filler placements '
        'carried at $0.00 cost, valued at published ratecard rates. All rates and costs shown ex GST.',
        styles['footnote']
    ))

    # Footer page 1

    # Daypart Distribution — only when the log carried aired times
    if data['dayparts']:
        story.append(Paragraph('Daypart Distribution', styles['section']))
        story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

        # Laid out horizontally — dayparts as columns — to stay inside 3 pages.
        active = [(n, data['dayparts'][n]) for n, _, _ in DAYPARTS if data['dayparts'].get(n)]
        dp_total = sum(c for _, c in active)
        dp_rows = [
            [''] + [n for n, _ in active],
            ['Spots'] + [str(c) for _, c in active],
            ['% of Schedule'] + [f"{c / dp_total * 100:.1f}%" for _, c in active],
        ]
        first_w = CONTENT_W * 0.22
        rest_w = (CONTENT_W - first_w) / len(active)
        t5 = Table(dp_rows, colWidths=[first_w] + [rest_w] * len(active))
        t5.setStyle(TableStyle([
            ('BACKGROUND',   (0, 0), (-1, 0),  PURPLE),
            ('TEXTCOLOR',    (0, 0), (-1, 0),  WHITE),
            ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
            ('BACKGROUND',   (0, 1), (0, -1),  LIGHT_BG),
            ('FONTNAME',     (0, 1), (0, -1),  'Helvetica-Bold'),
            ('TEXTCOLOR',    (0, 1), (0, -1),  PURPLE),
            ('FONTSIZE',     (0, 0), (-1, -1), 8.5),
            ('ALIGN',        (1, 0), (-1, -1), 'CENTER'),
            ('ALIGN',        (0, 0), (0, -1),  'LEFT'),
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID',         (0, 0), (-1, -1), 0.3, BORDER_COL),
            ('TOPPADDING',   (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
        ]))
        story.append(t5)
        story.append(Spacer(1, 4*mm))
    story.append(Spacer(1, 4*mm))
    _add_footer(story, styles, client)

    # ── PAGE 2 ────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    _add_page_header(story, styles, 'Post-Campaign Report — Page 2', client)

    # Station Breakdown (dual only)
    if dual and data['stations']:
        story.append(Paragraph('Station Breakdown', styles['section']))
        story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

        per_station = {}
        for s in data['spots']:
            stn = s.get('station', 'Unknown')
            if stn not in per_station:
                per_station[stn] = {'count': 0, 'duration': 0, 'cost': 0.0, 'rc': 0.0}
            per_station[stn]['count']    += 1
            per_station[stn]['duration'] += s['duration']
            per_station[stn]['cost']     += s['rate']
            per_station[stn]['rc']       += s.get('rc_rate', s['rate'])

        sb_data = [['Station', 'Spots', 'Airtime', 'Investment (ex GST)', 'RC Value']]
        for stn, d in per_station.items():
            sb_data.append([stn, str(d['count']), fmt_duration(d['duration']),
                            f"${d['cost']:.2f}", f"${d['rc']:.2f}"])
        sb_data.append(['TOTAL', str(data['total_spots']), fmt_duration(data['total_duration']),
                        f"${data['total_cost']:.2f}", f"${rc_total:.2f}"])

        col_w2 = [CONTENT_W * p for p in [0.25, 0.12, 0.18, 0.25, 0.20]]
        t2 = Table(sb_data, colWidths=col_w2, repeatRows=1)
        t2.setStyle(_table_style(has_total=True))
        story.append(t2)
        story.append(Spacer(1, 5*mm))

    # Monthly Spot Delivery
    story.append(Paragraph('Monthly Spot Delivery', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    type_keys = list(data['type_data'].keys())
    month_hdr = ['Month'] + type_keys + ['Total Spots', 'Airtime', 'Investment']
    monthly_rows = [month_hdr]
    partial_months = []

    all_months_sorted = sorted(
        data['months'].keys(),
        key=lambda x: datetime.strptime(x, '%B %Y')
    )
    today_month = date.today().strftime('%B %Y')

    for month in all_months_sorted:
        mdata = data['months'][month]
        if month == today_month:
            partial_months.append(month)
        row = [month]
        total_month_spots = 0
        total_month_dur = 0
        total_month_cost = 0.0
        for tk in type_keys:
            td = mdata.get(tk, {})
            cnt = td.get('count', 0)
            row.append(str(cnt))
            total_month_spots += cnt
            total_month_dur   += td.get('duration', 0)
            total_month_cost  += td.get('cost', 0.0)
        row += [str(total_month_spots), fmt_duration(total_month_dur), f"${total_month_cost:.2f}"]
        monthly_rows.append(row)

    # Total row
    tot_row = ['TOTAL']
    for tk in type_keys:
        tot_row.append(str(data['type_data'][tk]['count']))
    tot_row += [str(data['total_spots']), fmt_duration(data['total_duration']), f"${data['total_cost']:.2f}"]
    monthly_rows.append(tot_row)

    n_cols = len(month_hdr)
    col_w3 = [CONTENT_W / n_cols] * n_cols
    col_w3[0] = CONTENT_W * 0.20
    remaining = CONTENT_W - col_w3[0]
    for i in range(1, n_cols):
        col_w3[i] = remaining / (n_cols - 1)

    t3 = Table(monthly_rows, colWidths=col_w3, repeatRows=1)
    t3.setStyle(_table_style(has_total=True))
    # A 12-month campaign makes this the tallest table in the report; tighten it
    # so page 2 still holds the investment analysis and the ROI callout.
    if len(monthly_rows) > 8:
        t3.setStyle(TableStyle([
            ('FONTSIZE',     (0, 0), (-1, -1), 7.5),
            ('TOPPADDING',   (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 1.5),
        ]))
    story.append(t3)
    if partial_months:
        story.append(Spacer(1, 2*mm))
        story.append(Paragraph(
            f'† Partial month(s): {", ".join(partial_months)} — data reflects spots aired to report date only.',
            styles['footnote']
        ))
    story.append(Spacer(1, 5*mm))

    # Investment vs Value Analysis
    story.append(Paragraph('Investment vs. Value Analysis', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    inv_data = [
        ['Line Item', 'Detail', 'Amount'],
        # What they actually paid for — the paid rotation only.
        ['Client Investment (ex GST)',
         f"{paid_spots:,} paid spots @ ${paid_rate:,.2f}",       f"${invest_ex:,.2f}"],
        ['Client Investment (inc GST)',
         'ex GST × 1.1',                                        f"${invest_gst:,.2f}"],
        ['Paid Spots Value (at ratecard)',
         f"{paid_spots:,} spots @ ${rc_rate:,.2f} ratecard",     f"${rc_total - bonus_val:,.2f}"],
        ['Bonus Spots Delivered (at $0.00)',
         f"{bonus_spots:,} spots @ ${rc_rate:,.2f} ratecard",    f"${bonus_val:,.2f}"],
        ['Bonus Uplift',
         f"{bonus_ratio_txt} — no additional charge",           f"+{bonus_pct:.1f}% spots"],
        ['Total Ratecard Value',
         f"{data['total_spots']:,} spots at published ratecard", f"${rc_total:,.2f}"],
        ['Value Above Investment',
         f"{disc_pct:.0f}% below ratecard",                     f"${saving:,.2f}"],
        ['Effective Cost Per Spot',
         f"vs. ${rc_cps:,.2f} at ratecard",                     f"${eff_cps:,.2f}"],
        ['Cost Per Broadcast Day',
         f"across {data['broadcast_days']} days on air",        f"${cost_per_day:,.2f}"],
        ['ROI Multiple',
         'ratecard value ÷ investment',                         f"{roi_mult:.2f}x"],
    ]
    col_w4 = [CONTENT_W * 0.38, CONTENT_W * 0.37, CONTENT_W * 0.25]
    t4 = Table(inv_data, colWidths=col_w4, repeatRows=1)
    t4.setStyle(_table_style(has_total=True))
    story.append(t4)
    story.append(Spacer(1, 4*mm))


    # ROI callout box — headline multiple, with the bonus uplift underneath
    roi_text = (
        f"For every $1.00 invested, {client} received ${roi_mult:.2f} "
        f"worth of airtime at ratecard value."
    )
    roi_sub = (
        f"{paid_spots:,} spots paid for · {bonus_spots:,} delivered free · "
        f"{data['total_spots']:,} total on air — {bonus_pct:.0f}% more airtime at no extra cost."
        if bonus_spots else None
    )
    story.append(ROIBox(roi_text, CONTENT_W, height=19*mm, subtext=roi_sub))
    story.append(Spacer(1, 4*mm))
    _add_footer(story, styles, client)

    # ── PAGE 3 ────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    _add_page_header(story, styles, 'Post-Campaign Report — Page 3', client)

    # The Value of Radio Advertising
    story.append(Paragraph('The Value of Radio Advertising', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    if nar:
        radio_points = [
            Paragraph(f'<b>{_esc(b.heading)}</b> {_esc(b.body)}', styles['body'])
            for b in nar.radio_benefits
        ]
    else:
        radio_points = _radio_value_paragraphs(client, data, dur_fmt, stations_f, styles)
    for p in radio_points:
        story.append(p)
        story.append(Spacer(1, 3*mm))

    story.append(Spacer(1, 4*mm))

    # Summary & Recommendations
    story.append(Paragraph('Summary & Recommendations', styles['section']))
    story.append(HRFlowable(width=CONTENT_W, thickness=1, color=BORDER_COL, spaceAfter=4))

    if nar:
        summary_text = _esc(nar.summary)
    else:
        summary_text = (
            f"{client}'s campaign with ARN's {stations_f} delivered outstanding results across {period}. "
            f"A total of <b>{data['total_spots']:,} spots</b> aired over <b>{data['broadcast_days']} broadcast days</b>, "
            f"accumulating <b>{dur_fmt}</b> of premium airtime. "
            f"The campaign's total ratecard value reached <b>${rc_total:,.2f}</b> against a client investment "
            f"of <b>${invest_ex:,.2f}</b> (ex GST), delivering an ROI multiple of <b>{roi_mult:.2f}x</b>. "
            f"Bonus and filler placements added <b>${bonus_val:,.2f}</b> in incremental value at no additional cost."
        )
    story.append(Paragraph(summary_text, styles['summary']))
    story.append(Spacer(1, 4*mm))

    if nar:
        recs = [f'<b>{_esc(r.heading)}</b> {_esc(r.body)}' for r in nar.recommendations]
    else:
        recs = _recommendations(client, data, dual, stations_f)
    for i, rec in enumerate(recs, 1):
        story.append(Paragraph(f"{i}. {rec}", styles['body']))
        story.append(Spacer(1, 2*mm))

    story.append(Spacer(1, 6*mm))
    _add_footer(story, styles, client)

    # ── Build PDF ─────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )
    doc.build(story)

    return {
        'client_name': client,
        'package_name': package,
        'contract_number': contract,
        'stations': stations_f,
        'total_spots': data['total_spots'],
        'ai_narrative': bool(nar),
        'ai_cost': ai['cost'] if ai else 0.0,
    }


def _add_page_header(story, styles, title, client):
    hdr = Table(
        [[Paragraph(f'<b>{title}</b>', ParagraphStyle('ph', fontName='Helvetica-Bold', fontSize=9, textColor=PURPLE)),
          Paragraph(client, ParagraphStyle('ph2', fontName='Helvetica', fontSize=9, textColor=GREY_TEXT, alignment=TA_RIGHT))]],
        colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4]
    )
    hdr.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (0, -1), 5),
        ('RIGHTPADDING', (-1, 0), (-1, -1), 5),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 4*mm))


def _add_footer(story, styles, client):
    from reportlab.platypus import Spacer
    story.append(Spacer(1, 2*mm))
    story.append(HRFlowable(width=CONTENT_W, thickness=0.5, color=BORDER_COL))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        f'ARN — Australian Radio Network  |  {client}  |  Confidential  |  '
        f'{date.today().strftime("%d %b %Y")}',
        styles['footer']
    ))


def _radio_value_paragraphs(client, data, dur_fmt, stations_f, styles):
    spots_per_day = data['total_spots'] / data['broadcast_days'] if data['broadcast_days'] else 0
    footprint = "the stations' own broadcast footprint" if len(data['stations']) > 1 \
        else "the station's own broadcast footprint"
    paras = []
    items = [
        (
            'Unmatched Reach & Frequency.',
            f'Radio remains Australia\'s most trusted broadcast medium, reaching over 17 million Australians weekly. '
            f'{client}\'s campaign capitalised on this reach by securing {data["total_spots"]:,} spots across '
            f'{data["broadcast_days"]} broadcast days — an average of {spots_per_day:.1f} spots per day on air, '
            f'the repetition that builds durable brand recall.'
        ),
        (
            'Targeted Local Engagement.',
            f'Local radio connects businesses directly with their community. By advertising on '
            f'{stations_f}, {client} spoke directly to local listeners in {footprint} — an audience '
            f'defined by geography rather than clicks, delivering relevance no digital feed can replicate.'
        ),
        (
            'Credibility Through Association.',
            f'Radio listeners trust the stations they tune into daily. When {client}\'s message appears alongside '
            f'trusted presenters and content, that credibility transfers to the brand — accelerating trust-building '
            f'that would otherwise take years through other channels.'
        ),
        (
            'Exceptional Value Delivery.',
            f'This campaign generated {dur_fmt} of total airtime at a ratecard value of '
            f'${sum(d["rc_value"] for d in data["type_data"].values()):,.2f}, '
            f'significantly exceeding the client\'s investment through bonus and value-added placements. '
            f'Radio consistently delivers one of the highest ROI multiples in traditional media.'
        ),
        (
            'The Power of Audio in a Screen-Saturated World.',
            f'As consumers experience digital fatigue, audio advertising cuts through by engaging listeners '
            f'while they drive, work, and go about their day — moments when screens are unavailable. '
            f'{client}\'s message reached audiences in these exclusive listening windows, '
            f'creating brand impressions that visual media cannot access.'
        ),
    ]
    for bold, body in items:
        p = Paragraph(f'<b>{bold}</b> {body}', styles['body'])
        paras.append(p)
    return paras


def _recommendations(client, data, dual, stations):
    lr = data['type_data'].get('Live Read', {}).get('count', 0)
    lr_pct = lr / data['total_spots'] * 100 if data['total_spots'] else 0
    recs = [
        (
            f'<b>Extend Campaign Duration.</b> The campaign demonstrated strong delivery consistency across '
            f'{data["broadcast_days"]} broadcast days. Renewing for a further 12-month cycle would allow '
            f'{client} to build on existing brand recall and deepen frequency among loyal listeners.'
        ),
        (
            f'<b>Increase Live Read Integration.</b> Live reads delivered by trusted presenters generate '
            f'significantly higher engagement than pre-recorded spots. '
            + (
                f'Live reads made up {lr_pct:.0f}% of this campaign ({lr} spots); lifting that share '
                f'in the next package would strengthen authenticity and response rates.'
                if lr else
                'Introducing a live read component in the next package would add a trusted, '
                'personally-delivered endorsement to the schedule.'
            )
        ),
        (
            f'<b>Optimise Dual-Station Split.</b> With {stations} both delivering strong numbers, '
            f'a refined spot weighting strategy between stations — informed by listener data — '
            f'could maximise cost efficiency and peak-time presence.'
            if dual else
            '<b>Expand to Additional Stations.</b> Extending the campaign to a second ARN station '
            'would broaden geographic and demographic reach, capturing new audience segments across the region.'
        ),
        (
            f'<b>Leverage Seasonal Peaks.</b> Scheduling increased spot weighting around {client}\'s own '
            f'peak trading periods — rather than spreading evenly across the year — would align broadcast '
            f'frequency with consumer intent and drive measurable uplift in response.'
        ),
    ]
    return recs
