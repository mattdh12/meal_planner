from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
SEED_DATA_PATH = DATA_DIR / "seed_data.json"
DATABASE_PATH = DATA_DIR / "meal_planner.db"
DEFAULT_STORE = "Wegmans"
