"""Central configuration, read from environment variables."""
import os

PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
TAX_RATE = float(os.getenv("TAX_RATE", "0.18"))
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "5"))
