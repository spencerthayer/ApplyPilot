#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from applypilot.tailoring.comprehensive_engine import ComprehensiveTailoringEngine


def load_profile():
    profile_path = Path.home() / ".applypilot" / "profile.json"
    with open(profile_path) as f:
        return json.load(f)


def load_v7_output():
    v7_path = Path.home() / ".applypilot" / "tailored_resumes" / "ai_engineer_v7.txt"
    with open(v7_path) as f:
        return f.read()


def create_mock_llm():
    mock = MagicMock()
    mock.ask.return_value = json.dumps(
        {
            "variants": {
                "car": "Led migration from LAMP to headless architecture (Next.js, TypeScript, Supabase), reducing infrastructure costs by $35,000/month",
                "who": "Architected platform modernization using modern tech stack, achieving significant cost savings",
                "technical": "Implemented DatoCMS + NextJS + TypeScript + Supabase + Typesense stack",
                "product": "Led platform modernization initiative saving $35k/month while improving performance",
            },
            "tags": ["architecture", "cost_optimization"],
            "skills": ["nextjs", "typescript", "supabase"],
            "domains": ["platform", "infrastructure"],
            "role_families": ["ai_engineer", "engineering_manager"],
        }
    )
    return mock


def run_comprehensive_test():
    print("=" * 80)
    print("Comprehensive Tailoring Engine Integration Test")
    print("=" * 80)

    print("\n[1/5] Loading profile from ~/.applypilot/profile.json...")
    profile = load_profile()
    print(f"    Loaded profile for {profile['personal']['full_name']}")
    print(f"    Work history: {len(profile['work_history'])} roles")
    print(f"    Key metrics: {len(profile['resume_facts']['real_metrics'])} preserved")

    print("\n[2/5] Initializing ComprehensiveTailoringEngine with mocked LLM...")
    mock_llm = create_mock_llm()

    with patch("applypilot.tailoring.comprehensive_engine.get_client", return_value=mock_llm):
        engine = ComprehensiveTailoringEngine(config={})
        print("    Engine initialized")

        print("\n[3/5] Running PreprocessLibrary phase (building bullet bank)...")
        engine.preprocess_library(profile)
        print(f"    Built bullet bank with {len(engine.bullet_bank)} bullets")
        print(f"    Metrics registry: {len(engine.metrics_registry)} metrics")

        print("\n    Sample bullets from bank:")
        for i, (bullet_id, bullet) in enumerate(list(engine.bullet_bank.items())[:3]):
            print(f"    [{bullet_id}]: {bullet.text[:80]}...")

        print("\n[4/5] Creating test AI Engineer job...")
        job = {
            "job_id": "test_ai_engineer",
            "description": """
            AI Engineer - Machine Learning Platform
            
            We are seeking an AI Engineer to build and scale our machine learning infrastructure.
            
            Requirements:
            - 5+ years experience with Python and ML frameworks
            - Experience with LLMs, vector embeddings, and RAG architectures
            - Production experience with recommendation systems
            - Strong background in data pipelines and ETL
            - Experience with cloud platforms (AWS, GCP, or Azure)
            - Track record of delivering measurable business impact
            
            Nice to have:
            - Experience with content automation and personalization
            - Background in affiliate marketing or e-commerce
            - Open source contributions
            """,
            "role_type": "ai_engineer",
        }
        print(f"    Created job: {job['job_id']}")

        print("\n[5/5] Running TailorForJob phase...")
        result = engine.tailor_for_job(job)
        print(f"    Generated resume ({len(result.split(chr(10)))} lines)")

        output_dir = Path.home() / ".applypilot" / "tailored_resumes"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "ai_engineer_comprehensive.txt"
        with open(output_path, "w") as f:
            f.write(result)
        print(f"    Saved to: {output_path}")

        print("\n" + "=" * 80)
        print("Comparison with v7")
        print("=" * 80)

        v7_output = load_v7_output()
        v7_lines = len(v7_output.split("\n"))
        comp_lines = len(result.split("\n"))

        print(f"\nv7 output: {v7_lines} lines")
        print(f"Comprehensive: {comp_lines} lines")

        print("\nMetrics verification:")
        v7_metrics = ["$35k/month", "$35,000/month", "125% YoY", "125%", "108% YoY", "400% YoY", "150%"]

        for metric in v7_metrics:
            in_v7 = metric in v7_output
            in_comp = metric in result
            status = "PASS" if in_comp else "MISS"
            print(f"    {status} {metric:20} (v7: {'PASS' if in_v7 else 'MISS'})")

        print("\nComprehensive engine improvements:")
        improvements = [
            ("Evidence tracking", hasattr(engine, "evidence_ledger")),
            ("Audit trail", hasattr(engine, "audit_results")),
            ("Mechanism compliance", "architected" in result.lower() or "implemented" in result.lower()),
            ("Template variants", "car" in result.lower() or "who" in result.lower()),
        ]

        for feature, present in improvements:
            status = "PASS" if present else "PENDING"
            print(f"    {status} {feature}")

        print("\n" + "=" * 80)
        print("Test Complete")
        print("=" * 80)
        print(f"\nOutput saved to: {output_path}")
        print("\nTo view the full result:")
        print(f"    cat {output_path}")

    return result


if __name__ == "__main__":
    try:
        run_comprehensive_test()
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
