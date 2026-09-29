"""
Client-facing narrative for the Post-Campaign Report, written by Claude.

The report's tables come straight from the Post Times PDF. The prose — campaign
overview, value-of-radio section, summary and recommendations — is written per
client by Claude, from the campaign's real numbers plus (optionally) a web
search about the business.

If no API key is configured, or the call fails, callers fall back to the
deterministic template copy in report_generator.py. A report always generates.
"""
import os
import logging
from typing import List, Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MODEL = 'claude-opus-5'

# Opus 5 pricing, $ per token, for the cost estimate returned to the caller.
PRICE_IN = 5.00 / 1_000_000
PRICE_OUT = 25.00 / 1_000_000


# ── Output schema ─────────────────────────────────────────────────────────────
class Benefit(BaseModel):
    heading: str = Field(description="Bold lead-in, 2-5 words, ending in a full stop.")
    body: str = Field(description="35-50 words, 2 sentences. Reference a real campaign number where it fits.")


class Recommendation(BaseModel):
    heading: str = Field(description="Bold lead-in, 2-5 words, ending in a full stop.")
    body: str = Field(description="30-45 words, 2 sentences, specific and actionable for this client.")


class Narrative(BaseModel):
    overview_para1: str = Field(
        description="Sets the scene: who the client is, what they do, the partnership, "
                    "the package, the period, the audience and region. 55-75 words, 3 sentences."
    )
    overview_para2: str = Field(
        description="Delivery mechanics in plain language: total spots, airtime, broadcast "
                    "days, paid vs bonus spots, consistency. 55-75 words, 3 sentences. Do not "
                    "list monthly or daypart counts - the tables show those."
    )
    radio_benefits: List[Benefit] = Field(
        description="Exactly 5 benefits of radio advertising, tailored to this client's "
                    "industry and local market."
    )
    summary: str = Field(
        description="One paragraph, 60-80 words, pulling together the key metrics: spots, "
                    "broadcast days, investment, bonus spots and their value, ratecard value, ROI."
    )
    recommendations: List[Recommendation] = Field(
        description="Exactly 4 forward-looking recommendations tailored to this client."
    )


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM = """You are a senior account director at ARN (Australian Radio Network), \
writing the narrative sections of a post-campaign report that will be sent directly \
to the client.

Voice and standards:
- Write TO the client about their own campaign. Professional, warm, confident — \
never salesy, never padded with filler adjectives.
- Australian English and Australian conventions throughout (organise, realise, \
programme for broadcast schedules, $ for AUD).
- Use the client's actual business name naturally. Do not invent a tagline or slogan.
- Ground claims in the campaign numbers you are given. Quote real figures rather than \
vague intensifiers — "120 spots across 72 broadcast days" beats "extensive coverage".
- The bonus spots are the strongest part of the value story: the client paid for a set \
number of spots and received meaningfully more on air at no additional charge. Make that \
concrete at least once — the paid count, the free count, and what the free spots are worth \
at ratecard — rather than calling it "added value" in the abstract.
- Radio industry facts you may state as general context: radio reaches around 17 million \
Australians weekly; audio reaches listeners in car, at work and at home; local radio \
carries high trust in regional markets. Do not invent specific ratings, share figures, \
or audience numbers for these stations — you have not been given them.

Hard rules:
- Respect the word limits on every field. The report is a fixed 3-page layout and \
overlong text breaks it. Tables already show monthly, daypart and per-station counts — \
do not repeat those lists in prose.
- Avoid internal trade codes in client-facing prose: say "paid spots" rather than \
"BMAD", and "bonus spots" rather than "Bonus BTA".
- Do not describe the stations' formats, demographics, or audience profiles (e.g. \
"AM heartland", "younger audience") — you have not been given that information.
- NEVER invent facts about the client's business. If the research section below is \
empty or uncertain, write about the campaign and the industry in general terms rather \
than guessing at specifics like founding year, staff numbers, locations, or awards.
- Do not promise results or make performance guarantees.
- Do not mention this report was AI-generated."""


