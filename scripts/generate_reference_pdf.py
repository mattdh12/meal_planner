from pathlib import Path


PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 48
TOP = 744
LEADING = 15


def escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_line(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_content() -> list[tuple[str, int]]:
    lines: list[tuple[str, int]] = [
        ("Codex New Project Workflow", 22),
        ("A one-sheet checklist for starting clean and staying unblocked.", 11),
        ("", 8),
    ]

    sections = [
        (
            "1. Open Clean",
            [
                "Create a dedicated project folder and open that folder as the Codex workspace root.",
                "Initialize git early so setup and first changes are tracked from day one.",
            ],
        ),
        (
            "2. Add Instructions",
            [
                "Create AGENTS.md with the stack, package manager, key commands, and any guardrails.",
                "Tell Codex what to avoid changing and whether you want it to ask before risky edits.",
            ],
        ),
        (
            "3. Build the Environment",
            [
                "Create the local environment first: .venv, npm, pnpm, or whatever the project uses.",
                "Install dependencies, keep secrets in .env files, and confirm the app starts locally.",
            ],
        ),
        (
            "4. Ask for an Inspection",
            [
                "Start with: inspect the repo, explain the structure, and identify setup steps.",
                "Use this before larger edits so you understand where work should happen.",
            ],
        ),
        (
            "5. Pick a Working Mode",
            [
                "Small clear task: let Codex edit directly.",
                "Bigger or unclear task: use plan mode for the approach, risks, and checkpoints.",
            ],
        ),
        (
            "6. Prompt Well",
            [
                "Use: Goal + Constraints + Approach + Verification.",
                "Example: Add X, avoid Y, inspect first, then run the smallest useful test.",
            ],
        ),
        (
            "7. Use Skills When They Fit",
            [
                "Use a skill only when the task clearly matches a specialized workflow.",
                "Do not force skills for normal coding tasks.",
            ],
        ),
        (
            "8. Work in Small Loops",
            [
                "Make one focused change, run verification, review results, then continue.",
                "Commit small milestones instead of waiting for a huge batch of edits.",
            ],
        ),
        (
            "9. Good Starter Prompts",
            [
                "Inspect this repo and get the dev environment working.",
                "Use plan mode and propose a small plan for [feature].",
                "Make the change, run relevant verification, and summarize what changed.",
            ],
        ),
        (
            "10. Default Workflow",
            [
                "Create folder -> Open in Codex -> Init git -> Add AGENTS.md -> Create env",
                "Install deps -> Run app -> Ask Codex to inspect -> Use plan mode for first real feature",
            ],
        ),
    ]

    for title, bullets in sections:
        lines.append((title, 13))
        for bullet in bullets:
            wrapped = wrap_line(f"- {bullet}", 78)
            for item in wrapped:
                lines.append((item, 10))
        lines.append(("", 6))

    return lines


def build_stream(lines: list[tuple[str, int]]) -> str:
    parts = ["BT", f"/F1 22 Tf {LEFT} {TOP} Td"]
    current_font = 22
    first = True
    y_drop = 0

    for text, size in lines:
        if first:
            first = False
        else:
            y_drop = LEADING if size >= 10 else 10
            parts.append(f"0 -{y_drop} Td")
        if size != current_font:
            parts.append(f"/F1 {size} Tf")
            current_font = size
        if text:
            parts.append(f"({escape_pdf_text(text)}) Tj")
    parts.append("ET")
    return "\n".join(parts)


def write_pdf(path: Path) -> None:
    content_lines = build_content()
    stream = build_stream(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(
            "ascii"
        )
    )
    objects.append(
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"
        ).encode("ascii")
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)


if __name__ == "__main__":
    workspace = Path(__file__).resolve().parents[1]
    output = workspace / "docs" / "codex-new-project-one-sheet.pdf"
    write_pdf(output)
    print(output)
