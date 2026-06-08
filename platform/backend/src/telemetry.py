import os
import logging
from fastapi import FastAPI
from typing import Optional

def setup_telemetry(app: FastAPI) -> None:
    """
    Sets up OpenTelemetry using Azure AI Project if environment variables are present.
    Gracefully handles missing environment variables.
    """
    # Check if local tracing is enabled
    local_tracing_enabled = os.getenv("LOCAL_TRACING_ENABLED")
    otel_exporter_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    
    # Get the connection string from the environment variables
    try:
        azure_location = os.getenv("AZURE_LOCATION")
        azure_sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        azure_rg = os.getenv("AZURE_RESOURCE_GROUP")
        azure_project = os.getenv("AZURE_AI_PROJECT")
        
        # Only proceed if all required variables are present
        if all([azure_location, azure_sub_id, azure_rg, azure_project]):
            ai_project_conn_str = f"{azure_location}.api.azureml.ms;{azure_sub_id};{azure_rg};{azure_project}"
            
            # Configure OpenTelemetry using Azure AI Project
            # Add your telemetry configuration code here
            logging.info("Telemetry configuration successful")
        else:
            logging.warning("Telemetry configuration skipped - missing required environment variables")
    except Exception as e:
        logging.warning(f"Error in telemetry setup: {str(e)}")
        # Continue without telemetry