RESEARCH_SYSTEM = """You research Australian businesses to give an advertising account \
team accurate context. Be precise and conservative: report only what you can verify, \
and say plainly when you cannot find a business. Never speculate or fill gaps with \
plausible-sounding detail."""


CONTEXT_LABELS = {
    'industry': 'Industry / business type',
    'region': 'Local market / region',
    'target_audience': 'Target audience',
    'notes': 'Additional notes from the account manager',
}


def _context_block(ctx: dict) -> str:
    """Render the account manager's notes. These are authoritative — they come
    from the person who sold and ran the campaign."""
    if not ctx:
        return (
            "CONTEXT FROM THE ACCOUNT MANAGER:\n"
            "(none supplied — keep industry references general and do not guess "
            "at what this business does beyond what the research below supports)"
        )
    lines = ["CONTEXT FROM THE ACCOUNT MANAGER (authoritative — prefer this over "
             "anything from web research if they conflict):"]
    for key, label in CONTEXT_LABELS.items():
        if ctx.get(key):
            lines.append(f"- {label}: {ctx[key]}")
    return '\n'.join(lines)


def _research(client, client_name: str, stations: str, timeout: float) -> str:
    """
    Look up the client business so the narrative can speak to what they actually do.
    Returns a short factual brief, or '' when nothing reliable was found.
    """
    prompt = (
        f"Research this Australian business, which advertises on ARN radio "
        f"({stations}): \"{client_name}\".\n\n"
        "Report only what you can verify from search results:\n"
        "- What the business does (industry, products/services)\n"
        "- Where it operates (town/region/state)\n"
        "- Who its customers are\n"
        "- Anything notable about its market position or competitive landscape\n\n"
        "If you cannot confidently identify this specific business, say exactly: "
        "NOT FOUND — followed by what the business name alone suggests about the "
        "industry, clearly labelled as inference. Never present a guess as fact."
    )
    resp = client.with_options(timeout=timeout).messages.create(
        model=MODEL,
        max_tokens=2000,
        system=RESEARCH_SYSTEM,
        output_config={'effort': 'low'},
        tools=[{
            'type': 'web_search_20260209',
            'name': 'web_search',
            'max_uses': 2,
            'user_location': {'type': 'approximate', 'country': 'AU'},
        }],
        messages=[{'role': 'user', 'content': prompt}],
    )
    text = '\n'.join(b.text for b in resp.content if b.type == 'text').strip()
    usage = (resp.usage.input_tokens, resp.usage.output_tokens)
    return text, usage


def _facts_block(f: dict) -> str:
    """Render the campaign facts as a compact, unambiguous block for the prompt."""
    lines = [
        f"Client: {f['client']}",
        f"Package: {f['package'] or '(not stated in the log)'}",
        f"Station(s): {f['stations']}",
        f"Contract: {f['contract']}",
        f"Campaign period: {f['period']}",
        f"First spot aired: {f['first_date']}",
        f"Last spot aired: {f['last_date']}",
        f"Total spots aired: {f['total_spots']}",
        f"Broadcast days: {f['broadcast_days']}",
        f"Total airtime: {f['airtime']}",
        f"Paid spots: {f.get('paid_spots', 0)} @ ${f.get('paid_rate', 0):,.2f} each",
        f"Bonus spots delivered free: {f.get('bonus_spots', 0)} "
        f"(+{f.get('bonus_pct', 0):.1f}% more airtime than was paid for)",
        f"Client investment (ex GST): ${f['invest_ex']:,.2f}",
        f"Total ratecard value: ${f['rc_total']:,.2f} "
        f"(published ratecard ${f.get('rc_rate', 0):,.2f} per spot)",
        f"Value of the bonus spots at ratecard: ${f['bonus_val']:,.2f}",
        f"Effective cost per spot paid: ${f['eff_cps']:,.2f} vs ${f['rc_cps']:,.2f} ratecard "
        f"({f.get('disc_pct', 0):.0f}% below ratecard)",
        f"ROI multiple: {f['roi']:.2f}x",
        "",
        "Spot types delivered:",
    ]
    for name, d in f['type_data'].items():
        pct = d['count'] / f['total_spots'] * 100 if f['total_spots'] else 0
        lines.append(f"  - {name}: {d['count']} spots ({pct:.1f}%), "
                     f"rate ${d['rate']:.2f}, ratecard value ${d['rc_value']:,.2f}")
    if f['station_split']:
        lines.append("")
        lines.append("Per-station split:")
        for stn, d in f['station_split'].items():
            pct = d['count'] / f['total_spots'] * 100 if f['total_spots'] else 0
            lines.append(f"  - {stn}: {d['count']} spots ({pct:.0f}%), "
                         f"investment ${d['cost']:,.2f}")
    if f['dayparts']:
        lines.append("")
        lines.append("Daypart distribution: " + ', '.join(
            f"{k} {v}" for k, v in f['dayparts'].items()))
    if f['months']:
        lines.append("")
        lines.append("Monthly delivery: " + ', '.join(
            f"{m} {n}" for m, n in f['months'].items()))
        last_month = list(f['months'])[-1]
        from datetime import date
        if last_month == date.today().strftime('%B %Y'):
            lines.append(
                f"NOTE: {last_month} is a partial month — this report only counts spots "
                f"aired up to today. Its lower count is not a delivery shortfall or gap; "
                f"do not describe it as one."
            )
    return '\n'.join(lines)


