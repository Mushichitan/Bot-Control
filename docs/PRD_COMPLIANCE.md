# PRD compliance checklist

| Requirement | Implemented | Tested | Status |
| --- | --- | --- | --- |
| Desktop control center shell + dark dashboard | Yes | UI workflows | Done |
| Complete project/folder import, structure preserved | Yes | test_project_import_multi_file | Done |
| Static analysis, no execute during analyze | Yes | test_analysis_does_not_execute | Done |
| Entry-point detection and user choice | Yes | test_entry_point_detection, test_entry_point_choice | Done |
| Dependency detection (pyproject / requirements) | Yes | test_dependency_detection | Done |
| Isolated per-bot environment | Yes | import + venv helpers | Done |
| Secure env vault, mask, import, delete | Yes | test_secret_masking, test_env_set_and_mask, test_env_import_and_delete | Done |
| Secrets redacted in logs/export | Yes | test_secret_redaction_in_logs | Done |
| Paper / Testnet / Live, no silent switch | Yes | update_bot + UI | Done |
| LIVE explicit confirmation | Yes | test_live_confirmation_required | Done |
| No withdrawal feature; warning if detected | Yes | test_no_withdrawal_warning | Done |
| Start / stop / restart with process verify | Yes | test_start_stop_restart | Done |
| Pause only if detected | Yes | test_pause_hidden_unless_supported | Done |
| Crash detection + bounded auto-restart | Yes | test_crash_detection | Done |
| Generic event adapter (CBC_EVENT + heuristics) | Yes | test_parse_structured_and_heuristic, ingestion tests | Done |
| Signals screen | Yes | test_signal_and_position_ingestion | Done |
| Open/closed positions, stable numbering | Yes | test_position_numbering | Done |
| TP/SL remain after close | Yes | test_tp_sl_remain_in_history | Done |
| Realized vs unrealized P&L separated | Yes | test_realized_vs_unrealized | Done |
| Hourly + 4-hour reports | Yes | test_hourly_and_four_hour_reports | Done |
| Telegram command detection / activity mirror | Yes | test_telegram_command_detection | Done |
| Independent health states | Yes | test_health_monitoring | Done |
| SQLite persistence across restart | Yes | test_database_persistence | Done |
| File change detection | Yes | test_file_change_and_version_activation | Done |
| Safe version activate; no LIVE silent activate | Yes | test_no_live_activate_while_running | Done |
| Rollback to known-good | Yes | test_rollback | Done |
| Audit trail without secrets | Yes | AuditLog on actions | Done |
| Backup excludes secrets by default | Yes | test_backup_excludes_secrets_by_default | Done |
| Mock bot never hits Binance/Telegram | Yes | mock_bot/ | Done |
| Docs + Windows launch script | Yes | README, docs/USER_GUIDE.md, start.bat | Done |
