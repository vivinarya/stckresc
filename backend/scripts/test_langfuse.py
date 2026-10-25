from pathlib import Path
from dotenv import load_dotenv
from langfuse import get_client


env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(env_path)

# Initialize the Langfuse client
langfuse = get_client()

try:
    if not langfuse.auth_check():
        raise SystemExit(
            "Langfuse authentication failed. Check your API keys and region URL."
        )

    with langfuse.start_as_current_observation(
        as_type="span",
        name="stockrescue-setup-test",
    ) as span:
        span.update(
            input={
                "project": "StockRescue",
                "check": "Langfuse connectivity",
            },
            output={
                "status": "connected",
            },
        )
    langfuse.flush()

    print("Langfuse authentication successful!")
    print("Test observation submitted.")
    print("Open your Langfuse project and check the Traces view.")

except Exception as error:
    print(f"Langfuse test failed: {error}")
    raise
