"""Run one SpecGuard audit against Vertex AI and Firestore."""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from google.cloud import firestore

from specguard.agent import AdkClaimGenerator, AuditRuntime, create_adk_agent
from specguard.models import AuditRunSummary
from specguard.tools import AuditTools

DEFAULT_PROJECT = "specguard-hack"
DEFAULT_OUTPUT_DIRECTORY = Path("artifacts")


def _pdf_path(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"PDF does not exist: {value}")
    if path.suffix.casefold() != ".pdf":
        raise argparse.ArgumentTypeError(f"expected a PDF path: {value}")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit one cut-sheet PDF against one specification PDF."
    )
    parser.add_argument("--spec", required=True, type=_pdf_path, help="Specification PDF path.")
    parser.add_argument("--cutsheet", required=True, type=_pdf_path, help="Cut-sheet PDF path.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="Directory for the generated RFI draft PDF.",
    )
    return parser


async def _run(args: argparse.Namespace) -> AuditRunSummary:
    run_id = uuid.uuid4().hex
    firestore_client = firestore.Client(project=args.project)
    try:
        tools = AuditTools(
            firestore_client=firestore_client,
            spec_path=args.spec,
            cut_sheet_path=args.cutsheet,
            run_id=run_id,
            output_directory=args.output_dir,
        )
        agent = create_adk_agent(tools, project_id=args.project)
        claim_generator = AdkClaimGenerator(agent, run_id=run_id)
        runtime = AuditRuntime(
            claim_generator=claim_generator,
            tools=tools,
            spec_path=args.spec,
            cut_sheet_path=args.cutsheet,
            run_id=run_id,
        )
        return await runtime.run()
    finally:
        firestore_client.close()


def _print_summary(summary: AuditRunSummary) -> None:
    print("RUN SUMMARY")
    print(f"run id: {summary.run_id}")
    print(f"claims made: {summary.claims_made}")
    print(f"rejected: {summary.rejected}")
    print(f"retried: {summary.retried}")
    print(f"findings persisted: {summary.findings_persisted}")
    print(f"RFI path: {summary.rfi_path}")


def main() -> int:
    args = _parser().parse_args()
    summary = asyncio.run(_run(args))
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
