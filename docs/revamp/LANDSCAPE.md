# Competitive Landscape

Research date: 2026-11-20. Author role: CEO agent (research and product critic).

Conventions: every factual claim carries a source URL. `[vendor, biased]` marks claims from a
vendor's own marketing, blog, or pricing page about itself (prices on a vendor's own pricing page
are treated as fact). Lines starting **Inference:** are my judgment, not sourced fact. G2 and
Capterra blocked direct fetching (HTTP 403), so review quotes and ratings marked *(snippet)* come
from search-engine snippets of those pages and could not be read in full.

## 1. Comparison table

| Product | Published price (checked 2026-11-20) | Core signals / data | Delivery channels | CRM sync / integrations |
|---|---|---|---|---|
| **Harmonic** | No public price; every lane is "Get pricing" ([pricing](https://harmonic.ai/pricing)). Third-party estimate about $10K per seat per year, 3-seat minimum ([multiples.vc](https://multiples.vc/compare/harmonic-api-alternatives)) | 30M+ companies, 190M+ people, headcount and web-traffic deltas, funding, stealth-founder alerts ([home](https://harmonic.ai/)) [vendor, biased] | Slack alerts for saved searches, daily or weekly at 9am PST ([support](https://support.harmonic.ai/en/articles/5878370-slack-integration)); email subscribe ([support](https://support.harmonic.ai/en/articles/7025950-how-can-i-enable-email-notifications)); mobile web console ([pricing](https://harmonic.ai/pricing)) | Native Affinity push ([support](https://support.harmonic.ai/en/articles/9171056-integrations)); Salesforce via Polytomic ([collection](https://support.harmonic.ai/en/collections/15682075-integrations)); CRM sync is a paid add-on; API, bulk data, Zapier, n8n, MCP ([pricing](https://harmonic.ai/pricing), [home](https://harmonic.ai/)) |
| **Affinity** | $2,000 / $2,300 / $2,700 per user per year (Essential / Scale / Advanced), annual billing; Enterprise custom ([pricing](https://www.affinity.co/pricing)) | Email and calendar capture, relationship scoring; premium PitchBook, Dealroom, and Crunchbase enrichment on Advanced ([pricing](https://www.affinity.co/pricing)) | Mobile app, Slack, Teams listed; per-tier availability unclear; no email digest mentioned ([pricing](https://www.affinity.co/pricing)) | It is the CRM. API at 100K calls/month below Enterprise; hosted MCP on Scale+ ([pricing](https://www.affinity.co/pricing)) |
| **Attio** | Free (3 users); Plus $35, Pro $79, Ultra $189 per extra user/month on annual billing; Ultra $1,000/month workspace minimum ([pricing](https://attio.com/pricing)) | General CRM, no proprietary venture dataset ([pricing](https://attio.com/pricing)) | Slack app, Gmail and Outlook ([pricing](https://attio.com/pricing)) | API, webhooks, and MCP on all plans ([pricing](https://attio.com/pricing)) |
| **4Degrees** | Not published; third-party figures conflict, $100 to $500/month ([SelectHub](https://www.selecthub.com/p/crm-software/4degrees/), [Software Advice](https://www.softwareadvice.com/crm/4degrees-profile/)) | Relationship scoring from email and calendar, "4,000+ signals" ([home](https://4degrees.ai/)) [vendor, biased] | iOS and Android app ([blog](https://www.4degrees.ai/blog/what-is-new-at-4degrees)) [vendor, biased]; no digest found | Layer inside Salesforce; PitchBook and Crunchbase enrichment; Zapier ([Salesforce page](https://www.4degrees.ai/salesforce)) [vendor, biased] |
| **PitchBook** | Not published. Vendr-based median contract $30K/yr; single seat about $12K to $20K ([CostBench](https://costbench.com/software/financial-data-terminals/pitchbook/)). Priced per licensed user per Morningstar 10-K, cited by [privateequitylist](https://privateequitylist.com/intelligence/pitchbook-pricing) | Private funding, valuations, deals, investors ([workflow](https://pitchbook.com/products/desktop/workflow)) | Email alerts on saved searches and lists ([workflow](https://pitchbook.com/products/desktop/workflow)); iOS and Android apps ([Capterra](https://www.capterra.com/p/202323/PitchBook/reviews/)) | Salesforce, Dynamics, HubSpot, DealCloud, Affinity; integrations are priced extra ([CRM page](https://pitchbook.com/products/crm-integration)) |
| **Specter** | No public price; seat, credit-metered API, bulk data ([pricing](https://www.tryspecter.com/pricing)) | 50M+ companies, 700M+ people; talent, stealth, revenue, and investor-interest signals; modeled revenue and valuation ([pricing](https://www.tryspecter.com/pricing)) [vendor, biased] | Slack, email, webhooks, Zapier, CSV; no mobile mentioned ([pricing](https://www.tryspecter.com/pricing)) | Attio, Affinity, Salesforce, HubSpot, Pipedrive, Airtable; MCP ([pricing](https://www.tryspecter.com/pricing)) |
| **Sourcescrub** | Not published; "Contact Us" ([Capterra](https://www.capterra.com/p/227581/SourceScrub/), [G2 pricing](https://g2.com/products/sourcescrub/pricing)) | Private-company data, conference data ([Intapp](https://www.intapp.com/?p=4914)) | Weekly reports via CRM ([HubSpot page](https://www.sourcescrub.com/crm-integrations/hubspot)) [vendor, biased] | Affinity, DealCloud, HubSpot, Salesforce on daily, weekly, or monthly enrichment ([Affinity](https://sourcescrub.com/crm-integrations/affinity), [DealCloud](https://sourcescrub.com/crm-integrations/dealcloud)) |
| **Clay** | Free; Launch $185/mo ($167 annual); Growth $495/mo ($446 annual); credits from $0.05 ([pricing](https://www.clay.com/pricing)) | GTM enrichment: job changes, web intent ([docs](https://university.clay.com/docs/signals)) | Slack alerts, email campaigns ([signals](https://www.clay.com/lp/signals)) [vendor, biased] | CRM sync and webhooks on Growth+ only ([pricing](https://www.clay.com/pricing)) |
| **Dealroom** | EUR 250 / 325 per seat/month (Premium / Premium Plus), annual, 3-seat minimum; API is a usage-priced add-on ([pricing](https://dealroom.co/pricing)) | Funding, founder activity, ecosystem data ([solutions](https://www.dealroom.co/solutions/)) | Funding alerts; Slack connector ([API](https://dealroom.co/api/)) | Salesforce, HubSpot, Zapier, n8n, MCP ([API](https://dealroom.co/api/)) |

**Inference:** Dealroom's cheapest legal configuration is 3 x EUR 250 x 12 = EUR 9,000/yr,
derived from the published per-seat price.

### Open-source alternatives found

| Project | What it is | Status |
|---|---|---|
| [VCRM](https://github.com/nralston-saif/vcrm) | Fork-and-run VC backend from SAIF: deal intake, partner voting, portfolio, notes ([post, 2026-03-20](https://nralston.substack.com/p/vcrm-an-open-source-backend-for-any)) | License not stated in post |
| [DF Hub](https://www.github.com/nicoalbanese/df-hub-opensource) | "Open source(ish)" Affinity/Attio alternative | README says in progress ([search summary](https://www.github.com/nicoalbanese/df-hub-opensource)) |
| [subsignal](https://github.com/0xnyn/subsignal) | Deal-flow monitoring for funds, 29 stars | Listed in [open-source-vc](https://github.com/maximally0/open-source-vc) |
| [ExecSum Deal Brief](https://github.com/kklounge/execsum-deal-brief) | Daily deal-flow brief with ranked top three, 50 stars | Same list |
| [Twenty](https://github.com/twentyhq/twenty) | General CRM, 56.7k stars | License listed as "Other" ([open-source-vc](https://github.com/maximally0/open-source-vc)); BSL-1.1 per [one listing](https://github.com/studiolanes/twenty), not OSI open source |
| [yc-oss/api](https://github.com/yc-oss/api) | YC company data as an API | Same list |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Apache-2.0 web-page change monitor, 34.1k stars | Same list |
| [awesome-vc-opensource](https://github.com/isanthoshgandhi/awesome-vc-opensource), [open-source-vc](https://github.com/maximally0/open-source-vc) | Curated indexes (the latter claims 226 projects) | Indexes |

**Inference:** No open-source project I found combines thesis-driven discovery, evidence and
confidence, change tracking, and scheduled delivery. Existing FOSS splits into CRM shells
(VCRM, DF Hub, Twenty) and single-purpose monitors. That gap is real but small.

## 2. Real-user complaints

At most one quote per source.

- **Harmonic** (G2, about 5 reviews, *snippet*): "Maybe a better mobile app and more coverage of LP data" ([G2](https://www.g2.com/products/harmonic/reviews)). A reviewer also says it gives no edge "if everyone uses the same tool" (same page). A competitor makes the same argument [vendor, biased] ([Crustdata](https://crustdata.com/blog/why-harmonic-doesnt-give-your-fund-a-sourcing-edge)).
- **Affinity** (G2 pricing page, *snippet*): "not meant for emerging managers" ([G2](https://www.g2.com/products/affinity-relationship-intelligence/pricing)). TrustRadius users describe choppy navigation on large datasets ([TrustRadius](https://www.trustradius.com/products/affinity-crm/reviews)). In an independent survey, 42% of Affinity users say they are "not a fan" ([EWVC survey](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)).
- **Attio** (Capterra, 3.8/5 from 8 reviews, *snippet*): "a few important features were missing, like reporting" ([Capterra NZ](https://www.capterra.co.nz/software/1001325/attio)).
- **PitchBook** (TrustRadius): "pricey and lacks info on smaller companies" ([TrustRadius](https://www.trustradius.com/reviews/pitchbook-2025-05-16-08-32-09)). G2 tags show about 36 reviews flagging outdated data on niche companies and 20 flagging expense. These counts vary by page snapshot (*snippet*, [G2](https://g2.com/products/pitchbook/reviews?page=2)).
- **Clay** (Clay community thread title): "charging credits for using my own API keys has got to be a joke" ([community](https://community.clay.com/x/general/q4kzatudctfv/charging-credits-for-using-my-own-api-keys-has-got)). Clay called its March 2026 change its biggest pricing change since launch ([Clay blog](https://www.clay.com/blog/clay-pricing-memo-internal)) [vendor, biased].
- **Dealroom**: data-accuracy score of 7.6, its lowest feature score on a G2 comparison page. The exact page is unconfirmed (*snippet*, [G2](https://www.g2.com/compare/crunchbase-vs-dealroom-co)).
- **Sourcescrub**: 4.5/5 from 56 G2 reviews; no substantive complaints surfaced (*snippet*, [G2](https://g2.com/products/sourcescrub/reviews?page=2)).
- **4Degrees, Specter**: too few reviews. 4Degrees has 4 Capterra reviews ([Capterra](https://www.capterra.com/p/184593/4Degrees/)); Specter has 0 on G2 ([G2](https://www.g2.com/products/specter/reviews)).

Market-level fact: pricing is the top reason VCs reject tools (39%), ahead of workflow mismatch
(18%) and implementation effort (9%). Data privacy is the top concern in every category. That
survey had 250+ respondents, ran March to April 2026, and calls itself unsponsored
([EWVC survey](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)).

## 3. Where a FOSS, BYOK, local-and-hosted tool can win, and where it cannot

### Can genuinely be better

1. **Price.** PitchBook's median contract is $30K/yr ([CostBench](https://costbench.com/software/financial-data-terminals/pitchbook/)). Dealroom starts around EUR 9K/yr (derived above). Affinity costs $2K+ per user ([pricing](https://www.affinity.co/pricing)). Price is the top rejection reason ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). **Inference:** emerging managers and solo GPs, who are priced out today, are the wedge.
2. **BYOK honesty.** The Clay thread shows users resent paying markup on their own keys ([community](https://community.clay.com/x/general/q4kzatudctfv/charging-credits-for-using-my-own-api-keys-has-got)). **Inference:** "your key, your cost, zero markup" is a clean message.
3. **Transparency and calibrated confidence.** Incumbents sell scores and lists. Users complain that everyone runs the same filters on the same data ([G2 snippet](https://www.g2.com/products/harmonic/reviews)). **Inference:** showing exact provenance per claim and an editable, private thesis is a differentiator no closed vendor has an incentive to copy, because their value is the opaque dataset.
4. **Privacy and local-first.** Privacy is the top concern ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). **Inference:** "your thesis and pipeline never leave your machine" beats a hosted SaaS for some funds, but only if the hosted mode does not dilute it.
5. **No lock-in.** CSV, webhooks, and an open data model let funds keep their CRM. Every incumbent bundles CRM sync as a paid add-on or upper tier: [Harmonic](https://harmonic.ai/pricing), [PitchBook](https://pitchbook.com/products/crm-integration), [Clay](https://www.clay.com/pricing).
6. **OSINT niches incumbents under-index.** Examples: public code activity, accelerator cohorts, grants, papers, conference speakers. Projects like [yc-oss/api](https://github.com/yc-oss/api) and [changedetection.io](https://github.com/dgtlmoon/changedetection.io) show the raw material is open. **Inference:** this is where a custom thesis plus public sources can beat a generic database for a specific fund.

### Cannot compete (blunt)

- **Headcount and web-traffic time series.** Harmonic sells 90-day engineering-headcount and 30-day traffic deltas ([home](https://harmonic.ai/)). We will not have this without a paid data vendor.
- **Private funding data.** PitchBook is the most-used sourcing source at 52% ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)), and its moat is round-level data. We can only parse public announcements and filings.
- **Stealth detection recall.** Specter's flagship stealth example is a LinkedIn status change ([talent](https://www.tryspecter.com/signals/talent)) [vendor, biased]. A G2 reviewer says Harmonic leans on LinkedIn scraping (*snippet*, [G2](https://www.g2.com/products/harmonic/reviews)). **Inference:** with no LinkedIn or X crawling, our stealth recall will be a fraction of theirs. We should sell precision with evidence, never coverage, and say so in the product.
- **Relationship intelligence.** Affinity and 4Degrees mine email and calendars ([Affinity](https://www.affinity.co/pricing), [4Degrees](https://www.4degrees.ai/salesforce)). **Inference:** warm-intro paths without inbox data will be weak, and inbox data collides with the privacy promise.
- **Modeled revenue and valuation.** Specter ships modeled revenue and valuation with confidence bands ([pricing](https://www.tryspecter.com/pricing)) [vendor, biased]. Out of scope for us.

**Inference, overall:** we are not a Harmonic replacement. We are a thesis-specific, explainable
complement that a fund runs alongside, or instead of, a database it cannot afford.

## 4. Proposed deferrals (hypotheses pending owner approval, not scope cuts)

These are the CEO agent's recommendations. Every feature in the brief stays in scope until the owner explicitly approves a deferral.

**Inference throughout, with supporting evidence cited.**

- **Defer the mobile channel indefinitely.** Harmonic offers only a mobile web console ([pricing](https://harmonic.ai/pricing)). Specter lists no mobile channel ([pricing](https://www.tryspecter.com/pricing)). The EWVC 12-month wishlist does not mention mobile ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). Email already renders on phones.
- **Defer Teams behind Slack.** Slack is the common alert channel for [Harmonic](https://support.harmonic.ai/en/articles/5878370-slack-integration), [Specter](https://www.tryspecter.com/pricing), and [Dealroom](https://dealroom.co/api/). Build Teams only on request.
- **Shrink "CRM-lite" to an inbox, not a CRM.** Affinity holds 40% and Attio is the most loved ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). Workflow mismatch is the second rejection reason (same source). Keep shortlist triage (save, dismiss, note, and push), and cut pipeline stages and a home-grown deal board.
- **Defer warm-intro paths.** They need inbox or social-graph data that we either will not collect or cannot get (section 3). Revisit only as an opt-in, local-only import.
- **Deprioritize L1 explorers.** Paying pain sits with L2 analysts and L4 technical VCs. Keep L1 to a demo mode.
- **Keep MCP, cheaply.** Affinity, Attio, Specter, and Dealroom all ship MCP ([Affinity](https://www.affinity.co/pricing), [Attio](https://attio.com/pricing), [Specter](https://www.tryspecter.com/pricing), [Dealroom](https://dealroom.co/api/)). It is table stakes for L4.
- **Hosted mode: single-tenant first.** Privacy is the top concern ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). Defer multi-tenant SaaS until the local loop is proven.

## 5. CRMs VC firms use most (for sync adapters)

Evidence is thin: only one independent survey with VC-specific CRM share exists. Supply-side
evidence comes from which CRMs data vendors bother to integrate with.

| Rank | CRM | Evidence | Adapter priority |
|---|---|---|---|
| 1 | **Affinity** | Most used, 40% of funds, independent survey of 250+ ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). Affinity itself also claims 40% and 3,300+ firms ([Affinity](https://www.affinity.co/affinity-answers)) [vendor, biased]. Integrated by Harmonic, Specter, Sourcescrub, and PitchBook (table above) | Build first |
| 2 | **Attio** | "Most loved"; over 80% of its users love it, but adoption runs about 4x below interest ([EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)). Integrated by Specter ([pricing](https://www.tryspecter.com/pricing)). Free tier plus API, webhooks, and MCP on all plans ([pricing](https://attio.com/pricing)) | Build second (cheapest to build, fastest-growing) |
| 3 | **Salesforce** | Described as the enterprise standard for large institutional firms ([vcbeast](https://vcbeast.com/best-crm-for-venture-capital)) (third-party blog, no share data). Integrated by PitchBook, Specter, Sourcescrub, Clay, Dealroom, and 4Degrees (table above) | Third |
| 4 | **HubSpot** | "Used by many VCs" per a blog, no share data ([vcbeast](https://vcbeast.com/vc-tools-and-crm)). Leads at 31.1% (Attio 15.2%) among 671 startups, not VCs, that requested a demo from Zero, a CRM vendor. The vendor says this over-represents unhappy teams ([zero.inc](https://zero.inc/blog/what-crm-do-startups-use)) [vendor, biased]. Integrated by PitchBook, Specter, Sourcescrub, and Dealroom | Fourth |
| 5 | **Intapp DealCloud** | Marketed for PE and VC with a VC blueprint ([Intapp](https://www.intapp.com/?p=25084)) [vendor, biased]. Integrated by PitchBook and Sourcescrub. **Inference:** skews to PE and large funds | Defer |
| n/a | Airtable / Notion | Recommended for the smallest funds ([vcbeast](https://vcbeast.com/best-crm-for-venture-capital)) | Covered by CSV and webhook |

## 6. Hypotheses about what VCs want

**These are hypotheses, not findings. They stay hypotheses until real user feedback arrives.**
Each is my inference, with the nearest supporting evidence noted.

- **H1.** Analysts want fewer, better leads with reasons, not bigger lists. Five explained names per week beat a 200-row export (supported by the "same data, no edge" complaint, [G2 snippet](https://www.g2.com/products/harmonic/reviews)).
- **H2.** "What changed since last time" is the most valued part of the digest, more than the ranking itself.
- **H3.** Funds will tolerate lower recall if every claim links to its source and confidence is honest. Precision with provenance builds trust that black-box scores do not.
- **H4.** Emerging managers and solo GPs will self-host. Larger funds will want hosted single-tenant, and will not leave their CRM (price and workflow-mismatch rejection reasons, [EWVC](https://ewvc.beehiiv.com/p/vc-tech-stack-pulse-2026)).
- **H5.** Email is the right first channel; Slack is the second channel that sticks for teams. Mobile apps add little.
- **H6.** Technical VCs (L4) will adopt through MCP and CLI before the web UI, and will contribute connectors if the source plug-in contract is simple.
- **H7.** A written, versioned thesis that the tool scores against is novel enough to be the headline feature.

## 7. Could not verify

- Harmonic, Specter, Sourcescrub, 4Degrees, and PitchBook prices (none published; estimates are third-party).
- The full text of G2 and Capterra reviews (403 on fetch; snippets only).
- Any Reddit or HN thread specifically about Harmonic or Specter.
- Independent VC-specific market share for Attio, Salesforce, HubSpot, or DealCloud.
- An "OpenDealflow" open-source VC CRM mentioned in one search summary. No repo found.
- VCRM's license.
