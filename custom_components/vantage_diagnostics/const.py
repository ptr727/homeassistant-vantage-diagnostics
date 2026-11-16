"""Constants for the Vantage Diagnostics integration."""

import logging

LOGGER = logging.getLogger(__package__)

DOMAIN = "vantage_diagnostics"

# The stock integration this one reuses the connection of
VANTAGE_DOMAIN = "vantage"

# Stored in the config entry, the entry ID of the stock entry this one follows
CONF_VANTAGE_ENTRY_ID = "vantage_entry_id"

# Services
SERVICE_GET_TRIGGER_HISTORY = "get_trigger_history"
SERVICE_GET_SYSTEM_LOG = "get_system_log"
SERVICE_GET_MODULE_STATUS = "get_module_status"

# Why a diagnostic service or the module status read left something unread
UNLOADED = "Not read, the integration was unloaded"

# Events keep the names the fork of the stock integration fired, so automations that listen for them keep working
EVENT_TRIGGER = "vantage_trigger"
EVENT_SYSTEM_LOG = "vantage_system_log"
