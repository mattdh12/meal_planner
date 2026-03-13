# Meal Planner

This project is a personal meal planning app built in Python.

## Current Workflow
- Use notebooks in `notebooks/` for exploration and early experiments.
- Move reusable Python code into `src/`.
- Keep local data files in `data/`.

## Project Folders
- `notebooks/`: Jupyter notebooks for planning, prototyping, and analysis
- `src/`: reusable Python code that may later power an app
- `data/`: local data files used during development
- `docs/`: reference notes and project documents
- `scripts/`: helper scripts for local project tasks

## Environment
Use the local virtual environment for all project work.

`cmd.exe` activation:

```bat
.venv\Scripts\activate.bat
```

Install dependencies:

```bat
pip install -r requirements.txt
```

Run the web app:

```bat
python run_app.py
```

Run tests:

```bat
python -m pytest
```

Start Jupyter Lab:

```bat
.venv\Scripts\jupyter-lab.exe
```
