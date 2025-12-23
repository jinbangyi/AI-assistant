# Temporal Runtime Heartbeating Configuration

## About the Warning

You may see this warning in worker logs:
```
WARN temporalio_sdk_core::worker::heartbeat: Worker heartbeating configured for runtime, but server version does not support it.
```

## What This Means

This warning is **harmless** and does not affect workflow functionality. It indicates that:

1. **Activity heartbeating** (used in your workflows) works perfectly fine
2. **Runtime heartbeating** (worker-level health monitoring) is not supported by the current server version or configuration

## Two Types of Heartbeating

### Activity Heartbeating ✅ (Works)
- Used within activities to report progress
- Fully supported in all Temporal versions
- Used in `subscribe_and_persist_trades` activity
- Allows Temporal to detect if an activity is still alive

### Runtime Heartbeating ⚠️ (Optional)
- Worker-level feature for overall worker health
- Introduced in Temporal Server 1.24.0+
- Optional feature for advanced monitoring
- Not required for basic workflow functionality

## Current Configuration

The server is configured with:
- **Image**: `temporalio/auto-setup:1.29.0`
- **Server Version**: 1.29.0 (supports runtime heartbeating)
- **Status**: Runtime heartbeating may not be explicitly enabled in auto-setup mode

## Solutions

### Option 1: Ignore the Warning (Recommended)
The warning is harmless. Your workflows will work correctly with activity heartbeating.

### Option 2: Use a Custom Server Configuration
If you want to explicitly enable runtime heartbeating, you can:

1. Create a custom Temporal server configuration file
2. Mount it as a volume in docker-compose
3. Configure runtime heartbeating settings

However, this requires more complex setup and is typically not necessary.

### Option 3: Update to Latest Server Version
Try using the latest server version:
```yaml
image: temporalio/auto-setup:latest
```

Then restart the server:
```bash
docker-compose -f .devcontainer/temporal.docker-compose.yaml restart temporal-server
```

## Verification

To verify your workflows are working correctly despite the warning:

1. Check that activities complete successfully
2. Verify trades are being persisted to the database
3. Monitor workflow execution in Temporal UI (http://localhost:8088)

The warning does not indicate any functional issues.
