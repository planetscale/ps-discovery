# Output Format

The tool generates reports in the specified output directory (default: `./discovery_output/`).

## Generated Files

- **`planetscale_discovery_results.json`**: Complete analysis results in JSON format
- **`discovery_summary.md`**: Local debugging summary (only when `--local-summary` flag is used)

## JSON Report Structure

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "discovery_version": "1.1.0",
  "database_results": {
    "connection_info": { ... },
    "analysis_results": {
      "config": { ... },
      "schema": { ... },
      "performance": { ... },
      "security": { ... },
      "features": { ... }
    }
  },
  "cloud_results": { ... },
  "summary": { ... }
}
```

## Markdown Summary Sections

When `--local-summary` is used, the markdown file includes:

1. **Overview**: Which modules ran and their status
2. **Database Summary**: PostgreSQL version, table count, size, extensions, users
3. **Cloud Summary**: Per-provider instance and cluster counts
4. **Information Not Collected**: Any gaps due to permissions or missing extensions
5. **Errors**: Any errors encountered during discovery