def generate_narrative(
    facts: dict,
    research: bool = True,
    timeout: float = 120.0,
) -> Optional[dict]:
    """
    Write the report's narrative sections for this client.

    Returns {'narrative': Narrative, 'research': str, 'cost': float} on success,
    or None when no API key is configured or the call fails — callers then use
    the built-in template copy.
    """
    if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
        log.info('No Anthropic credentials set; using template narrative.')
        return None

    try:
        import anthropic
    except ImportError:
        log.warning('anthropic SDK not installed; using template narrative.')
        return None

    client = anthropic.Anthropic()
    tok_in = tok_out = 0
    brief = ''

    # Step 1 — research the business (best-effort; a failure here is not fatal)
    # The account manager's own description beats a web search, and the search is
    # most of the cost — only research when we don't already know the business.
    if research and not (facts.get('context') or {}).get('industry'):
        try:
            brief, (ti, to) = _research(client, facts['client'], facts['stations'], timeout)
            tok_in += ti
            tok_out += to
        except Exception as e:
            log.warning('Client research failed (%s); writing from campaign data only.', e)
            brief = ''

    research_section = (
        f"RESEARCH ON THIS CLIENT:\n{brief}"
        if brief else
        "RESEARCH ON THIS CLIENT:\n(none available — write about the campaign and the "
        "industry in general terms; do not invent specifics about this business)"
    )

    prompt = (
        f"Write the narrative sections of the post-campaign report for this client.\n\n"
        f"CAMPAIGN DATA (every figure here is verified from the station's post log — "
        f"use these numbers, do not alter them):\n{_facts_block(facts)}\n\n"
        f"{_context_block(facts.get('context') or {})}\n\n"
        f"{research_section}\n\n"
        f"Write the overview, five radio-value benefits, the summary, and four "
        f"recommendations. Tailor everything to this client's industry and local market. "
        f"Where a benefit or recommendation can cite a real campaign number from above, "
        f"cite it."
    )

    try:
        resp = client.with_options(timeout=timeout).messages.parse(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            output_config={'effort': 'medium'},
            messages=[{'role': 'user', 'content': prompt}],
            output_format=Narrative,
        )
    except Exception as e:
        log.warning('Narrative generation failed (%s); using template narrative.', e)
        return None

    if resp.stop_reason == 'refusal':
        log.warning('Narrative request was declined; using template narrative.')
        return None

    tok_in += resp.usage.input_tokens
    tok_out += resp.usage.output_tokens
    cost = tok_in * PRICE_IN + tok_out * PRICE_OUT

    n = resp.parsed_output
    # The schema asks for 5 and 4; trim or pad defensively so layout never breaks.
    n.radio_benefits = n.radio_benefits[:5]
    n.recommendations = n.recommendations[:4]

    log.info('Narrative written: %d in / %d out tokens, $%.4f', tok_in, tok_out, cost)
    return {'narrative': n, 'research': brief, 'cost': cost}
