# ruff: noqa: E501
"""Build the fictional, text-based PDFs used by the SpecGuard demo.

All names, organizations, project details, and technical statements in these
fixtures are invented for the demo. The generator writes fixed metadata and
content so repeated builds produce identical extracted text.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURE_DIRECTORY = Path(__file__).parent
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
LEFT_MARGIN = 54.0
TOP_MARGIN = 72.0
BOTTOM_MARGIN = 54.0
BODY_FONT_SIZE = 9.5
LINE_HEIGHT = 15.0
FIXED_METADATA = {
    "title": "SpecGuard Fictional Demo Fixture",
    "author": "SpecGuard Fixture Builder",
    "subject": "Fictional construction submittal audit fixture",
    "keywords": "fictional, SpecGuard, demo fixture",
    "creator": "SpecGuard Fixture Builder",
    "producer": "PyMuPDF",
    "creationDate": "D:20260820000000+00'00'",
    "modDate": "D:20260820000000+00'00'",
}


def _page_lines(
    section: str, title: str, body: list[str], page_number: int, total_pages: int
) -> list[str]:
    """Add a stable fixture header and footer around freshly written body text."""
    return [
        "SPEC GUARD DEMO FIXTURE — ALL CONTENT IS FICTIONAL",
        section,
        title,
        "",
        *body,
        "",
        f"Page {page_number} of {total_pages} | Issued for demonstration | 20 August 2026",
    ]


def _write_pdf(path: Path, pages: list[list[str]]) -> None:
    """Write selectable text to each PDF page with fixed document metadata."""
    document = pymupdf.open()
    document.set_metadata(FIXED_METADATA)
    for lines in pages:
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        y = TOP_MARGIN
        for line in lines:
            if y > PAGE_HEIGHT - BOTTOM_MARGIN:
                raise ValueError(f"page content exceeds the printable area: {path.name}")
            font_size = 10.5 if line.startswith(("SECTION ", "CUT SHEET ")) else BODY_FONT_SIZE
            page.insert_text((LEFT_MARGIN, y), line, fontname="helv", fontsize=font_size)
            y += LINE_HEIGHT
    document.save(str(path), garbage=4, deflate=True, no_new_id=True)
    document.close()


def _specification_pages() -> list[list[str]]:
    """Return a seven-page fictional project specification with three sections."""
    project = "Asterquay Learning Workshop"
    owner = "Lumaquay Community Fabrication Authority"
    return [
        _page_lines(
            "PROJECT SPECIFICATION",
            "Asterquay Learning Workshop — Electrical Work",
            [
                f"Project: {project}",
                f"Owner: {owner}",
                "This document is an invented training specification for the SpecGuard demo.",
                "No project, company, manufacturer, or product in this document is real.",
                "Division 26 contains the electrical requirements used for the demonstration.",
                "Section 26 24 13 describes the distribution switchboard assembly.",
                "Section 26 05 19 describes conductor termination requirements.",
                "Section 01 33 00 provides routine submittal administration requirements.",
                "The design team uses this issue only to illustrate a traceable audit workflow.",
                "",
                "TABLE OF CONTENTS",
                "26 24 13 — Low-Voltage Distribution Switchboards",
                "26 05 19 — Low-Voltage Electrical Power Conductors and Cables",
                "01 33 00 — Submittal Procedures",
            ],
            1,
            7,
        ),
        _page_lines(
            "SECTION 26 24 13 — LOW-VOLTAGE DISTRIBUTION SWITCHBOARDS",
            "PART 1 — GENERAL",
            [
                "1.1 SUMMARY",
                "A. Furnish a floor-mounted distribution switchboard for the workshop service.",
                "B. Include service disconnecting means, distribution bus, and protective devices.",
                "C. Coordinate the assembly with the room dimensions shown on the fictional drawings.",
                "1.2 RELATED REQUIREMENTS",
                "A. Section 26 05 19 applies to conductors that land at this equipment.",
                "B. Section 01 33 00 applies to product data and factory test records.",
                "1.3 QUALITY ASSURANCE",
                "A. Submit a factory wiring diagram that identifies all field connection points.",
                "B. Identify each compartment with a durable engraved nameplate.",
                "C. Arrange equipment so routine service can occur from the front of the lineup.",
            ],
            2,
            7,
        ),
        _page_lines(
            "SECTION 26 24 13 — LOW-VOLTAGE DISTRIBUTION SWITCHBOARDS",
            "PART 2 — PRODUCTS",
            [
                "2.1 SERVICE DISTRIBUTION ASSEMBLY",
                "A. Provide a 480V, 3-phase distribution switchboard for service distribution.",
                "B. Provide a 4-wire solidly grounded system with a full-size neutral bus.",
                "C. Provide copper bus braced for the available fault current shown on the drawings.",
                "D. Provide main and feeder breakers with permanent circuit identification.",
                "E. Provide a barriered service section with a lockable front access door.",
                "2.2 ENCLOSURE",
                "A. Provide a painted indoor enclosure suitable for the dedicated electrical room.",
                "B. Provide lifting provisions that remain accessible during setting operations.",
                "C. Provide a base channel that permits anchorage after final equipment alignment.",
                "2.3 FACTORY WORK",
                "A. Perform routine production tests before shipment to the fictional project site.",
            ],
            3,
            7,
        ),
        _page_lines(
            "SECTION 26 05 19 — LOW-VOLTAGE ELECTRICAL POWER CONDUCTORS AND CABLES",
            "PART 1 — GENERAL",
            [
                "1.1 SUMMARY",
                "A. This section covers insulated conductors and their terminations at equipment.",
                "B. The work includes conductor preparation, lugs, torque records, and labels.",
                "1.2 SUBMITTALS",
                "A. Submit cut sheets for lugs and terminals before conductor installation begins.",
                "B. Submit installation data that states the conductor material and temperature rating.",
                "C. Submit a field torque log after each switchboard termination is complete.",
                "1.3 DELIVERY AND STORAGE",
                "A. Keep conductors dry and supported above the floor until installation.",
                "B. Protect exposed conductor ends from damage during construction activities.",
                "C. Replace any material whose insulation is cut, crushed, or permanently deformed.",
            ],
            4,
            7,
        ),
        _page_lines(
            "SECTION 26 05 19 — LOW-VOLTAGE ELECTRICAL POWER CONDUCTORS AND CABLES",
            "PART 2 — PRODUCTS",
            [
                "2.1 TERMINATIONS",
                "A. Conductor terminations shall be rated 90 deg C minimum.",
                "B. Use listed terminals that accept the conductor size installed at each location.",
                "C. Provide plated contact surfaces where the listed terminal requires them.",
                "D. Do not install conductors whose insulation has been damaged by pulling tools.",
                "2.2 INSTALLATION MATERIALS",
                "A. Use compression lugs for conductors larger than the manufacturer's set-screw range.",
                "B. Use oxide inhibitor only where the terminal manufacturer directs its use.",
                "C. Mark spare conductors at both ends with durable, machine-printed labels.",
                "2.3 FIELD QUALITY CONTROL",
                "A. Record final torque values for each service and feeder conductor termination.",
            ],
            5,
            7,
        ),
        _page_lines(
            "SECTION 01 33 00 — SUBMITTAL PROCEDURES",
            "PART 1 — GENERAL",
            [
                "1.1 PURPOSE",
                "A. Use this procedure to organize product data for the fictional project record.",
                "B. A submittal is complete only when its product identifier and revision are visible.",
                "1.2 FORMAT",
                "A. Group cut sheets by specification section and identify the proposed equipment.",
                "B. Mark optional features that are not supplied with the submitted configuration.",
                "C. State deviations in a separate note instead of hiding them in promotional material.",
                "1.3 REVIEW SEQUENCE",
                "A. The contractor checks completeness before sending material to the design team.",
                "B. The design team may return incomplete material without a technical review.",
                "C. Review does not relieve the contractor from furnishing the specified performance.",
            ],
            6,
            7,
        ),
        _page_lines(
            "SECTION 01 33 00 — SUBMITTAL PROCEDURES",
            "PART 2 — EXECUTION",
            [
                "2.1 TRANSMITTAL",
                "A. List the specification section, equipment tag, and manufacturer on each transmittal.",
                "B. Include a short statement describing any substitution or requested variance.",
                "C. Keep the submitted page order intact so citations can be checked efficiently.",
                "2.2 RECORDS",
                "A. Retain an approved copy of each cut sheet with the project closeout files.",
                "B. Replace superseded product data when a later revision changes a stated value.",
                "C. Provide a final index that points to the accepted product information.",
                "2.3 DEMONSTRATION NOTE",
                "A. This harmless boilerplate exists only to give the demo specification realistic context.",
                "B. It creates no obligation outside the fictional Asterquay Learning Workshop.",
            ],
            7,
            7,
        ),
    ]


def _cut_sheet_pages(
    manufacturer: str,
    product: str,
    system_line: str,
    termination_line: str,
) -> list[list[str]]:
    """Return a two-page fictional switchboard cut sheet."""
    return [
        _page_lines(
            f"CUT SHEET — {manufacturer}",
            product,
            [
                f"Manufacturer: {manufacturer} (fictional demonstration manufacturer)",
                f"Product family: {product}",
                "Application: indoor service distribution for a fictional training project.",
                "This cut sheet is invented for SpecGuard and does not describe a real product.",
                "ELECTRICAL DATA",
                system_line,
                "Bus arrangement: copper main bus with full neutral and ground bars.",
                "Enclosure: indoor, floor-mounted steel assembly with front service access.",
                "Main protective device: molded-case main breaker sized by the final design load.",
                "Branch positions: configurable to suit the listed feeder breaker schedule.",
                "Short-circuit rating: selected during engineered configuration for the application.",
                "Product drawings: issued after the fictional order review is complete.",
            ],
            1,
            2,
        ),
        _page_lines(
            f"CUT SHEET — {manufacturer}",
            f"{product} — Connection and Documentation Data",
            [
                "FIELD CONNECTION DATA",
                termination_line,
                "Accepted conductor material: copper conductors within the marked lug range.",
                "Installation access: front removable barriers support routine field landing work.",
                "Torque information: ship a terminal schedule with the configured assembly.",
                "NAMEPLATES AND RECORDS",
                "Provide equipment identification and breaker labels with the final shipment.",
                "Provide a wiring diagram that identifies all main and feeder field connections.",
                "Provide factory routine test documentation for the completed assembly.",
                "DEMONSTRATION NOTICE",
                "All names, values, and statements on this cut sheet are fictional demo content.",
                "No endorsement, certification, or product availability is implied by this fixture.",
            ],
            2,
            2,
        ),
    ]


def build_fixtures(output_directory: Path = FIXTURE_DIRECTORY) -> list[Path]:
    """Build all committed fixtures and return their paths in stable order."""
    output_directory.mkdir(parents=True, exist_ok=True)
    fixtures = [
        (
            "asterquay_learning_workshop_specification.pdf",
            _specification_pages(),
        ),
        (
            "caldra_meridian_480v_switchboard.pdf",
            _cut_sheet_pages(
                "Caldra Meridian Electric",
                "Meridian-480 Distribution Switchboard",
                "Nominal system: 480V, 3-phase, 4-wire.",
                "Field conductor terminations: 90 deg C minimum.",
            ),
        ),
        (
            "veylan_arcworks_208v_switchboard.pdf",
            _cut_sheet_pages(
                "Veylan Arcworks",
                "Arcway-208 Distribution Switchboard",
                "Nominal system: 208V, 3-phase, 4-wire.",
                "Field conductor terminations: 90 deg C minimum.",
            ),
        ),
        (
            "torven_70c_termination_switchboard.pdf",
            _cut_sheet_pages(
                "Torven Switchgear Works",
                "Torven Linea Distribution Switchboard",
                "Nominal system: 480V, 3-phase, 4-wire.",
                "Field conductor termination rating: 158 deg F.",
            ),
        ),
    ]
    paths: list[Path] = []
    for name, pages in fixtures:
        path = output_directory / name
        _write_pdf(path, pages)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for fixture_path in build_fixtures():
        print(fixture_path.name)
