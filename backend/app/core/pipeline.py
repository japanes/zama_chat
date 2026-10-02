"""The turn pipeline used by run_turn(). Stage 0: the simple one (history → reply)."""

from app.core.simple_pipeline import simple_pipeline

full_pipeline = simple_pipeline
