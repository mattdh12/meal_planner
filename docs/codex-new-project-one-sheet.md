# Codex New Project Workflow

## 1. Open Clean
- Create a dedicated project folder
- Open Codex with that folder as the workspace root
- Initialize git early

## 2. Add Instructions
- Create `AGENTS.md` at the repo root
- Include stack, package manager, run/test/lint commands, and guardrails
- Note what Codex should avoid changing

## 3. Build the Environment
- Create the app environment first (`.venv`, `npm`, `pnpm`, etc.)
- Install dependencies
- Add `.env` files for secrets, never hardcode them
- Confirm the app starts locally

## 4. Ask Codex for an Inspection
- Start with: inspect the repo, explain the structure, and identify setup steps
- Use this before making large edits
- Ask for the safest place to begin

## 5. Choose Working Mode
- Small clear task: let Codex edit directly
- Bigger or unclear task: use plan mode first
- Ask for checkpoints when tradeoffs matter

## 6. Prompt Well
- Give the goal
- Add constraints
- Say whether Codex should explain first or just do it
- Ask for verification at the end

Prompt pattern:
Goal -> Constraints -> Approach -> Verification

## 7. Use Skills Only When They Fit
- Use a skill when the task clearly matches a specialized workflow
- Do not force skills for normal coding tasks
- If you need OpenAI product guidance, use the relevant docs skill

## 8. Work in Small Loops
- Make one focused change at a time
- Run the narrowest useful test, lint, or build step
- Review results before stacking more changes
- Commit small milestones

## 9. Good First Prompts
- "Inspect this repo and get the dev environment working."
- "Use plan mode and propose a small plan for [feature]."
- "Make the change, run relevant verification, and summarize what changed."

## 10. Default Workflow
1. Create folder
2. Open in Codex
3. Init git
4. Add `AGENTS.md`
5. Create environment
6. Install deps
7. Run app
8. Ask Codex to inspect
9. Use plan mode for the first real feature
10. Commit small clean steps
