"""
Seed realistic demo responses for the Baobab walkthrough.

Reads the existing solicitation, fixes the existing response, and creates
additional responses from named organizations with realistic answers.

Usage:
    python manage.py seed_demo_responses --solicitation-id <ID>
    python manage.py seed_demo_responses --solicitation-id <ID> --dry-run
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager
from commcare_connect.solicitations.data_access import SolicitationsDataAccess

# Three realistic partner organizations for the demo
DEMO_RESPONSES = [
    {
        "llo_entity_id": "health_bridge_ng",
        "llo_entity_name": "Health Bridge Nigeria",
        "org_name": "Health Bridge Nigeria",
        "org_id": "health_bridge_ng",
        "submitted_by_name": "Amina Okafor",
        "submitted_by_email": "amina.okafor@healthbridge.ng",
        "status": "submitted",
        "tone": "strong",  # Used to vary answer quality
    },
    {
        "llo_entity_id": "mwangaza_health_ke",
        "llo_entity_name": "Mwangaza Health Initiative",
        "org_name": "Mwangaza Health Initiative",
        "org_id": "mwangaza_health_ke",
        "submitted_by_name": "Grace Wanjiku",
        "submitted_by_email": "grace.wanjiku@mwangaza.or.ke",
        "status": "submitted",
        "tone": "moderate",
    },
    {
        "llo_entity_id": "sahel_community_bf",
        "llo_entity_name": "Sahel Community Health Partners",
        "org_name": "Sahel Community Health Partners",
        "org_id": "sahel_community_bf",
        "submitted_by_name": "Ibrahim Traoré",
        "submitted_by_email": "ibrahim.traore@sahelhealth.org",
        "status": "submitted",
        "tone": "developing",
    },
]

# Answer templates keyed by question keyword → tone
ANSWER_BANK = {
    "experience": {
        "strong": (
            "Health Bridge Nigeria has delivered CHW training programs across 6 states "
            "since 2018, reaching over 4,200 community health workers. Our flagship program "
            "in Lagos and Ogun states achieved a 94% training completion rate and a 78% "
            "improvement in correct case management for childhood diarrhea and malaria. "
            "We partner with state primary healthcare boards and have MOUs with 23 LGAs."
        ),
        "moderate": (
            "Mwangaza Health Initiative has been training community health volunteers in "
            "western Kenya since 2020. We have trained 1,800 CHVs across Kisumu and Siaya "
            "counties with a focus on maternal and newborn health. Our training curriculum "
            "was developed with the Kenya Ministry of Health and has been adopted by two "
            "county health departments."
        ),
        "developing": (
            "Sahel Community Health Partners works in Burkina Faso and Niger on community "
            "health worker training. We trained 600 ASCs (agents de santé communautaire) "
            "last year with support from WHO regional office. We are expanding our "
            "geographic reach and seeking partnerships to scale our approach."
        ),
    },
    "approach": {
        "strong": (
            "Our approach combines classroom instruction (2 weeks) with supervised field "
            "practice (4 weeks) and ongoing mentorship. Each cohort of 25 CHWs is assigned "
            "a clinical mentor who conducts monthly supportive supervision visits for 6 months "
            "post-training. We use CommCare for real-time data collection and performance "
            "monitoring, with dashboards accessible to supervisors and LGA health teams. "
            "Training materials are available in English, Yoruba, and Hausa."
        ),
        "moderate": (
            "We use a blended learning model: 10 days of in-person training followed by "
            "mobile-based refresher modules delivered via CommCare. CHVs complete case "
            "studies and practice exercises on their phones. Supervisors review submissions "
            "weekly and flag CHVs who need additional support. We plan to add AI-assisted "
            "feedback on practice exercises."
        ),
        "developing": (
            "Training is delivered over 3 weeks in community health centers. ASCs learn "
            "basic assessment, referral protocols, and health education delivery. We "
            "recently began using mobile data collection and are looking for technical "
            "partners to strengthen our digital monitoring approach."
        ),
    },
    "budget": {
        "strong": (
            "Total budget: $450,000 over 18 months. Personnel (45%): Program manager, "
            "4 regional coordinators, 12 clinical mentors. Training delivery (30%): "
            "Venue, materials, per diems for 600 CHWs across 3 cohorts. Technology (10%): "
            "CommCare licenses, devices, connectivity. M&E (10%): Baseline/endline surveys, "
            "quarterly assessments. Overhead (5%): Office, transport, administration."
        ),
        "moderate": (
            "Proposed budget: $280,000 for 12 months. This covers training of 400 CHVs "
            "(2 cohorts of 200), supervisor stipends, CommCare platform costs, and program "
            "coordination. We request a 15% indirect cost rate. Detailed line-item budget "
            "available on request."
        ),
        "developing": (
            "We estimate $150,000-$200,000 for the program. Major costs include trainer "
            "compensation, training materials, transport, and mobile devices for ASCs. "
            "We would welcome guidance on appropriate budget allocation across categories."
        ),
    },
    "timeline": {
        "strong": (
            "Month 1-2: Recruitment and curriculum adaptation. Month 3-4: Cohort 1 training "
            "(200 CHWs). Month 5-6: Cohort 1 mentorship + Cohort 2 training. Month 7-8: "
            "Cohort 2 mentorship + Cohort 3 training. Month 9-12: Cohort 3 mentorship + "
            "performance evaluation. Month 13-18: Sustainability transition, refresher "
            "training, and program documentation."
        ),
        "moderate": (
            "Quarter 1: Stakeholder engagement and curriculum localization. Quarter 2: "
            "First cohort training and deployment. Quarter 3: Second cohort training, "
            "first cohort monitoring. Quarter 4: Program evaluation and reporting."
        ),
        "developing": (
            "We plan to begin training within 2 months of award and complete all training "
            "within 8 months. Monitoring will continue for the full project period."
        ),
    },
    "impact": {
        "strong": (
            "We target a 40% reduction in under-5 mortality in program areas, measured "
            "against DHIS2 baselines. Secondary outcomes: 90%+ CHW retention at 12 months, "
            "80%+ correct case management (verified by clinical audit), and 50% increase "
            "in care-seeking behavior. We will publish results in a peer-reviewed journal "
            "and share our adapted curriculum as an open resource."
        ),
        "moderate": (
            "Expected outcomes: 85% of trained CHVs active at 12 months, 70% improvement "
            "in knowledge assessment scores, measurable increase in facility-based deliveries "
            "in target areas. We will track outcomes through CommCare data and quarterly "
            "household surveys."
        ),
        "developing": (
            "We expect to see improved health knowledge among trained ASCs and increased "
            "referrals to health facilities. We will track training completion rates and "
            "monthly activity reports from ASCs."
        ),
    },
    # Fallback for any question not matched above
    "default": {
        "strong": (
            "Health Bridge Nigeria brings 8 years of community health programming experience "
            "across West Africa. We have strong government partnerships, proven training "
            "methodologies, and robust monitoring systems. Our team includes public health "
            "specialists, clinical trainers, and technology experts."
        ),
        "moderate": (
            "Mwangaza Health Initiative has a growing track record in community health "
            "programming in East Africa. We are committed to evidence-based approaches "
            "and continuous improvement in our programs."
        ),
        "developing": (
            "Sahel Community Health Partners is dedicated to improving health outcomes "
            "in the Sahel region. We bring local knowledge, community trust, and "
            "a commitment to building sustainable health systems."
        ),
    },
}

# Keywords to match questions to answer categories
QUESTION_KEYWORDS = {
    "experience": ["experience", "track record", "background", "history", "previous", "past work"],
    "approach": ["approach", "methodology", "how will", "strategy", "plan to", "implement", "describe your"],
    "budget": ["budget", "cost", "financial", "funding", "price", "expenditure"],
    "timeline": ["timeline", "schedule", "when", "milestones", "duration", "timeframe"],
    "impact": ["impact", "outcome", "result", "measure", "metric", "success", "kpi", "indicator"],
}


def match_question_category(question_text: str) -> str:
    """Match a question to an answer category based on keywords."""
    text_lower = question_text.lower()
    for category, keywords in QUESTION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "default"


class Command(BaseCommand):
    help = "Seed realistic demo responses for the Baobab walkthrough"

    def add_arguments(self, parser):
        parser.add_argument(
            "--solicitation-id",
            type=int,
            help="ID of the solicitation to seed responses for",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created without making API calls",
        )
        parser.add_argument(
            "--profile",
            default="main",
            help="Token profile to use (default: main)",
        )
        parser.add_argument(
            "--fix-existing",
            action="store_true",
            default=True,
            help="Fix org_name and submitted_by_name on existing responses",
        )

    def handle(self, *args, **options):
        # Get token
        tm = TokenManager(profile=options["profile"])
        token = tm.get_valid_token()
        if not token:
            self.stderr.write(
                self.style.ERROR(
                    f"No valid token for profile '{options['profile']}'. "
                    "Run: python manage.py get_cli_token --profile main"
                )
            )
            return
        da = SolicitationsDataAccess(access_token=token)

        # Find the solicitation
        solicitation_id = options["solicitation_id"]
        if not solicitation_id:
            # List public solicitations to help the user find the right one
            solicitations = da.get_public_solicitations()
            if not solicitations:
                self.stderr.write(self.style.ERROR("No public solicitations found"))
                return

            self.stdout.write("\nAvailable solicitations:")
            for s in solicitations:
                self.stdout.write(f"  ID {s.id}: {s.title} ({s.status})")
            self.stderr.write(self.style.ERROR("\nPass --solicitation-id <ID> to seed responses"))
            return

        solicitation = da.get_solicitation_by_id(solicitation_id)
        if not solicitation:
            self.stderr.write(self.style.ERROR(f"Solicitation {solicitation_id} not found"))
            return

        self.stdout.write(self.style.SUCCESS(f"\nSolicitation: {solicitation.title} (ID {solicitation.id})"))
        questions = solicitation.questions or []
        self.stdout.write(f"  Questions ({len(questions)}):")
        for q in questions:
            self.stdout.write(f"    - [{q.get('id', '?')}] {q.get('text', '?')}")

        # Check existing responses
        existing = da.get_responses_for_solicitation(solicitation_id)
        self.stdout.write(f"\n  Existing responses: {len(existing)}")
        for r in existing:
            self.stdout.write(
                f"    ID {r.id}: {r.llo_entity_name} / {r.submitted_by_name} " f"({r.status}) org_name='{r.org_name}'"
            )

        # Fix existing responses with missing org_name / bad submitted_by_name
        if options["fix_existing"] and existing:
            self.stdout.write(self.style.WARNING("\nFixing existing responses..."))
            for r in existing:
                needs_fix = False
                data = dict(r.data)

                # Fix empty/None llo_entity_name
                entity_name = data.get("llo_entity_name") or ""
                org_name = data.get("org_name") or ""
                if not entity_name or entity_name in ("None", "individual"):
                    data["llo_entity_name"] = org_name or "Community Health Partners"
                    needs_fix = True

                # Fix empty org_name
                if not org_name:
                    data["org_name"] = data.get("llo_entity_name", "Community Health Partners")
                    needs_fix = True

                # Fix "None None" submitted_by_name
                name = data.get("submitted_by_name") or ""
                if not name.strip() or name.strip() == "None None":
                    email = data.get("submitted_by_email") or ""
                    data["submitted_by_name"] = email.split("@")[0] if email else "Demo User"
                    needs_fix = True

                if needs_fix:
                    if options["dry_run"]:
                        self.stdout.write(
                            f"  [DRY RUN] Would fix response {r.id}: "
                            f"org_name='{data.get('org_name')}', "
                            f"submitted_by_name='{data.get('submitted_by_name')}'"
                        )
                    else:
                        da.update_response(r.id, data)
                        self.stdout.write(self.style.SUCCESS(f"  Fixed response {r.id}"))

        # Check which demo orgs already have responses
        existing_entities = {r.llo_entity_id for r in existing}

        # Create new demo responses (stagger submission dates over the past week)
        now = timezone.now()
        self.stdout.write(self.style.WARNING("\nCreating demo responses..."))
        for idx, org_info in enumerate(DEMO_RESPONSES):
            entity_id = org_info["llo_entity_id"]
            if entity_id in existing_entities:
                self.stdout.write(f"  Skipping {org_info['org_name']} — response already exists")
                continue

            # Build answers for each question
            tone = org_info["tone"]
            answers = {}
            for q in questions:
                q_id = q.get("id", "")
                q_text = q.get("text", "")
                category = match_question_category(q_text)
                answer_set = ANSWER_BANK.get(category, ANSWER_BANK["default"])
                answers[q_id] = answer_set.get(tone, answer_set.get("strong", ""))

            data = {
                "solicitation_id": solicitation_id,
                "llo_entity_id": entity_id,
                "llo_entity_name": org_info["llo_entity_name"],
                "responses": answers,
                "status": org_info["status"],
                "submitted_by_name": org_info["submitted_by_name"],
                "submitted_by_email": org_info["submitted_by_email"],
                "org_id": org_info["org_id"],
                "org_name": org_info["org_name"],
                "submission_date": (now - timedelta(days=idx + 1)).isoformat(),
            }

            if options["dry_run"]:
                self.stdout.write(f"  [DRY RUN] Would create response for {org_info['org_name']}")
                self.stdout.write(f"    Answers: {json.dumps(answers, indent=2)[:200]}...")
            else:
                resp = da.create_response(
                    solicitation_id=solicitation_id,
                    llo_entity_id=entity_id,
                    data=data,
                )
                self.stdout.write(self.style.SUCCESS(f"  Created response {resp.id} for {org_info['org_name']}"))

        self.stdout.write(self.style.SUCCESS("\nDone!"))
