"""FastAPI backend for the stage 7 UI -- wraps the existing pipeline
(gmail, classify, drafting, digest, storage) as REST endpoints. No
pipeline logic lives here; this package only orchestrates calls into
those modules and shapes the results as JSON."""
